"""
Tests for issue #83: a per-connection RPC cap, a failed or stalled forward
that answers the caller, and replies routed to the caller's own socket
rather than looked up by identity.

The cap and disconnect classes run against a live uvicorn server, the same
fixture test_connection_restart.py uses, because they exercise the
endpoint's own receive loop. The rest are unit tests against
ConnectionManager directly, with stubs for client_state, accept, send_json
and send_denial_response, matching the shape test_duplicate_connection_state.py
already uses for its own unit-level #80 test.
"""

import asyncio
import inspect
import json
import time
from typing import Any

import gevent
import pytest
import websocket
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

import derhost.server.fastapi_message_bus as fastapi_message_bus_module
from derhost.server.connection_manager import ConnectionManager


class _StubWebSocket:
    """WebSocket stand-in exposing only what ConnectionManager touches:
    client_state, accept, send_json and send_denial_response."""

    def __init__(self, *, client_state: WebSocketState = WebSocketState.CONNECTING):
        self.client_state = client_state
        self.sent: list[dict] = []
        self.denial_responses: list[Any] = []

    async def accept(self):
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict):
        self.sent.append(message)

    async def send_denial_response(self, response: Any):
        self.denial_responses.append(response)


class _YieldingAcceptWebSocket(_StubWebSocket):
    """accept() sets `state_while_yielded`, then yields once before
    CONNECTED, so a concurrent connect() can interleave."""

    def __init__(self, state_while_yielded: WebSocketState):
        super().__init__()
        self._state_while_yielded = state_while_yielded

    async def accept(self):
        self.client_state = self._state_while_yielded
        await asyncio.sleep(0)
        self.client_state = WebSocketState.CONNECTED


class _RaisingAcceptWebSocket(_StubWebSocket):
    """accept() reaches CONNECTED, then raises."""

    async def accept(self):
        self.client_state = WebSocketState.CONNECTED
        raise RuntimeError("accept failed after handshake started")


class _RaisingSendJsonWebSocket(_StubWebSocket):
    """send_json() always raises: a peer that vanishes mid-forward."""

    def __init__(self, exc: Exception):
        super().__init__(client_state=WebSocketState.CONNECTED)
        self._exc = exc

    async def send_json(self, message: dict):
        raise self._exc


class _StalledSendJsonWebSocket(_StubWebSocket):
    """send_json() never completes: a peer that has stopped reading."""

    def __init__(self):
        super().__init__(client_state=WebSocketState.CONNECTED)

    async def send_json(self, message: dict):
        await asyncio.Event().wait()


def _send_rpc(conn, peer_id: str, msg_id: str) -> None:
    conn.send(json.dumps({"type": "rpc", "peer": peer_id, "method": "never_answers", "msg_id": msg_id}))


def _answer_rpcs(peer_conn, msg_ids) -> None:
    for msg_id in msg_ids:
        peer_conn.send(json.dumps({"type": "rpc_response", "msg_id": msg_id, "result": "ok"}))


class TestRpcInFlightCapPerConnection:
    """An RPC beyond the per-connection cap gets a named rpc_error and
    creates no pending future; separately pins the constructor default."""

    def test_fifth_rpc_over_cap_of_four_gets_named_error(self, message_bus_manager_fixture):
        manager = message_bus_manager_fixture
        manager.start_bus(max_rpcs_in_flight=4)
        connection_manager = manager.bus.manager
        peer_id = "cap-silent-peer"

        silent_peer = websocket.create_connection(manager.get_ws_url(peer_id), timeout=5)
        sender = websocket.create_connection(manager.get_ws_url("cap-sender"), timeout=5)
        try:
            gevent.sleep(0.3)
            msg_ids = [f"cap-{i}" for i in range(5)]
            for msg_id in msg_ids:
                sender.send(
                    json.dumps({"type": "rpc", "peer": peer_id, "method": "never_answers", "msg_id": msg_id})
                )
            gevent.sleep(0.3)

            reply = json.loads(sender.recv())
            assert reply == {
                "type": "rpc_error",
                "msg_id": msg_ids[4],
                "error": "too many RPCs in flight on this connection (limit 4); retry later",
            }
            assert len(connection_manager.rpc_responses) == 4
            for msg_id in msg_ids[:4]:
                assert msg_id in connection_manager.rpc_responses
        finally:
            for msg_id in list(connection_manager.rpc_responses):
                silent_peer.send(json.dumps({"type": "rpc_response", "msg_id": msg_id, "result": "cleanup"}))
            if connection_manager.rpc_responses:
                gevent.sleep(0.3)
            sender.close()
            silent_peer.close()

    def test_constructor_default_max_rpcs_in_flight_is_128(self):
        sig = inspect.signature(fastapi_message_bus_module.FastAPIMessageBus.__init__)
        assert sig.parameters["max_rpcs_in_flight"].default == 128


