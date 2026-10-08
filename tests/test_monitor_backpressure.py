"""
Test issue #110: a monitor that stops reading must not stall publishes.

Live tests run against a uvicorn server (`MessageBusManager`) with a raw-socket
monitor that completes the handshake and never reads. Unit tests drive
`ConnectionManager` with stub sockets whose sends never return.
"""

import asyncio
import base64
import json
import logging
import os
import socket
import threading
import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect as ws_connect

from derhost.server.connection_manager import ConnectionManager, _MonitorOutbox
from derhost.server.fastapi_message_bus import FastAPIMessageBus

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


def _masked_text_frame(text: str) -> bytes:
    """A client text frame with an all-zero mask key, so the payload bytes are the text itself."""
    payload = text.encode("utf-8")
    if len(payload) < 126:
        head = bytes([0x81, 0x80 | len(payload)])
    elif len(payload) < 65536:
        head = bytes([0x81, 0x80 | 126]) + len(payload).to_bytes(2, "big")
    else:
        head = bytes([0x81, 0x80 | 127]) + len(payload).to_bytes(8, "big")
    return head + b"\x00\x00\x00\x00" + payload


def _start_batched_burst(manager, identity: str, count: int, pad: str):
    """Like `_start_burst`, but writes every frame in one `sendall`, so the server reads many at once."""
    publisher = ws_connect(manager.get_ws_url(identity), open_timeout=5)
    errors: list[Exception] = []
    topics = [f"burst/{i}" for i in range(count)] + [END_TOPIC]
    frames = b"".join(
        _masked_text_frame(
            json.dumps(
                {
                    "type": "publish",
                    "bus": "",
                    "topic": topic,
                    "headers": {},
                    "message": {"i": i, "pad": pad},
                }
            )
        )
        for i, topic in enumerate(topics)
    )

    def run():
        try:
            publisher.socket.sendall(frames)
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
        except (TimeoutError, ConnectionClosed, ConnectionError):
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
        self.manager.start_bus(monitor_max_lag=1.0)
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


class TestHealthyMonitorKeepsUpAtDefaultBounds:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def _burst_reaches_a_healthy_monitor(self, count: int, pad: str, deadline_s: float) -> None:
        monitor_id = f"healthy-{uuid.uuid4().hex[:8]}"
        healthy = ws_connect(self.manager.get_monitor_ws_url(monitor_id), open_timeout=5)
        publisher, worker, _ = _start_burst(self.manager, f"pub-{uuid.uuid4().hex[:8]}", count, pad)
        try:
            topics = _read_topics(healthy, deadline_s=deadline_s)
        finally:
            _finish_burst(publisher, worker)
            healthy.close()
        assert topics == [f"burst/{i}" for i in range(count)]

    def test_a_thousand_small_frames_and_a_marker_reach_a_monitor_that_is_reading(self):
        self._burst_reaches_a_healthy_monitor(1000, "", 20)

    def test_three_hundred_32k_frames_reach_a_monitor_that_is_reading(self):
        self._burst_reaches_a_healthy_monitor(300, PAD_32K, 30)


class TestPublisherYieldsToTheMonitorSender:
    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus(monitor_queue_depth=256)
        yield

    def test_frames_the_server_reads_in_one_go_do_not_overflow_a_monitor_that_is_reading(self):
        # One socket write holds far more frames than the 256 deep queue, so
        # the monitor survives only if its sender runs between publishes.
        healthy = ws_connect(self.manager.get_monitor_ws_url(f"healthy-{uuid.uuid4().hex[:8]}"), open_timeout=5)
        publisher, worker, _ = _start_batched_burst(self.manager, f"pub-{uuid.uuid4().hex[:8]}", 1000, "")
        try:
            topics = _read_topics(healthy, deadline_s=20)
        finally:
            _finish_burst(publisher, worker)
            healthy.close()
        assert topics == [f"burst/{i}" for i in range(1000)]


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
    """A monitor socket for unit tests.

    `hang` makes every send wait forever, `send_delay` makes each take that
    long, and `send_error` is raised by every send. `hang_close` and
    `hang_receive` make the endpoint's close and receive wait forever.
    """

    def __init__(
        self,
        hang: bool = False,
        send_delay: float = 0.0,
        send_error: Exception | None = None,
        hang_close: bool = False,
        hang_receive: bool = False,
    ):
        self.client_state = WebSocketState.CONNECTING
        self.hang = hang
        self.send_delay = send_delay
        self.send_error = send_error
        self.hang_close = hang_close
        self.hang_receive = hang_receive
        self.texts: list[str] = []
        self.jsons: list[dict] = []
        self.close_calls: list[tuple] = []

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        if self.hang:
            await asyncio.Event().wait()
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.texts.append(text)

    async def receive_json(self) -> dict:
        if self.hang_receive:
            await asyncio.Event().wait()
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, data: dict) -> None:
        if self.hang:
            await asyncio.Event().wait()
        self.jsons.append(data)

    async def send_denial_response(self, response) -> None:
        raise AssertionError("unexpected denial")

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))
        if self.hang_close:
            await asyncio.Event().wait()


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
            manager = ConnectionManager(monitor_queue_depth=depth, monitor_max_lag=30.0)
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
            manager = ConnectionManager(monitor_queue_depth=3, monitor_max_lag=30.0)
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
            manager = ConnectionManager(monitor_queue_depth=100, monitor_queue_bytes=100, monitor_max_lag=30.0)
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


