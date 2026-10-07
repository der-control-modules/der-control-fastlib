"""
Test issue #84: a duplicate `monitor_id` is refused, never allowed to displace
the monitor that holds it.

These tests run against a live uvicorn server (`MessageBusManager`), not
Starlette's TestClient, because TestClient bypasses the pre-accept HTTP
refusal path the 409 travels on.
"""

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.websockets import WebSocketState
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

from derhost.server.connection_manager import ConnectionManager


def _publish(manager, topic: str) -> None:
    """Publish `topic` through a real agent socket, as any client would."""
    publisher = ws_connect(manager.get_ws_url(f"publisher-{uuid.uuid4().hex[:8]}"), open_timeout=5)
    try:
        publisher.send(
            json.dumps({"type": "publish", "bus": "", "topic": topic, "headers": {}, "message": "payload"})
        )
        time.sleep(0.5)
    finally:
        publisher.close()


def _received_topic(monitor) -> str:
    return json.loads(monitor.recv(timeout=5))["topic"]


class _StubMonitorSocket:
    """Just enough of a WebSocket for the manager's monitor bookkeeping."""

    def __init__(self, state: WebSocketState):
        self.client_state = state
        self.sent: list[dict] = []
        self.denials: list[object] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def send_denial_response(self, response) -> None:
        self.denials.append(response)

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED


class _YieldingAcceptSocket(_StubMonitorSocket):
    """A socket whose accept() yields to the loop, as a real handshake does."""

    async def accept(self) -> None:
        await asyncio.sleep(0)
        self.client_state = WebSocketState.CONNECTED


class TestDuplicateMonitorIdIsRefused:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_second_socket_on_a_held_id_gets_409_and_first_keeps_receiving(self):
        monitor_id = f"held-{uuid.uuid4().hex[:8]}"
        connection_manager = self.manager.bus.manager
        first = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
        try:
            held_socket = connection_manager.monitor_connections[monitor_id]

            with pytest.raises(InvalidStatus) as excinfo:
                ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
            response = excinfo.value.response
            assert response.status_code == 409
            assert bytes(response.body) == f"monitor {monitor_id} is already connected".encode()

            assert connection_manager.monitor_connections[monitor_id] is held_socket
            assert len(connection_manager.monitor_connections) == 1

            _publish(self.manager, "devices/after-refusal")
            assert _received_topic(first) == "devices/after-refusal"
        finally:
            first.close()

    def test_twenty_concurrent_sockets_on_one_free_id_admit_exactly_one(self):
        monitor_id = f"race-{uuid.uuid4().hex[:8]}"
        url = self.manager.get_monitor_ws_url(monitor_id)

        def attempt():
            try:
                return ws_connect(url, open_timeout=10)
            except InvalidStatus as exc:
                return exc.response.status_code

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(20)))

        accepted = [o for o in outcomes if not isinstance(o, int)]
        refused = [o for o in outcomes if isinstance(o, int)]
        try:
            assert len(accepted) == 1
            assert refused == [409] * 19
            assert list(self.manager.bus.manager.monitor_connections) == [monitor_id]
        finally:
            for sock in accepted:
                sock.close()

    def test_refused_duplicate_logs_no_error(self, caplog):
        monitor_id = f"quiet-{uuid.uuid4().hex[:8]}"
        first = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
        try:
            with caplog.at_level(logging.DEBUG):
                with pytest.raises(InvalidStatus):
                    ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
                # The refused handler runs on the server thread after the 409 is sent.
                time.sleep(0.5)
            # uvicorn's own logger is out of scope: only the handler's records count.
            errors = [
                r.getMessage()
                for r in caplog.records
                if r.name.startswith("derhost") and r.levelno >= logging.ERROR
            ]
            assert errors == []
            assert any("refusing new connection" in r.getMessage() for r in caplog.records)
        finally:
            first.close()


