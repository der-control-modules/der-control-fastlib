"""
Test issue #80: a refused duplicate connection must not evict the live agent.

`test_single_agent_per_identity.py` only checks that the first agent's
`.connected` flag stays true. That flag is a client-side echo of the socket
state and says nothing about the server's bookkeeping for the identity, so it
missed that a refused duplicate was tearing down the live agent's server-side
registration (der-control-modules/der-control-fastlib#80). These tests assert
the server's own state (`ConnectionManager`) directly, and prove delivery
still works by routing a real RPC through it.
"""

import gevent
import pytest

from derhost.client.agent import Agent


class TestDuplicateConnectionDoesNotEvictLiveAgent:
    """Issue #80: reject the duplicate, never the identity's live state."""

    @pytest.fixture(autouse=True)
    def setup(self, message_bus_manager_fixture):
        """Set up test with message bus."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        yield

    def test_refused_duplicate_preserves_state_and_rpc_delivery(self):
        identity = "duplicate-test-agent"
        connection_manager = self.manager.bus.manager

        agent1 = self.manager.create_agent(identity)
        agent1.vip.rpc.export_method("ping", lambda value: f"pong:{value}")
        agent1.connect()
        gevent.sleep(1)
        assert agent1.connected, "First agent should be connected"

        caller = self.manager.create_connected_agent("duplicate-test-caller")
        gevent.sleep(0.5)

        live_socket = connection_manager.active_connections[identity]
        registered_methods = connection_manager.agent_rpc_methods[identity]
        assert {"name": "ping", "params": [{"name": "value"}]} in registered_methods
        assert identity in connection_manager.prefix_subscriptions

        # A second connection under the same identity must be refused, not
        # allowed to replace the CONNECTED one (VOLTTRON compatibility). The
        # refused socket is closed before being accepted, so the client's
        # own `connect()` never sees `connected` become true and times out.
        agent2 = Agent(
            identity=identity, host=self.manager.host, port=self.manager.port
        )
        with pytest.raises(ConnectionError):
            agent2.connect()
        gevent.sleep(1)

        # The refusal must not have touched the live agent's entry, its
        # subscriptions, or its registered RPC methods.
        assert connection_manager.active_connections[identity] is live_socket
        assert connection_manager.agent_rpc_methods[identity] == registered_methods
        assert identity in connection_manager.prefix_subscriptions
        assert agent1.connected, "First agent should remain connected"

        # And the live agent must still be reachable: an RPC naming it as the
        # peer must be delivered and answered, not lost.
        result = caller.vip.rpc.call(identity, "ping", "x")
        response = result.get(timeout=5)
        assert response == "pong:x"

        agent1.disconnect()
        caller.disconnect()
        # Unconditional: stops agent2's own reconnect attempts even though
        # it never reached `connected` (the refusal is exactly the point).
        agent2.disconnect()

    def test_genuine_disconnect_still_cleans_up_and_allows_reconnect(self):
        identity = "reconnect-test-agent"
        connection_manager = self.manager.bus.manager

        agent1 = self.manager.create_connected_agent(identity)
        gevent.sleep(1)
        assert identity in connection_manager.active_connections

        agent1.disconnect()
        gevent.sleep(1)
        assert identity not in connection_manager.active_connections
        assert identity not in connection_manager.agent_rpc_methods
        assert identity not in connection_manager.prefix_subscriptions

        # A restarted agent under the same identity must be accepted, not
        # refused as though the old (now-gone) connection were still live.
        agent2 = self.manager.create_connected_agent(identity)
        gevent.sleep(1)
        assert identity in connection_manager.active_connections

        agent2.disconnect()
