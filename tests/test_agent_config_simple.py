"""
Test agent configuration functionality using pytest - simplified for debugging
"""

import gevent
import pytest


class TestAgentConfigSimple:
    """Test agent configuration functionality with config merging."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus_manager_fixture):
        """Set up test agents with the running message bus."""
        # Store the message bus manager reference
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create agents using the new paradigm
        self.agent = self.manager.create_connected_agent("config_test_agent")
        self.config_agent = self.manager.create_connected_agent("config_manager")

        # Wait for connections
        gevent.sleep(1)

        yield

        # Cleanup
        if hasattr(self, "agent"):
            self.agent.disconnect()
        if hasattr(self, "config_agent"):
            self.config_agent.disconnect()

    def test_config_defaults_only(self):
        """Test config.get() returns defaults when no server config exists."""
        # Set a default configuration
        default_config = {
            "interval": 60,
            "enabled": True,
            "max_retries": 3,
            "devices": ["default_device"],
        }

        self.agent.vip.config.set_default("test_config", default_config)

        # Get config - should return defaults
        result = self.agent.vip.config.get("test_config")

        assert result == default_config, "Should return default config when no server config exists"

    def test_debug_server_config_set(self):
        """Debug test to see what happens when we try to set server config."""
        # Store configuration on server using set method
        server_config = {"timeout": 120, "retry_count": 5, "endpoints": ["server1", "server2"]}

        print(f"Attempting to set server config: {server_config}")

        # Store via config_agent using the set method
        store_result = self.config_agent.vip.config.set("test_server_config", server_config)
        print(f"Store result type: {type(store_result)}")

        try:
            response = store_result.get(timeout=5)
            print(f"Store response: {response}")
        except Exception as e:
            print(f"Store error: {e}")

        # Wait for config to be processed
        gevent.sleep(1)

        # Try to get config from SAME agent (not different agent)
        print("Attempting to get config from same agent...")
        try:
            result = self.config_agent.vip.config.get("test_server_config")
            print(f"Get result: {result}")
            print(f"Get result type: {type(result)}")

            # Should return the server config since no defaults are set
            assert result == server_config, f"Should return server config. Expected: {server_config}, Got: {result}"
        except Exception as e:
            print(f"Get error: {e}")
            assert False, f"Get should not fail: {e}"

    def test_simple_merging(self):
        """Test basic config merging with simpler setup."""
        # Set default first
        default_config = {"a": 1, "b": 2}
        self.agent.vip.config.set_default("merge_test", default_config)

        # Verify defaults work
        result1 = self.agent.vip.config.get("merge_test")
        print(f"Default only result: {result1}")
        assert result1 == default_config, "Defaults should work"

        # Now try to add server config (using SAME agent)
        server_config = {"a": 999, "c": 3}
        print(f"Setting server config: {server_config}")

        store_result = self.agent.vip.config.set("merge_test", server_config)
        try:
            store_response = store_result.get(timeout=5)
            print(f"Store response: {store_response}")
        except Exception as e:
            print(f"Store failed: {e}")

        gevent.sleep(1)

        # Get merged result
        result2 = self.agent.vip.config.get("merge_test")
        print(f"After server config result: {result2}")

        # Expected: {"a": 999, "b": 2, "c": 3} (server overrides 'a', adds 'c', keeps 'b' from defaults)
        expected = {"a": 999, "b": 2, "c": 3}

        print(f"Expected: {expected}")
        print(f"Actual: {result2}")

        assert result2 == expected, f"Config should be merged. Expected: {expected}, Got: {result2}"
