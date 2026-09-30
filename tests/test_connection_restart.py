"""
Test issue #83: a restarting agent can be refused for 30 s or more.

The refused-duplicate case that keeps a live agent's state is the #80
regression test in `test_duplicate_connection_state.py`, not repeated here.

These tests run against a live uvicorn server (`MessageBusManager`, the same
route `test_duplicate_connection_state.py` uses for #80), not Starlette's
TestClient, because TestClient bypasses uvicorn's own keepalive ping/pong and
its pre-accept HTTP reject path.
"""

import ast
import inspect
import json
import time

import gevent
import pytest
import websocket
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

import derhost.server.fastapi_message_bus as fastapi_message_bus_module
from derhost.client.agent import Agent


def _uvicorn_call_kwargs(dotted_name: str) -> list[dict[str, ast.expr]]:
    """Keyword args of every `dotted_name(...)` call in fastapi_message_bus.py.

    Reads the source rather than running it: the second call site
    (`uvicorn.run` in `_main()`) only executes from the CLI entry point, not
    under pytest.
    """
    source_path = inspect.getsourcefile(fastapi_message_bus_module)
    tree = ast.parse(open(source_path).read())
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and f"{node.func.value.id}.{node.func.attr}" == dotted_name
        ):
            calls.append({kw.arg: kw.value for kw in node.keywords})
    return calls


class TestRestartReconnectsPromptly:
    """A restart must not inherit the peer's RPC-wait lockout."""

    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_restart_reconnects_within_5s_while_peer_never_answers(self):
        identity = "restart-lockout-agent"
        peer_id = "silent-rpc-peer"
        connection_manager = self.manager.bus.manager

        # A raw socket, not an Agent: it must never send an rpc_response, so
        # the server's handle_rpc(30.0) wait for `identity` never resolves.
        silent_peer = websocket.create_connection(self.manager.get_ws_url(peer_id), timeout=5)
        agent1 = None
        agent2 = None
        try:
            agent1 = self.manager.create_connected_agent(identity, auto_reconnect=False)
            gevent.sleep(0.5)
            assert peer_id in connection_manager.active_connections

            agent1.vip.rpc.call(peer_id, "never_answers")
            gevent.sleep(0.5)  # let the server start blocking in handle_rpc
            assert connection_manager.rpc_responses, (
                "expected the RPC to be registered as pending before simulating the restart"
            )

            # Simulate the old process dying: TCP closes, nothing tells the
            # server's blocked receive loop for `identity`.
            agent1.websocket.close()
            gevent.sleep(0.2)

            agent2 = Agent(identity=identity, host=self.manager.host, port=self.manager.port)
            started = time.monotonic()
            try:
                agent2.connect()
            except ConnectionError as e:
                elapsed = time.monotonic() - started
                pytest.fail(
                    f"restart refused for >{elapsed:.1f}s (must be under 5s): {e}. "
                    "Cause: the receive loop for the old socket is blocked "
                    "inside handle_rpc's 30s wait, so it never reads the TCP "
                    "close and keeps reporting CONNECTED (#83)."
                )
            assert agent2.connected
        finally:
            # Resolve the RPC the server is still waiting on ourselves,
            # through the peer's own connection (the normal message path,
            # handled on the server's event loop), instead of waiting out
            # handle_rpc's 30s timeout. Otherwise the old task and its
            # server thread outlive this test and can corrupt the next
            # test's event loop.
            pending_msg_ids = list(connection_manager.rpc_responses)
            for msg_id in pending_msg_ids:
                silent_peer.send(
                    json.dumps({"type": "rpc_response", "msg_id": msg_id, "result": "test-cleanup"})
                )
            if pending_msg_ids:
                gevent.sleep(0.3)
            silent_peer.close()
            if agent2 is not None:
                agent2.disconnect()
            if agent1 is not None:
                try:
                    agent1.disconnect()
                except Exception:  # noqa: BLE001 - socket was already closed above
                    pass