class TestDisconnectCancelsInFlightRpcs:
    """A caller's disconnect must not leave its RPCs pending for 30s."""

    def test_three_pending_rpcs_removed_within_5s_of_caller_disconnect(self, message_bus_manager_fixture):
        manager = message_bus_manager_fixture
        manager.start_bus()
        connection_manager = manager.bus.manager
        peer_id = "disc-silent-peer"

        silent_peer = websocket.create_connection(manager.get_ws_url(peer_id), timeout=5)
        caller = websocket.create_connection(manager.get_ws_url("disc-caller"), timeout=5)
        try:
            gevent.sleep(0.3)
            msg_ids = [f"disc-{i}" for i in range(3)]
            for msg_id in msg_ids:
                caller.send(
                    json.dumps({"type": "rpc", "peer": peer_id, "method": "never_answers", "msg_id": msg_id})
                )
            gevent.sleep(0.3)
            assert all(msg_id in connection_manager.rpc_responses for msg_id in msg_ids)

            caller.close()

            deadline = time.monotonic() + 5
            remaining = set(msg_ids)
            while time.monotonic() < deadline and remaining:
                remaining = {m for m in remaining if m in connection_manager.rpc_responses}
                if remaining:
                    gevent.sleep(0.1)
            assert not remaining, f"still pending after 5s: {remaining}"
        finally:
            for msg_id in list(connection_manager.rpc_responses):
                silent_peer.send(json.dumps({"type": "rpc_response", "msg_id": msg_id, "result": "cleanup"}))
            if connection_manager.rpc_responses:
                gevent.sleep(0.3)
            silent_peer.close()


class TestReplyGuardedByCurrentSocket:
    """A reconnect under the same identity must not receive a reply
    meant for the socket it replaced."""

    @pytest.mark.asyncio
    async def test_second_stub_receives_nothing_after_sender_is_replaced(self):
        manager = ConnectionManager()
        sender_id = "reply-sender"
        peer_id = "reply-peer"
        first_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)
        second_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)
        peer_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)

        manager.active_connections[sender_id] = first_stub
        manager.active_connections[peer_id] = peer_stub

        task = asyncio.create_task(
            manager.handle_rpc(
                sender=sender_id,
                sender_ws=first_stub,
                peer=peer_id,
                method="whatever",
                args=[],
                kwargs={},
                msg_id="reply-m1",
            )
        )
        await asyncio.sleep(0)  # let it register the future and forward to the peer

        # A reconnect under the same identity replaces the registered socket
        # before the peer answers.
        manager.active_connections[sender_id] = second_stub
        manager.set_rpc_response("reply-m1", "late-answer")
        await asyncio.wait_for(task, timeout=2)

        assert first_stub.sent == []
        assert second_stub.sent == []