class TestLagDeadline:
    def test_a_send_that_outlasts_the_lag_drops_the_monitor_with_no_further_publish(self):
        async def scenario():
            manager = ConnectionManager(monitor_max_lag=0.2)
            stub = _Stub(hang=True)
            sender = await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            await asyncio.wait_for(manager._broadcast_to_monitors({"i": 0}), 1.0)
            assert "m" in manager.monitor_connections
            assert await _wait_until(lambda: "m" not in manager.monitor_connections, 1.0)
            assert "behind" in outbox.drop_reason
            done, _ = await asyncio.wait({sender}, timeout=1)
            assert done

        asyncio.run(scenario())

    def test_a_send_inside_the_lag_leaves_the_monitor_registered(self):
        async def scenario():
            manager = ConnectionManager(monitor_max_lag=5.0)
            stub = _Stub(hang=True)
            sender = await _admit(manager, stub)
            await asyncio.wait_for(manager._broadcast_to_monitors({"i": 0}), 1.0)
            await asyncio.sleep(1.0)
            assert "m" in manager.monitor_connections
            sender.cancel()

        asyncio.run(scenario())

    def test_lag_counts_from_enqueue_so_frames_queued_behind_a_slow_send_expire(self):
        # Each send takes 0.4 s, under the 1 s lag, but the third of four
        # frames queued together is still in flight at its 1 s deadline.
        async def scenario():
            manager = ConnectionManager(monitor_max_lag=1.0)
            stub = _Stub(send_delay=0.4)
            sender = await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            for i in range(4):
                await manager._broadcast_to_monitors({"i": i})
            assert await _wait_until(lambda: "m" not in manager.monitor_connections, 3.0)
            assert "behind" in outbox.drop_reason
            assert [json.loads(t)["i"] for t in stub.texts] == [0, 1]
            done, _ = await asyncio.wait({sender}, timeout=1)
            assert done

        asyncio.run(scenario())

    def test_the_same_slow_stub_fed_one_frame_at_a_time_stays_registered(self):
        # Three sends of 0.4 s take longer than the 1 s lag together, but none
        # waited behind another.
        async def scenario():
            manager = ConnectionManager(monitor_max_lag=1.0)
            stub = _Stub(send_delay=0.4)
            sender = await _admit(manager, stub)
            for i in range(3):
                await manager._broadcast_to_monitors({"i": i})
                assert await _wait_until(lambda delivered=i + 1: len(stub.texts) == delivered, 2.0)
            assert [json.loads(t)["i"] for t in stub.texts] == [0, 1, 2]
            assert "m" in manager.monitor_connections
            sender.cancel()

        asyncio.run(scenario())

    def test_a_frame_already_past_its_deadline_when_taken_drops_the_monitor_without_a_send(self):
        async def scenario():
            manager = ConnectionManager(monitor_max_lag=0.2)
            stub = _Stub()
            sender = await _admit(manager, stub)
            outbox = manager.monitor_outboxes["m"]
            await manager._broadcast_to_monitors({"i": 0})
            time.sleep(0.3)  # Hold the loop so the sender takes the frame late.
            done, _ = await asyncio.wait({sender}, timeout=1)
            assert done
            assert stub.texts == []
            assert "m" not in manager.monitor_connections
            assert "behind" in outbox.drop_reason

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
        [{"monitor_queue_depth": 0}, {"monitor_queue_bytes": 0}, {"monitor_max_lag": 0}],
    )
    def test_non_positive_bound_is_refused(self, kwargs):
        with pytest.raises(ValueError):
            ConnectionManager(**kwargs)