class TestDistinctMonitorIdsAreUnaffected:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_two_distinct_ids_both_receive_one_publish(self):
        first = ws_connect(self.manager.get_monitor_ws_url(f"a-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        second = ws_connect(self.manager.get_monitor_ws_url(f"b-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        try:
            _publish(self.manager, "devices/both")
            assert _received_topic(first) == "devices/both"
            assert _received_topic(second) == "devices/both"
        finally:
            first.close()
            second.close()

    def test_a_closed_monitors_id_is_reusable_within_5s(self):
        monitor_id = f"reuse-{uuid.uuid4().hex[:8]}"
        connection_manager = self.manager.bus.manager
        first = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
        first.close()

        deadline = time.monotonic() + 5
        reopened = None
        last_error = None
        while time.monotonic() < deadline and reopened is None:
            try:
                reopened = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=2)
            except InvalidStatus as exc:
                last_error = exc.response.status_code
                time.sleep(0.2)
        assert reopened is not None, f"id still refused after 5s (last status {last_error})"
        try:
            assert monitor_id in connection_manager.monitor_connections
            _publish(self.manager, "devices/reused")
            assert _received_topic(reopened) == "devices/reused"
        finally:
            reopened.close()


class TestMonitorReservationBookkeeping:
    """Unit level: the reservation survives a broadcast and a failed accept."""

    def test_broadcast_does_not_unregister_a_monitor_still_connecting(self):
        manager = ConnectionManager()
        connecting = _StubMonitorSocket(WebSocketState.CONNECTING)
        manager.monitor_connections["pending"] = connecting

        asyncio.run(manager._broadcast_to_monitors({"type": "pubsub_message", "topic": "t"}))

        assert manager.monitor_connections["pending"] is connecting
        assert connecting.sent == []

    def test_broadcast_unregisters_a_disconnected_monitor(self):
        manager = ConnectionManager()
        manager.monitor_connections["gone"] = _StubMonitorSocket(WebSocketState.DISCONNECTED)

        asyncio.run(manager._broadcast_to_monitors({"type": "pubsub_message", "topic": "t"}))

        assert "gone" not in manager.monitor_connections

    def test_failed_accept_releases_the_id(self):
        manager = ConnectionManager()

        class FailingAccept(_StubMonitorSocket):
            async def accept(self):
                raise RuntimeError("accept failed")

        failing = FailingAccept(WebSocketState.CONNECTING)
        with pytest.raises(RuntimeError):
            asyncio.run(manager.connect_monitor(failing, "m"))
        assert "m" not in manager.monitor_connections


class TestMonitorReservationOrder:
    """Unit level: the reservation precedes the accept that yields."""

    def test_two_concurrent_connects_on_one_id_admit_exactly_one(self):
        manager = ConnectionManager()
        first = _YieldingAcceptSocket(WebSocketState.CONNECTING)
        second = _YieldingAcceptSocket(WebSocketState.CONNECTING)

        async def race():
            return await asyncio.gather(
                manager.connect_monitor(first, "m"), manager.connect_monitor(second, "m")
            )

        results = asyncio.run(race())

        assert sorted(results) == [False, True]
        assert len(first.denials) + len(second.denials) == 1
        winner, loser = (first, second) if results[0] else (second, first)
        assert manager.monitor_connections["m"] is winner
        assert len(loser.denials) == 1
        assert loser.denials[0].status_code == 409
        assert loser.denials[0].body == b"monitor m is already connected"

    def test_a_disconnected_holder_is_replaced(self):
        manager = ConnectionManager()
        stale = _StubMonitorSocket(WebSocketState.DISCONNECTED)
        fresh = _StubMonitorSocket(WebSocketState.CONNECTING)
        manager.monitor_connections["m"] = stale

        admitted = asyncio.run(manager.connect_monitor(fresh, "m"))

        assert admitted is True
        assert manager.monitor_connections["m"] is fresh
        assert fresh.denials == []

    def test_a_cancelled_accept_releases_the_id(self):
        manager = ConnectionManager()

        class CancelledAccept(_StubMonitorSocket):
            async def accept(self):
                raise asyncio.CancelledError

        async def attempt():
            try:
                await manager.connect_monitor(CancelledAccept(WebSocketState.CONNECTING), "m")
            except asyncio.CancelledError:
                return "cancelled"
            return "returned"

        assert asyncio.run(attempt()) == "cancelled"
        assert "m" not in manager.monitor_connections

    def test_broadcast_survives_a_monitor_reserved_mid_send(self):
        manager = ConnectionManager()
        late = _StubMonitorSocket(WebSocketState.CONNECTING)

        class ReservingMonitor(_StubMonitorSocket):
            async def send_json(self, data: dict) -> None:
                await super().send_json(data)
                manager.monitor_connections["late"] = late

        reserving = ReservingMonitor(WebSocketState.CONNECTED)
        manager.monitor_connections["reserving"] = reserving

        asyncio.run(manager._broadcast_to_monitors({"type": "pubsub_message", "topic": "t"}))

        assert reserving.sent == [{"type": "pubsub_message", "topic": "t"}]
        assert manager.monitor_connections["late"] is late
        assert late.sent == []