class TestFailedForwardAnswersTheCaller:
    """A forward that raises, or reads a DISCONNECTED peer, answers
    the caller with a named error and clears the future."""

    @pytest.mark.asyncio
    async def test_send_json_raises(self, caplog):
        await self._run_case(
            peer_stub=_RaisingSendJsonWebSocket(WebSocketDisconnect(code=1006)),
            expect_exc_info=True,
            caplog=caplog,
        )

    @pytest.mark.asyncio
    async def test_peer_reads_disconnected(self, caplog):
        await self._run_case(
            peer_stub=_StubWebSocket(client_state=WebSocketState.DISCONNECTED),
            expect_exc_info=False,
            caplog=caplog,
        )

    @staticmethod
    async def _run_case(peer_stub, expect_exc_info: bool, caplog):
        manager = ConnectionManager()
        sender_id = "fwd-sender"
        peer_id = "fwd-peer"
        sender_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)
        manager.active_connections[sender_id] = sender_stub
        manager.active_connections[peer_id] = peer_stub

        with caplog.at_level("WARNING", logger="derhost.server.connection_manager"):
            await asyncio.wait_for(
                manager.handle_rpc(
                    sender=sender_id,
                    sender_ws=sender_stub,
                    peer=peer_id,
                    method="do_it",
                    args=[],
                    kwargs={},
                    msg_id="fwd-m1",
                ),
                timeout=2,
            )

        assert sender_stub.sent == [{"type": "rpc_error", "msg_id": "fwd-m1", "error": f"Peer {peer_id} unreachable"}]
        assert "fwd-m1" not in manager.rpc_responses
        if isinstance(peer_stub, _StubWebSocket) and peer_stub.client_state == WebSocketState.DISCONNECTED:
            assert peer_stub.sent == [], "send_json must never run against a DISCONNECTED peer"

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "fwd-m1" in warnings[0].message
        assert (warnings[0].exc_info is not None) == expect_exc_info