class TestKeepaliveConfiguredNotDefaulted:
    """Uvicorn's ping/pong keepalive is set explicitly, not left at 20/20."""

    def test_both_uvicorn_entry_points_set_ws_ping_defaults(self):
        for label in ("uvicorn.Config", "uvicorn.run"):
            calls = _uvicorn_call_kwargs(label)
            assert len(calls) == 1, f"expected exactly one {label}(...) call, found {len(calls)}"
            kwargs = calls[0]
            missing = [n for n in ("ws_ping_interval", "ws_ping_timeout") if n not in kwargs]
            assert not missing, (
                f"{label}(...) at fastapi_message_bus.py has no {missing}. "
                "Cause (#83): neither call site sets it, so uvicorn's "
                "own defaults (20.0/20.0) apply."
            )
            for name in ("ws_ping_interval", "ws_ping_timeout"):
                value_node = kwargs[name]
                if isinstance(value_node, ast.Constant):
                    assert value_node.value == 10, (
                        f"{label}(...) passes {name}={value_node.value}, expected 10"
                    )

    def test_constructor_defaults_for_ws_ping_are_10(self):
        """The AST check above only reads the call sites; this pins the
        FastAPIMessageBus constructor defaults the call sites forward."""
        sig = inspect.signature(fastapi_message_bus_module.FastAPIMessageBus.__init__)
        assert sig.parameters["ws_ping_interval"].default == 10
        assert sig.parameters["ws_ping_timeout"].default == 10

    def test_stalled_socket_replaced_within_5s_with_fast_keepalive(self, message_bus_manager_fixture):
        """With the fixture at 1s/1s, a peer that stops reading after the
        handshake is dropped by uvicorn's own ping timeout inside one
        keepalive cycle, freeing the identity for a new connection.
        """
        manager = message_bus_manager_fixture
        manager.start_bus(ws_ping_interval=1, ws_ping_timeout=1)
        identity = "stalled-keepalive-agent"

        stalled = websocket.create_connection(manager.get_ws_url(identity), timeout=5)
        try:
            deadline = time.monotonic() + 5
            replaced = False
            last_error = None
            while time.monotonic() < deadline and not replaced:
                try:
                    fresh = websocket.create_connection(manager.get_ws_url(identity), timeout=1)
                    fresh.close()
                    replaced = True
                except Exception as e:  # noqa: BLE001 - polling for the identity to free up
                    last_error = e
                    gevent.sleep(0.2)
            assert replaced, (
                f"identity still refused after 5s of a stalled peer (last: {last_error}). "
                "Expected: with the fixture at 1s/1s the stalled peer should be "
                "failed by uvicorn's own keepalive (#83) within one cycle."
            )
        finally:
            try:
                stalled.close()
            except Exception:  # noqa: BLE001 - socket may already be dead
                pass


class TestDuplicateGetsNamedRefusal:
    """A live duplicate gets HTTP 409 naming the identity, not a bare close."""

    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_duplicate_of_live_identity_gets_409_with_identity_in_body(self):
        identity = "named-refusal-agent"
        agent1 = self.manager.create_connected_agent(identity)
        gevent.sleep(0.5)

        try:
            with pytest.raises(InvalidStatus) as excinfo:
                ws_connect(self.manager.get_ws_url(identity), open_timeout=5)
            response = excinfo.value.response
            assert response.status_code == 409, (
                f"got HTTP {response.status_code}, body {bytes(response.body)!r}. "
                "Cause (#83): a pre-accept close today becomes a 403 with "
                "an empty body; the reason never reaches the wire."
            )
            assert identity.encode() in bytes(response.body)
        finally:
            agent1.disconnect()


class TestConcurrentConnectsToOneFreeIdentity:
    """Reservation must not admit two winners under contention."""

    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_exactly_one_accepted_per_round_across_30_rounds(self):
        total_rounds = 30
        concurrency = 20
        bad_rounds = []

        for round_number in range(total_rounds):
            identity = f"race-{round_number}"
            url = self.manager.get_ws_url(identity)
            outcomes = []

            def attempt(url=url, outcomes=outcomes):
                try:
                    sock = websocket.create_connection(url, timeout=5)
                    outcomes.append(sock)
                except Exception:  # noqa: BLE001 - refusal is the expected outcome for 19 of 20
                    outcomes.append(None)

            greenlets = [gevent.spawn(attempt) for _ in range(concurrency)]
            gevent.joinall(greenlets, timeout=10)
            accepted = [s for s in outcomes if s is not None]
            if len(accepted) != 1:
                bad_rounds.append((round_number, len(accepted)))
            for sock in accepted:
                sock.close()
            gevent.sleep(0.05)

        assert not bad_rounds, (
            f"rounds with != 1 accepted: {bad_rounds}. This control "
            "rules out a reservation-vs-accept race; it must stay green while the "
            "identity is reserved before any await."
        )


class TestIdleAgentSurvivesFastKeepalive:
    """Keepalive must never evict a live, answering client."""

    def test_idle_agent_stays_registered_across_5_keepalive_cycles(self, message_bus_manager_fixture):
        manager = message_bus_manager_fixture
        manager.start_bus(ws_ping_interval=1, ws_ping_timeout=1)
        identity = "idle-survivor-agent"
        connection_manager = manager.bus.manager

        agent = manager.create_connected_agent(identity)
        try:
            # 5 cycles of (ping interval + pong wait), plus margin.
            gevent.sleep(5 * (1 + 1) + 2)
            assert identity in connection_manager.active_connections
            assert agent.connected
        finally:
            agent.disconnect()
