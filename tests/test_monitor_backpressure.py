"""
Test issue #110: a monitor that stops reading must not stall publishes.

Live tests run against a uvicorn server (`MessageBusManager`) with a raw-socket
monitor that completes the handshake and never reads. Unit tests drive
`ConnectionManager` with stub sockets whose sends never return.
"""

import asyncio
import base64
import json
import os
import socket
import threading
import time
import uuid

import pytest
from starlette.websockets import WebSocketState
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

from derhost.server.connection_manager import ConnectionManager, _MonitorOutbox

PAD_32K = "x" * 32768
PAD_64K = "x" * 65536
END_TOPIC = "burst/end"


def _frame_text(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _small_rcvbuf_socket(host: str, port: int) -> socket.socket:
    """A TCP socket whose receive buffer is as small as the kernel allows."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect((host, port))
    return sock


def _open_stalled_monitor(manager, monitor_id: str) -> socket.socket:
    """Complete the monitor handshake by hand; the caller decides whether and when to read."""
    sock = _small_rcvbuf_socket(manager.host, manager.port)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        (
            f"GET /monitor/{monitor_id} HTTP/1.1\r\nHost: {manager.host}:{manager.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    sock.settimeout(5)
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        assert chunk, f"server closed during the monitor handshake: {head!r}"
        head += chunk
    assert head.startswith(b"HTTP/1.1 101"), head
    return sock


class _RawMonitor:
    """Reads monitor frames straight off a socket, so TCP-level reading is under the test's control.

    A websockets client reads eagerly on a background thread, which would hide a
    slow reader from the server.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock

    def _exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("server closed the monitor socket")
            data += chunk
        return data

    def recv(self, timeout: float) -> str:
        self.sock.settimeout(timeout)
        first, second = self._exact(2)
        assert first & 0x0F == 0x1, f"expected a text frame, got opcode {first & 0x0F}"
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._exact(8), "big")
        return self._exact(length).decode("utf-8")