class TestReservationSurvivesAYieldingAccept:
    """Exactly one of two concurrent connects for one identity is
    admitted, whether accept() yields while CONNECTING or while CONNECTED."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "state_while_yielded",
        [WebSocketState.CONNECTING, WebSocketState.CONNECTED],
        ids=["connecting", "connected"],
    )
    async def test_exactly_one_of_two_concurrent_connects_is_admitted(self, state_while_yielded):
        manager = ConnectionManager()
        identity = "conc-identity"
        ws_a = _YieldingAcceptWebSocket(state_while_yielded)
        ws_b = _YieldingAcceptWebSocket(state_while_yielded)

        results = await asyncio.gather(
            manager.connect(ws_a, identity),
            manager.connect(ws_b, identity),
        )

        assert sorted(results) == [False, True]
        assert len(ws_a.denial_responses) + len(ws_b.denial_responses) == 1


class TestFailedAcceptReleasesTheReservation:
    """A failed accept must not hold the identity until restart."""

    @pytest.mark.asyncio
    async def test_failed_accept_releases_reservation_for_a_following_connect(self, caplog):
        manager = ConnectionManager()
        identity = "acc-identity"
        failing_ws = _RaisingAcceptWebSocket()

        with caplog.at_level("WARNING", logger="derhost.server.connection_manager"):
            with pytest.raises(RuntimeError):
                await manager.connect(failing_ws, identity)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert identity in warnings[0].message
        assert warnings[0].exc_info is not None

        assert identity not in manager.active_connections
        assert identity not in manager.prefix_subscriptions

        second_ws = _StubWebSocket()
        admitted = await manager.connect(second_ws, identity)
        assert admitted is True


class TestRpcTaskUnhandledErrorIsLogged:
    """The endpoint's task-done callback must log an RPC task's own uncaught
    exception, with its traceback."""

    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_reply_send_failure_is_logged_as_unhandled_rpc_task_error(self, caplog):
        connection_manager = self.manager.bus.manager
        sender_id = "task-error-sender"
        peer_id = "task-error-peer"

        peer = self.manager.create_connected_agent(peer_id)
        peer.vip.rpc.export_method("ping", lambda value: f"pong:{value}")
        sender = self.manager.create_connected_agent(sender_id, auto_reconnect=False)
        gevent.sleep(0.3)

        sender_ws = connection_manager.active_connections[sender_id]

        async def _raise_on_send(message):
            raise RuntimeError("send failed for the RPC task error test")

        # Force the reply-send to the sender to fail without touching
        # active_connections: the ownership check in
        # ConnectionManager._send_to_socket_if_current must still pass, so
        # the exception reaches the RPC task uncaught.
        sender_ws.send_json = _raise_on_send

        with caplog.at_level("ERROR", logger="derhost.server.fastapi_message_bus"):
            sender.vip.rpc.call(peer_id, "ping", "x")
            gevent.sleep(1.0)

        records = [r for r in caplog.records if "Unhandled error in RPC task" in r.message]
        assert records, f"expected the RPC task's own exception logged; caplog had: {[r.message for r in caplog.records]}"
        exc_info = records[0].exc_info
        assert exc_info is not None
        assert isinstance(exc_info[1], RuntimeError)
        assert str(exc_info[1]) == "send failed for the RPC task error test"

        peer.disconnect()


class TestCapReleasesAndIsPerConnection:
    """The in-flight cap frees a slot when an RPC resolves, and one
    connection at its cap never refuses another connection's RPCs."""

    def test_cap_releases_after_rpcs_resolve_and_other_connection_is_not_refused(self, message_bus_manager_fixture):
        manager = message_bus_manager_fixture
        manager.start_bus(max_rpcs_in_flight=2)
        connection_manager = manager.bus.manager
        peer_id = "cap-silent-peer"

        silent_peer = websocket.create_connection(manager.get_ws_url(peer_id), timeout=5)
        sender = websocket.create_connection(manager.get_ws_url("cap-sender-a"), timeout=5)
        other = websocket.create_connection(manager.get_ws_url("cap-sender-b"), timeout=5)
        try:
            gevent.sleep(0.3)
            _send_rpc(sender, peer_id, "a0")
            _send_rpc(sender, peer_id, "a1")
            _send_rpc(sender, peer_id, "a2")
            assert json.loads(sender.recv())["msg_id"] == "a2"

            # sender-a is at its cap; sender-b must still be admitted.
            _send_rpc(other, peer_id, "b0")
            gevent.sleep(0.3)
            assert "b0" in connection_manager.rpc_responses

            _answer_rpcs(silent_peer, ["a0", "a1", "b0"])
            answered = {json.loads(sender.recv())["msg_id"] for _ in range(2)}
            assert answered == {"a0", "a1"}
            assert json.loads(other.recv())["msg_id"] == "b0"
            gevent.sleep(0.3)

            _send_rpc(sender, peer_id, "a3")
            gevent.sleep(0.3)
            assert "a3" in connection_manager.rpc_responses
            _answer_rpcs(silent_peer, ["a3"])
            reply = json.loads(sender.recv())
            assert reply["type"] == "rpc_response"
            assert reply["msg_id"] == "a3"
        finally:
            _answer_rpcs(silent_peer, list(connection_manager.rpc_responses))
            if connection_manager.rpc_responses:
                gevent.sleep(0.3)
            other.close()
            sender.close()
            silent_peer.close()

    def test_one_warning_per_cap_episode(self, message_bus_manager_fixture, caplog):
        manager = message_bus_manager_fixture
        manager.start_bus(max_rpcs_in_flight=2)
        connection_manager = manager.bus.manager
        peer_id = "episode-silent-peer"

        silent_peer = websocket.create_connection(manager.get_ws_url(peer_id), timeout=5)
        sender = websocket.create_connection(manager.get_ws_url("episode-sender"), timeout=5)
        try:
            gevent.sleep(0.3)
            with caplog.at_level("DEBUG", logger="derhost.server.fastapi_message_bus"):
                for msg_id in ("e0", "e1", "e2", "e3", "e4"):
                    _send_rpc(sender, peer_id, msg_id)
                refused = [json.loads(sender.recv())["msg_id"] for _ in range(3)]
                assert refused == ["e2", "e3", "e4"]

                _answer_rpcs(silent_peer, ["e0", "e1"])
                for _ in range(2):
                    sender.recv()
                gevent.sleep(0.3)

                for msg_id in ("f0", "f1", "f2", "f3"):
                    _send_rpc(sender, peer_id, msg_id)
                refused = [json.loads(sender.recv())["msg_id"] for _ in range(2)]
                assert refused == ["f2", "f3"]

            warnings = [r for r in caplog.records if r.levelname == "WARNING" and "in-flight cap" in r.message]
            assert len(warnings) == 2
            rejections = [r for r in caplog.records if r.levelname == "DEBUG" and "in-flight cap reached" in r.message]
            assert len(rejections) == 5
        finally:
            _answer_rpcs(silent_peer, list(connection_manager.rpc_responses))
            if connection_manager.rpc_responses:
                gevent.sleep(0.3)
            sender.close()
            silent_peer.close()


