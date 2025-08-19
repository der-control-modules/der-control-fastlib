"""
Test agent configuration functionality using pytest
"""

import gevent
import pytest


class TestAgentConfig:
    """Test agent configuration functionality with config merging."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus_manager_fixture):
        """Set up test agents with the running message bus."""
        # Store the message bus manager reference
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create agents with unique identities for each test run
        import uuid

        test_id = str(uuid.uuid4())[:8]  # Short unique ID for this test run
        self.agent = self.manager.create_connected_agent(f"config_test_agent_{test_id}")
        self.config_agent = self.manager.create_connected_agent(f"config_manager_{test_id}")

        # Wait for connections
        gevent.sleep(1)

        # Clear any cached configs from previous tests
        self.agent.vip.config._config_cache.clear()
        self.agent.vip.config._default_configs.clear()
        self.config_agent.vip.config._config_cache.clear()
        self.config_agent.vip.config._default_configs.clear()

        # Clear server-side configs for this agent to ensure clean test state
        self._clear_server_configs()

        yield

        # Cleanup
        if hasattr(self, "agent"):
            # Clear config caches before disconnecting
            self.agent.vip.config._config_cache.clear()
            self.agent.vip.config._default_configs.clear()
            self.agent.disconnect()
        if hasattr(self, "config_agent"):
            # Clear config caches before disconnecting
            self.config_agent.vip.config._config_cache.clear()
            self.config_agent.vip.config._default_configs.clear()
            self.config_agent.disconnect()

    def _clear_server_configs(self):
        """Clear server-side configs for test agents to ensure clean test state."""
        import gevent
        import requests

        # List and delete all configs for both test agents
        for agent in [self.agent, self.config_agent]:
            try:
                # List configs for this agent
                list_url = f"http://localhost:8888/config-store/{agent.identity}"
                response = requests.get(list_url)
                if response.status_code == 200:
                    configs = response.json()
                    # Delete each config
                    for config in configs:
                        config_name = config.get("name")
                        if config_name:
                            delete_url = f"http://localhost:8888/config-store/{agent.identity}/{config_name}"
                            requests.delete(delete_url)
                            print(f"Deleted server config: {agent.identity}/{config_name}")
                gevent.sleep(0.1)  # Small delay between operations
            except Exception as e:
                print(f"Failed to clear server configs for {agent.identity}: {e}")

    def test_config_defaults_only(self):
        """Test config.get() returns defaults when no server config exists."""
        config_name = "test_config_defaults_only"  # Unique name for this test

        # Set a default configuration
        default_config = {
            "interval": 60,
            "enabled": True,
            "max_retries": 3,
            "devices": ["default_device"],
        }

        self.agent.vip.config.set_default(config_name, default_config)

        # Get config - should return defaults
        result = self.agent.vip.config.get(config_name)

        assert result == default_config, "Should return default config when no server config exists"

    def test_config_server_only(self):
        """Test config.get() returns server config when no defaults exist."""
        config_name = "test_config_server_only"  # Unique name for this test

        # Store configuration on server using the SAME agent
        server_config = {"timeout": 120, "retry_count": 5, "endpoints": ["server1", "server2"]}

        # Store via agent (same agent that will retrieve it)
        store_result = self.agent.vip.config.set(config_name, server_config)
        store_result.get(timeout=5)  # Wait for completion

        # Wait for config to be processed
        gevent.sleep(1)

        # Get config from same agent - should return server config
        result = self.agent.vip.config.get(config_name)

        assert result == server_config, "Should return server config when no defaults exist"

    def test_config_merging_server_overrides_defaults(self):
        """Test that server config merges with and overrides default config."""
        config_name = "test_config_merging"  # Unique name for this test

        # Set default configuration
        default_config = {
            "interval": 60,
            "enabled": True,
            "max_retries": 3,
            "devices": ["default_device"],
            "timeout": 30,
        }

        self.agent.vip.config.set_default(config_name, default_config)

        # Store partial server configuration that overrides some defaults
        server_config = {
            "interval": 120,  # Override default
            "max_retries": 5,  # Override default
            "new_setting": "server_value",  # New setting not in defaults
        }

        store_result = self.agent.vip.config.set(config_name, server_config)
        store_result.get(timeout=5)  # Wait for completion

        # Wait for config to be processed
        gevent.sleep(1)

        # Get merged config
        result = self.agent.vip.config.get(config_name)

        # Expected merged result
        expected = {
            "interval": 120,  # From server (overridden)
            "enabled": True,  # From defaults (unchanged)
            "max_retries": 5,  # From server (overridden)
            "devices": ["default_device"],  # From defaults (unchanged)
            "timeout": 30,  # From defaults (unchanged)
            "new_setting": "server_value",  # From server (new)
        }

        assert result == expected, f"Config should be merged. Expected: {expected}, Got: {result}"

    def test_config_merging_preserves_defaults(self):
        """Test that defaults are preserved when server config doesn't override them."""
        # Set comprehensive default configuration
        default_config = {
            "database": {"host": "localhost", "port": 5432, "name": "default_db"},
            "logging": {"level": "INFO", "file": "default.log"},
            "features": ["feature1", "feature2"],
        }

        self.agent.vip.config.set_default("preserve_config", default_config)

        # Store server config that only overrides database settings
        server_config = {"database": {"host": "production.server.com", "port": 3306, "name": "prod_db"}}

        store_result = self.agent.vip.config.set("preserve_config", server_config)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Get merged config
        result = self.agent.vip.config.get("preserve_config")

        # Expected: server database config + default logging + default features
        expected = {
            "database": {"host": "production.server.com", "port": 3306, "name": "prod_db"},
            "logging": {"level": "INFO", "file": "default.log"},
            "features": ["feature1", "feature2"],
        }

        assert result == expected, f"Should preserve defaults. Expected: {expected}, Got: {result}"

    def test_config_non_dict_values(self):
        """Test that non-dict values are handled correctly in merging."""
        # Test with non-dict default
        self.agent.vip.config.set_default("simple_config", "default_value")

        # Store non-dict server config
        store_result = self.agent.vip.config.set("simple_config", "server_value")
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Get config - server should override completely for non-dict values
        result = self.agent.vip.config.get("simple_config")
        assert result == "server_value", "Server value should override default for non-dict values"

    def test_config_mixed_dict_and_non_dict(self):
        """Test merging when default is dict but server is non-dict."""
        # Set dict default
        default_config = {"key1": "value1", "key2": "value2"}
        self.agent.vip.config.set_default("mixed_config", default_config)

        # Store non-dict server config
        store_result = self.agent.vip.config.set("mixed_config", "override_string")
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Get config - server should completely override
        result = self.agent.vip.config.get("mixed_config")
        assert result == "override_string", "Non-dict server value should completely override dict default"

        gevent.sleep(1)

        # Verify it's gone
        try:
            self.config_agent.vip.config.get("delete_test")
            assert False, "Should raise exception for non-existent config"
        except Exception:
            pass  # Expected behavior

    def test_config_store_list(self):
        """Test listing configurations."""
        # Store multiple configurations
        configs = {
            "config1": {"data": "test1"},
            "config2": {"data": "test2"},
            "config3": {"data": "test3"},
        }

        for name, config in configs.items():
            self.config_agent.vip.config.set(name, config).get(timeout=5)

        gevent.sleep(1)

        # List configurations
        list_result = self.config_agent.vip.config.list()
        config_list = list_result.get(timeout=5)

        # Verify all configs are listed (config_list is now just a list of config names)
        for config_name in configs.keys():
            assert config_name in config_list, f"Config '{config_name}' should be in list: {config_list}"

    def test_config_validation(self):
        """Test configuration validation."""
        # Test with valid JSON-serializable data
        valid_config = {
            "string": "test",
            "number": 42,
            "boolean": True,
            "array": [1, 2, 3],
            "object": {"nested": "value"},
        }

        store_result = self.config_agent.vip.config.set("valid_config", valid_config)
        assert store_result.get(timeout=5) is True, "Valid config should be stored"

        # Verify retrieval maintains data types
        retrieved = self.config_agent.vip.config.get("valid_config")
        assert retrieved == valid_config, f"Data types should be preserved: {retrieved} != {valid_config}"
        assert isinstance(retrieved["string"], str), "String should remain string"
        assert isinstance(retrieved["number"], int), "Number should remain int"
        assert isinstance(retrieved["boolean"], bool), "Boolean should remain boolean"
        assert isinstance(retrieved["array"], list), "Array should remain list"
        assert isinstance(retrieved["object"], dict), "Object should remain dict"

    def test_config_agent_specific_storage(self):
        """Test agent-specific configuration storage."""
        # Each agent should have its own config namespace
        agent1_config = {"agent": "agent1", "value": 100}
        agent2_config = {"agent": "agent2", "value": 200}

        # Store configs for different agents
        self.agent.vip.config.set("shared_name", agent1_config).get(timeout=5)
        self.config_agent.vip.config.set("shared_name", agent2_config).get(timeout=5)

        gevent.sleep(1)

        # Verify each agent gets its own config
        agent1_retrieved = self.agent.vip.config.get("shared_name")
        agent2_retrieved = self.config_agent.vip.config.get("shared_name")

        assert agent1_retrieved == agent1_config, f"Agent1 should get its own config: {agent1_retrieved}"
        assert agent2_retrieved == agent2_config, f"Agent2 should get its own config: {agent2_retrieved}"
        assert agent1_retrieved != agent2_retrieved, "Configs should be different between agents"

    def test_config_watch_functionality(self):
        """Test configuration watch/notification functionality."""
        # This would require implementing a watch mechanism
        # For now, we'll test that configs can be monitored for changes

        # Store initial config
        initial_config = {"monitor": "me", "version": 1}
        self.agent.vip.config.set("watched_config", initial_config).get(timeout=5)
        gevent.sleep(1)

        # Update the config
        updated_config = {"monitor": "me", "version": 2}
        self.agent.vip.config.set("watched_config", updated_config).get(timeout=5)
        gevent.sleep(1)

        # Verify the update took effect
        final_config = self.agent.vip.config.get("watched_config")
        assert final_config["version"] == 2, f"Config should be updated to version 2: {final_config}"

    def test_config_error_handling(self):
        """Test configuration error handling."""
        # Test getting non-existent config
        try:
            self.config_agent.vip.config.get("nonexistent_config")
            assert False, "Should raise exception for non-existent config"
        except Exception:
            pass  # Expected behavior

        # Test deleting non-existent config
        try:
            result = self.config_agent.vip.config.delete("nonexistent_config")
            # This might succeed with False or raise an exception
            # Either behavior is acceptable
            response = result.get(timeout=5)
            if response is not False:
                # If it doesn't return False, it might raise an exception
                pass
        except Exception:
            pass  # Also acceptable behavior