class TestOversizeFrameIsReplacedNotQueued:
    LIMIT = 1000  # a quarter of the 4000 byte cap

    @staticmethod
    def _frame(pad_length: int) -> dict:
        return {"type": "pubsub_message", "topic": "big/topic", "sender": "agent-x", "message": "x" * pad_length}

    def test_a_frame_over_the_limit_reaches_every_monitor_without_its_message_and_drops_none(self, caplog):
        original = self._frame(5000)  # Larger than the whole 4000 byte cap.
        original_size = len(_frame_text(original).encode())

        async def scenario():
            manager = ConnectionManager(monitor_queue_bytes=4000)
            one, two = _Stub(), _Stub()
            sender_one = await _admit(manager, one, "one")
            sender_two = await _admit(manager, two, "two")
            await manager._broadcast_to_monitors(original)
            follow_up = {"type": "pubsub_message", "topic": "after", "message": "ok"}
            await manager._broadcast_to_monitors(follow_up)
            assert await _wait_until(lambda: len(one.texts) == 2 and len(two.texts) == 2)
            assert set(manager.monitor_connections) == {"one", "two"}
            assert one.texts == two.texts
            replacement = json.loads(one.texts[0])
            assert replacement == {
                "type": "pubsub_message",
                "topic": "big/topic",
                "sender": "agent-x",
                "message": None,
                "omitted": {"bytes": original_size, "limit": self.LIMIT},
            }
            assert one.texts[1] == _frame_text(follow_up)
            sender_one.cancel()
            sender_two.cancel()

        with caplog.at_level(logging.WARNING):
            asyncio.run(scenario())
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        for expected in ("'big/topic'", "'agent-x'", str(original_size), str(self.LIMIT)):
            assert expected in warnings[0]

    def test_a_frame_of_exactly_the_limit_arrives_unchanged_and_one_byte_more_is_replaced(self, caplog):
        at_limit = self._frame(0)
        at_limit["message"] = "x" * (self.LIMIT - len(_frame_text(at_limit).encode()))
        assert len(_frame_text(at_limit).encode()) == self.LIMIT
        over = dict(at_limit, message=at_limit["message"] + "x")

        async def scenario():
            manager = ConnectionManager(monitor_queue_bytes=4000)
            stub = _Stub()
            sender = await _admit(manager, stub)
            await manager._broadcast_to_monitors(at_limit)
            assert await _wait_until(lambda: len(stub.texts) == 1)
            assert stub.texts[0] == _frame_text(at_limit)
            await manager._broadcast_to_monitors(over)
            assert await _wait_until(lambda: len(stub.texts) == 2)
            replaced = json.loads(stub.texts[1])
            assert replaced["message"] is None
            assert replaced["omitted"] == {"bytes": self.LIMIT + 1, "limit": self.LIMIT}
            assert "m" in manager.monitor_connections
            sender.cancel()

        with caplog.at_level(logging.WARNING):
            asyncio.run(scenario())
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


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


class TestMonitorEndpointAfterTheMonitorLeaves:
    @staticmethod
    def _endpoint(tmp_path, **bounds):
        bus = FastAPIMessageBus(config_store_dir=str(tmp_path), **bounds)
        route = next(r for r in bus.app.routes if getattr(r, "path", "") == "/monitor/{monitor_id}")
        return bus, route.endpoint

    @staticmethod
    async def _run_handler_until_a_frame_is_sent(bus, endpoint, stub):
        handler = asyncio.create_task(endpoint(stub, "m"))
        assert await _wait_until(lambda: "m" in bus.manager.monitor_outboxes)
        outbox = bus.manager.monitor_outboxes["m"]
        await bus.manager.publish("", "t", {}, "payload", "s")
        return handler, outbox

    def test_handler_returns_and_closes_once_with_1008_after_a_drop_when_receive_and_close_hang(self, tmp_path):
        bus, endpoint = self._endpoint(tmp_path, monitor_max_lag=0.1)
        stub = _Stub(hang=True, hang_close=True, hang_receive=True)

        async def scenario():
            handler, outbox = await self._run_handler_until_a_frame_is_sent(bus, endpoint, stub)
            try:
                done, _ = await asyncio.wait({handler}, timeout=1.5)
                assert done, "handler still running 1.5 s after the drop"
                assert handler.exception() is None
                assert "behind" in outbox.drop_reason
                assert stub.close_calls == [(1008, "monitor too slow")]
                assert "m" not in bus.manager.monitor_connections
            finally:
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)

        asyncio.run(scenario())

    def test_a_send_that_raises_websocket_disconnect_is_logged_as_a_disconnect_and_not_closed(self, tmp_path, caplog):
        bus, endpoint = self._endpoint(tmp_path)
        stub = _Stub(send_error=WebSocketDisconnect(code=1006), hang_receive=True)

        async def scenario():
            handler, outbox = await self._run_handler_until_a_frame_is_sent(bus, endpoint, stub)
            try:
                done, _ = await asyncio.wait({handler}, timeout=1.5)
                assert done
                assert outbox.drop_reason is None
                assert stub.close_calls == []
                assert "m" not in bus.manager.monitor_connections
            finally:
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)

        with caplog.at_level(logging.DEBUG):
            asyncio.run(scenario())
        messages = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(lvl == logging.INFO and "WebSocketDisconnect" in m and "1006" in m for lvl, m in messages)
        assert not any("Dropping message bus monitor" in m for _, m in messages)

    @pytest.mark.parametrize("error", [RuntimeError("boom"), RuntimeError()], ids=["with-text", "empty"])
    def test_a_send_that_raises_anything_else_drops_with_a_named_reason_and_closes_once(self, tmp_path, error):
        bus, endpoint = self._endpoint(tmp_path)
        stub = _Stub(send_error=error, hang_receive=True)

        async def scenario():
            handler, outbox = await self._run_handler_until_a_frame_is_sent(bus, endpoint, stub)
            try:
                done, _ = await asyncio.wait({handler}, timeout=1.5)
                assert done
                assert outbox.drop_reason.startswith("send failed: RuntimeError")
                assert str(error) in outbox.drop_reason
                assert stub.close_calls == [(1008, "monitor too slow")]
            finally:
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)

        asyncio.run(scenario())