class TestStalledForwardIsBoundedByTheRpcDeadline:
    """A peer that stops reading must not hold an RPC, and its cap slot,
    beyond the RPC deadline."""

    @pytest.mark.asyncio
    async def test_stalled_forward_answers_the_caller_then_a_healthy_rpc_succeeds(self):
        manager = ConnectionManager()
        manager.rpc_timeout = 0.2
        sender_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)
        manager.active_connections["stall-sender"] = sender_stub
        manager.active_connections["stall-peer"] = _StalledSendJsonWebSocket()
        manager.active_connections["healthy-peer"] = _StubWebSocket(client_state=WebSocketState.CONNECTED)

        await asyncio.wait_for(
            manager.handle_rpc("stall-sender", sender_stub, "stall-peer", "m", [], {}, "stall-1"), timeout=2
        )

        assert sender_stub.sent == [{"type": "rpc_error", "msg_id": "stall-1", "error": "Peer stall-peer unreachable"}]
        assert "stall-1" not in manager.rpc_responses

        task = asyncio.create_task(manager.handle_rpc("stall-sender", sender_stub, "healthy-peer", "m", [], {}, "stall-2"))
        await asyncio.sleep(0)
        manager.set_rpc_response("stall-2", "fine")
        await asyncio.wait_for(task, timeout=2)

        assert sender_stub.sent[-1] == {"type": "rpc_response", "msg_id": "stall-2", "result": "fine"}


class TestRpcResponseFutureOwnership:
    """A cancelled RPC releases only its own future, never a later caller's
    registered under the same msg_id."""

    @pytest.mark.asyncio
    async def test_cancelling_the_first_call_leaves_the_second_calls_future_registered(self):
        manager = ConnectionManager()
        sender_stub = _StubWebSocket(client_state=WebSocketState.CONNECTED)
        manager.active_connections["dup-sender"] = sender_stub
        manager.active_connections["dup-peer"] = _StubWebSocket(client_state=WebSocketState.CONNECTED)

        # Real sleeps rather than a bare yield: each call must be past its
        # forward and parked waiting for the answer before the next step.
        first = asyncio.create_task(manager.handle_rpc("dup-sender", sender_stub, "dup-peer", "m", [], {}, "dup"))
        await asyncio.sleep(0.05)
        first_future = manager.rpc_responses["dup"]
        second = asyncio.create_task(manager.handle_rpc("dup-sender", sender_stub, "dup-peer", "m", [], {}, "dup"))
        await asyncio.sleep(0.05)
        second_future = manager.rpc_responses["dup"]
        assert second_future is not first_future

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        assert manager.rpc_responses.get("dup") is second_future
        manager.set_rpc_response("dup", "answer")
        await asyncio.wait_for(second, timeout=2)
        assert sender_stub.sent == [{"type": "rpc_response", "msg_id": "dup", "result": "answer"}]


class TestMaxRpcsInFlightValidation:
    """The cap must be at least 1: 0 would refuse every RPC."""

    def test_zero_is_rejected_and_one_is_accepted(self, tmp_path):
        bus_class = fastapi_message_bus_module.FastAPIMessageBus

        with pytest.raises(ValueError, match="max_rpcs_in_flight must be at least 1, got 0"):
            bus_class(config_store_dir=str(tmp_path), max_rpcs_in_flight=0)

        bus = bus_class(config_store_dir=str(tmp_path), max_rpcs_in_flight=1)
        assert bus.max_rpcs_in_flight == 1