def _start_burst(manager, identity: str, count: int, pad: str):
    """Publish `count` padded frames and an end marker from a worker thread."""
    publisher = ws_connect(manager.get_ws_url(identity), open_timeout=5)
    errors: list[Exception] = []

    def run():
        try:
            for i in range(count):
                publisher.send(
                    json.dumps(
                        {
                            "type": "publish",
                            "bus": "",
                            "topic": f"burst/{i}",
                            "headers": {},
                            "message": {"i": i, "pad": pad},
                        }
                    )
                )
            publisher.send(
                json.dumps({"type": "publish", "bus": "", "topic": END_TOPIC, "headers": {}, "message": "end"})
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return publisher, worker, errors


def _finish_burst(publisher, worker) -> None:
    """Close the publisher; a sender wedged in a full buffer is shut down first."""
    worker.join(timeout=10)
    if worker.is_alive():
        try:
            publisher.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        worker.join(timeout=5)
    try:
        publisher.close()
    except Exception:
        pass


def _read_topics(monitor, deadline_s: float, pause_after: int | None = None, per_frame_s: float = 0.0) -> list[str]:
    """Read topics until the end marker or the deadline; the marker is not returned."""
    deadline = time.monotonic() + deadline_s
    topics: list[str] = []
    while time.monotonic() < deadline:
        try:
            raw = monitor.recv(timeout=max(0.1, deadline - time.monotonic()))
        except TimeoutError:
            break
        topic = json.loads(raw)["topic"]
        if topic == END_TOPIC:
            break
        topics.append(topic)
        if pause_after is not None and len(topics) == pause_after:
            time.sleep(3)
        elif per_frame_s:
            time.sleep(per_frame_s)
    return topics


def _connect_with_retry(url: str, seconds: float):
    """Return (socket, None) once admitted, or (None, last_status) after `seconds`."""
    deadline = time.monotonic() + seconds
    status = None
    while time.monotonic() < deadline:
        try:
            return ws_connect(url, open_timeout=2), None
        except InvalidStatus as exc:
            status = exc.response.status_code
            time.sleep(0.2)
    return None, status


class TestStalledMonitorDoesNotStallPublishers:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        yield

    def test_healthy_monitor_receives_the_whole_burst_in_order(self):
        self.manager.start_bus()
        stalled = _open_stalled_monitor(self.manager, f"stalled-{uuid.uuid4().hex[:8]}")
        healthy = ws_connect(self.manager.get_monitor_ws_url(f"healthy-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        publisher, worker, _ = _start_burst(self.manager, f"pub-{uuid.uuid4().hex[:8]}", 200, PAD_32K)
        try:
            topics = _read_topics(healthy, deadline_s=10)
        finally:
            _finish_burst(publisher, worker)
            healthy.close()
            stalled.close()
        assert topics == [f"burst/{i}" for i in range(200)]

    def test_publisher_can_reconnect_under_its_identity_after_the_burst(self):
        self.manager.start_bus()
        stalled = _open_stalled_monitor(self.manager, f"stalled-{uuid.uuid4().hex[:8]}")
        identity = f"pub-{uuid.uuid4().hex[:8]}"
        publisher, worker, _ = _start_burst(self.manager, identity, 200, PAD_32K)
        reconnected = None
        try:
            _finish_burst(publisher, worker)
            reconnected, status = _connect_with_retry(self.manager.get_ws_url(identity), 5)
            assert reconnected is not None, f"reconnect still refused after 5s (last status {status})"
        finally:
            if reconnected is not None:
                reconnected.close()
            stalled.close()

    def test_stalled_monitor_is_dropped_and_its_id_reusable(self):
        self.manager.start_bus(monitor_send_timeout=1.0)
        bus_manager = self.manager.bus.manager
        stalled_id = f"stalled-{uuid.uuid4().hex[:8]}"
        stalled = _open_stalled_monitor(self.manager, stalled_id)
        healthy_id = f"healthy-{uuid.uuid4().hex[:8]}"
        healthy = ws_connect(self.manager.get_monitor_ws_url(healthy_id), open_timeout=5)
        publisher, worker, _ = _start_burst(self.manager, f"pub-{uuid.uuid4().hex[:8]}", 200, PAD_32K)
        reused = None
        try:
            _read_topics(healthy, deadline_s=10)
            deadline = time.monotonic() + 1.0 + 2
            while stalled_id in bus_manager.monitor_connections and time.monotonic() < deadline:
                time.sleep(0.1)
            assert stalled_id not in bus_manager.monitor_connections
            assert healthy_id in bus_manager.monitor_connections

            reused = ws_connect(self.manager.get_monitor_ws_url(stalled_id), open_timeout=5)
            assert bus_manager.monitor_connections[stalled_id] is not None

            with pytest.raises(InvalidStatus) as excinfo:
                ws_connect(self.manager.get_monitor_ws_url(healthy_id), open_timeout=5)
            assert excinfo.value.response.status_code == 409
        finally:
            _finish_burst(publisher, worker)
            if reused is not None:
                reused.close()
            healthy.close()
            stalled.close()


class TestSlowButLiveMonitorKeepsEveryFrame:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_monitor_that_reads_slowly_and_pauses_receives_all_frames_in_order(self):
        monitor_id = f"slow-{uuid.uuid4().hex[:8]}"
        slow = _RawMonitor(_open_stalled_monitor(self.manager, monitor_id))
        publisher, worker, _ = _start_burst(self.manager, f"pub-{uuid.uuid4().hex[:8]}", 100, PAD_64K)
        try:
            topics = _read_topics(slow, deadline_s=40, pause_after=10, per_frame_s=0.02)
            assert topics == [f"burst/{i}" for i in range(100)]
            assert monitor_id in self.manager.bus.manager.monitor_connections
        finally:
            _finish_burst(publisher, worker)
            slow.sock.close()


class TestMonitorFrameOnTheWire:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_frame_is_compact_unescaped_json_text(self):
        monitor = ws_connect(self.manager.get_monitor_ws_url(f"wire-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        publisher = ws_connect(self.manager.get_ws_url(f"pub-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        try:
            publisher.send(
                json.dumps(
                    {
                        "type": "publish",
                        "bus": "",
                        "topic": "devices/caf\u00e9",
                        "headers": {"k": "v"},
                        "message": {"name": "caf\u00e9 \u2603", "n": [1, 2]},
                    }
                )
            )
            raw = monitor.recv(timeout=5)
        finally:
            publisher.close()
            monitor.close()
        assert isinstance(raw, str)
        parsed = json.loads(raw)
        assert list(parsed) == ["type", "timestamp", "topic", "sender", "headers", "message"]
        assert parsed["type"] == "pubsub_message"
        assert parsed["message"] == {"name": "caf\u00e9 \u2603", "n": [1, 2]}
        assert raw == _frame_text(parsed)
        assert "caf\u00e9 \u2603" in raw

    def test_outbox_is_released_when_the_monitor_closes(self):
        bus_manager = self.manager.bus.manager
        monitor_id = f"released-{uuid.uuid4().hex[:8]}"
        monitor = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
        assert monitor_id in bus_manager.monitor_outboxes
        monitor.close()
        deadline = time.monotonic() + 5
        while monitor_id in bus_manager.monitor_outboxes and time.monotonic() < deadline:
            time.sleep(0.1)
        assert monitor_id not in bus_manager.monitor_outboxes
        assert monitor_id not in bus_manager.monitor_connections


class _Stub:
    """A monitor socket for unit tests; `hang` makes every send wait forever."""

    def __init__(self, hang: bool = False):
        self.client_state = WebSocketState.CONNECTING
        self.hang = hang
        self.texts: list[str] = []
        self.jsons: list[dict] = []
        self.close_calls: list[tuple] = []

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        if self.hang:
            await asyncio.Event().wait()
        self.texts.append(text)

    async def send_json(self, data: dict) -> None:
        if self.hang:
            await asyncio.Event().wait()
        self.jsons.append(data)

    async def send_denial_response(self, response) -> None:
        raise AssertionError("unexpected denial")

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))


async def _admit(manager: ConnectionManager, stub: _Stub, monitor_id: str = "m") -> asyncio.Task:
    assert await manager.connect_monitor(stub, monitor_id) is True
    return asyncio.create_task(manager.run_monitor_sender(monitor_id, stub))


async def _wait_until(predicate, seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


class TestBroadcastNeverAwaitsAMonitor:
    def test_a_monitor_whose_send_never_returns_is_dropped_without_delaying_publishes(self):
        depth = 3

        async def scenario():
            manager = ConnectionManager(monitor_queue_depth=depth, monitor_send_timeout=30.0)
            stub = _Stub(hang=True)
            sender = await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            # The sender holds the first frame mid-send, so depth + 1 more fill
            # the queue and overflow it.
            for i in range(depth + 2):
                await asyncio.wait_for(manager._broadcast_to_monitors({"i": i}), 0.1)
            assert "m" not in manager.monitor_connections
            assert "m" not in manager.monitor_outboxes
            assert len(outbox.frames) == 0
            assert outbox.pending_bytes == 0
            assert "full" in outbox.drop_reason
            done, _ = await asyncio.wait({sender}, timeout=1)
            assert done

        asyncio.run(scenario())

    def test_a_monitor_that_keeps_up_gets_every_frame_and_is_never_dropped(self):
        async def scenario():
            manager = ConnectionManager(monitor_queue_depth=3, monitor_send_timeout=30.0)
            stub = _Stub()
            sender = await _admit(manager, stub)
            for i in range(10):
                await asyncio.wait_for(manager._broadcast_to_monitors({"i": i}), 0.1)
                await asyncio.sleep(0)
            assert await _wait_until(lambda: len(stub.texts) == 10)
            assert [json.loads(t)["i"] for t in stub.texts] == list(range(10))
            assert "m" in manager.monitor_connections
            sender.cancel()

        asyncio.run(scenario())

    def test_pending_bytes_over_the_bound_drop_the_monitor(self):
        async def scenario():
            manager = ConnectionManager(monitor_queue_depth=100, monitor_queue_bytes=100, monitor_send_timeout=30.0)
            stub = _Stub(hang=True)
            await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            frame = {"pad": "x" * 35}
            size = len(_frame_text(frame).encode())
            assert 45 == size
            # One frame in flight, two pending, and the next would exceed 100 bytes.
            for _ in range(4):
                await asyncio.wait_for(manager._broadcast_to_monitors(frame), 0.1)
            assert "m" not in manager.monitor_connections
            assert "bytes" in outbox.drop_reason

        asyncio.run(scenario())

    def test_an_unserializable_message_is_logged_and_drops_nobody(self, caplog):
        async def scenario():
            manager = ConnectionManager()
            stub = _Stub()
            sender = await _admit(manager, stub)
            await manager._broadcast_to_monitors({"bad": object()})
            await asyncio.sleep(0.05)
            assert stub.texts == []
            assert "m" in manager.monitor_connections
            sender.cancel()

        with caplog.at_level("ERROR"):
            asyncio.run(scenario())
        assert any("serialize" in r.getMessage() for r in caplog.records)


class TestSendTimeout:
    def test_a_send_that_outlasts_the_timeout_drops_the_monitor_with_no_further_publish(self):
        async def scenario():
            manager = ConnectionManager(monitor_send_timeout=0.2)
            stub = _Stub(hang=True)
            sender = await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            await asyncio.wait_for(manager._broadcast_to_monitors({"i": 0}), 1.0)
            assert "m" in manager.monitor_connections
            assert await _wait_until(lambda: "m" not in manager.monitor_connections, 1.0)
            assert "timed out" in outbox.drop_reason
            done, _ = await asyncio.wait({sender}, timeout=1)
            assert done

        asyncio.run(scenario())

    def test_a_send_inside_the_timeout_leaves_the_monitor_registered(self):
        async def scenario():
            manager = ConnectionManager(monitor_send_timeout=5.0)
            stub = _Stub(hang=True)
            sender = await _admit(manager, stub)
            await asyncio.wait_for(manager._broadcast_to_monitors({"i": 0}), 1.0)
            await asyncio.sleep(1.0)
            assert "m" in manager.monitor_connections
            sender.cancel()

        asyncio.run(scenario())


class TestStaleSocketCannotDropANewerOne:
    def test_dropping_a_replaced_socket_leaves_the_replacement_registered_and_served(self):
        async def scenario():
            manager = ConnectionManager()
            stale = _Stub()
            await _admit(manager, stale)
            stale.client_state = WebSocketState.DISCONNECTED
            fresh = _Stub()
            fresh_sender = await _admit(manager, fresh)
            fresh_outbox = manager.monitor_outboxes["m"]

            manager._release_monitor("m", stale, "stale socket timed out")

            assert manager.monitor_connections["m"] is fresh
            assert manager.monitor_outboxes["m"] is fresh_outbox
            assert fresh_outbox.drop_reason is None
            await manager._broadcast_to_monitors({"i": 1})
            assert await _wait_until(lambda: len(fresh.texts) == 1)
            fresh_sender.cancel()

        asyncio.run(scenario())


class TestFrameBytesAndOrder:
    def test_each_monitor_gets_the_compact_text_frames_in_enqueue_order(self):
        first = {"type": "pubsub_message", "topic": "a/caf\u00e9", "message": {"v": "\u2603", "n": [1, 2]}}
        second = {"type": "pubsub_message", "topic": "b", "message": "x"}

        async def scenario():
            manager = ConnectionManager()
            one, two = _Stub(), _Stub()
            sender_one = await _admit(manager, one, "one")
            sender_two = await _admit(manager, two, "two")
            await manager._broadcast_to_monitors(first)
            await manager._broadcast_to_monitors(second)
            assert await _wait_until(lambda: len(one.texts) == 2 and len(two.texts) == 2)
            expected = [_frame_text(first), _frame_text(second)]
            assert one.texts == expected
            assert two.texts == expected
            assert one.jsons == []
            sender_one.cancel()
            sender_two.cancel()

        asyncio.run(scenario())


class TestBoundsAreValidated:
    @pytest.mark.parametrize(
        "kwargs",
        [{"monitor_queue_depth": 0}, {"monitor_queue_bytes": 0}, {"monitor_send_timeout": 0}],
    )
    def test_non_positive_bound_is_refused(self, kwargs):
        with pytest.raises(ValueError):
            ConnectionManager(**kwargs)


class TestOutboxBoundsAtTheBoundary:
    def test_exactly_depth_frames_are_kept_and_one_more_is_refused(self):
        async def scenario():
            outbox = _MonitorOutbox(_Stub())
            kept = [outbox.offer("a", 1, 3, 1000) for _ in range(3)]
            assert kept == [None, None, None]
            assert len(outbox.frames) == 3
            reason = outbox.offer("a", 1, 3, 1000)
            assert reason is not None and "3 frames" in reason
            assert len(outbox.frames) == 3

        asyncio.run(scenario())

    def test_pending_bytes_equal_to_the_cap_are_kept_and_one_more_byte_is_refused(self):
        async def scenario():
            outbox = _MonitorOutbox(_Stub())
            assert outbox.offer("aaaa", 4, 100, 10) is None
            assert outbox.offer("bbbbbb", 6, 100, 10) is None
            assert outbox.pending_bytes == 10
            reason = outbox.offer("c", 1, 100, 10)
            assert reason is not None and "10" in reason
            assert outbox.pending_bytes == 10

        asyncio.run(scenario())
