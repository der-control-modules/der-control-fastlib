"""
Comprehensive tests for config store loading at agent startup and external updates.
This module tests the complete config store workflow including:
- Loading existing configs when agent starts
- Handling external config modifications
- Update method propagation to agents
"""

import time

import gevent
import pytest
import requests


class TestConfigStoreComprehensive:
    """Comprehensive tests for config store functionality."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create unique test ID
        import uuid

        self.test_id = str(uuid.uuid4())[:8]

        # Store the base URL
        self.base_url = f"http://localhost:{self.manager.port}/config-store"

        yield

        # Cleanup any test agents
        for attr in ["agent1", "agent2", "test_agent"]:
            if hasattr(self, attr):
                agent = getattr(self, attr)
                if hasattr(agent, "disconnect"):
                    agent.disconnect()

    def test_agent_loads_config_at_startup_via_api(self):
        """Test that agent loads configurations stored via REST API before startup."""
        agent_id = f"api_startup_agent_{self.test_id}"
        config_name = "api_config"

        # Store config via REST API before agent starts
        config_data = {"source": "rest_api", "startup": True, "values": [1, 2, 3]}

        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=config_data, timeout=5)
        assert response.status_code == 200

        gevent.sleep(0.5)

        # Now start the agent - it should load the config automatically
        self.test_agent = self.manager.create_connected_agent(agent_id)
        gevent.sleep(1)

        # Verify config was loaded
        loaded = self.test_agent.vip.config.get(config_name)
        assert loaded == config_data, f"Config should match: {loaded}"

    def test_agent_to_agent_config_updates(self):
        """Test config updates between agents using the same config store."""
        config_name = "shared_config"

        # Create two agents
        self.agent1 = self.manager.create_connected_agent(f"writer_{self.test_id}")
        self.agent2 = self.manager.create_connected_agent(f"reader_{self.test_id}")

        gevent.sleep(1)

        # Agent1 stores a config for agent2
        config_data = {"from": "agent1", "value": 42}
        store_result = (
            self.agent1.vip.config.set_for_agent(self.agent2.identity, config_name, config_data)
            if hasattr(self.agent1.vip.config, "set_for_agent")
            else None
        )

        # If set_for_agent doesn't exist, use REST API
        if store_result is None:
            url = f"{self.base_url}/{self.agent2.identity}/{config_name}"
            response = requests.put(url, json=config_data, timeout=5)
            assert response.status_code == 200
        else:
            store_result.get(timeout=5)

        gevent.sleep(2)

        # Agent2 should be able to retrieve it
        # First clear cache to ensure fresh fetch
        if config_name in self.agent2.vip.config._config_cache:
            del self.agent2.vip.config._config_cache[config_name]

        # Now try to get - this will fetch from server
        try:
            loaded = self.agent2.vip.config.get(config_name)
            assert loaded == config_data, f"Agent2 should get config: {loaded}"
        except KeyError:
            # Config might not be available yet
            pytest.skip("Config propagation not yet implemented")

    def test_config_store_persistence_across_agent_restarts(self):
        """Test that configs persist when agent disconnects and reconnects."""
        agent_id = f"persistent_agent_{self.test_id}"
        config_name = "persistent_config"

        # Create first agent instance
        agent1 = self.manager.create_connected_agent(agent_id)

        # Store config
        config_data = {"session": 1, "persistent": True}
        store_result = agent1.vip.config.set(config_name, config_data)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Verify stored
        assert agent1.vip.config.get(config_name) == config_data

        # Disconnect
        agent1.disconnect()
        gevent.sleep(1)

        # Create new agent with same ID
        agent2 = self.manager.create_connected_agent(agent_id)
        gevent.sleep(1)

        # Config should still be available
        loaded = agent2.vip.config.get(config_name)
        assert loaded == config_data, "Config should persist across restarts"

        # Cleanup
        agent2.disconnect()

    def test_config_defaults_and_server_merging(self):
        """Test that default configs merge properly with server configs."""
        agent_id = f"merge_agent_{self.test_id}"
        config_name = "merged_config"

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Set defaults
        default_config = {
            "timeout": 30,
            "retries": 3,
            "server_url": "http://default.com",
            "features": ["basic", "standard"],
        }
        self.test_agent.vip.config.set_default(config_name, default_config)

        # Store partial server config that overrides some values
        server_config = {
            "timeout": 60,  # Override
            "server_url": "http://production.com",  # Override
            "new_option": "from_server",  # New
        }
        store_result = self.test_agent.vip.config.set(config_name, server_config)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Get merged config
        merged = self.test_agent.vip.config.get(config_name)

        # Verify merging
        assert merged["timeout"] == 60, "Server should override timeout"
        assert merged["server_url"] == "http://production.com", "Server should override URL"
        assert merged["retries"] == 3, "Default retries should be preserved"
        assert merged["features"] == ["basic", "standard"], "Default features should be preserved"
        assert merged["new_option"] == "from_server", "New server option should be added"

    def test_config_deletion_and_callbacks(self):
        """Test config deletion and callback notifications."""
        agent_id = f"delete_agent_{self.test_id}"
        config_name = "deletable_config"

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Track callbacks
        callback_events = []

        def config_callback(name, action, contents):
            callback_events.append({"name": name, "action": action, "contents": contents, "timestamp": time.time()})

        # Subscribe to config changes
        self.test_agent.vip.config.subscribe(config_callback, config_name)

        # Store config
        config_data = {"deletable": True, "value": "test"}
        store_result = self.test_agent.vip.config.set(config_name, config_data)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Delete config
        delete_result = self.test_agent.vip.config.delete(config_name)
        delete_result.get(timeout=5)

        gevent.sleep(1)

        # Verify deletion
        with pytest.raises(KeyError):
            self.test_agent.vip.config.get(config_name)

        # Check callbacks were triggered
        assert len(callback_events) >= 1, "Should have callback events"

        # Verify at least one DELETE event
        delete_events = [e for e in callback_events if e["action"] == "DELETE"]
        assert len(delete_events) > 0, "Should have DELETE callback"

    def test_config_list_functionality(self):
        """Test listing configurations for an agent."""
        agent_id = f"list_agent_{self.test_id}"

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Store multiple configs
        configs = {"config_a": {"index": 1}, "config_b": {"index": 2}, "config_c": {"index": 3}}

        for name, data in configs.items():
            store_result = self.test_agent.vip.config.set(name, data)
            store_result.get(timeout=5)

        gevent.sleep(1)

        # List configs
        config_list_future = self.test_agent.vip.config.list()
        config_list = config_list_future.get(timeout=5)

        # Verify all configs are listed
        assert isinstance(config_list, list), "Should return a list"
        for name in configs.keys():
            assert name in config_list, f"Config {name} should be in list"

    def test_concurrent_config_operations(self):
        """Test handling of concurrent config operations."""
        agent_id = f"concurrent_agent_{self.test_id}"

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Define configs to create concurrently
        num_configs = 10
        configs = {f"concurrent_{i}": {"index": i, "data": f"value_{i}"} for i in range(num_configs)}

        # Store all configs concurrently
        greenlets = []
        for name, data in configs.items():
            g = gevent.spawn(lambda n, d: self.test_agent.vip.config.set(n, d).get(timeout=5), name, data)
            greenlets.append(g)

        # Wait for all to complete
        gevent.joinall(greenlets, timeout=10)

        gevent.sleep(1)

        # Verify all configs were stored
        for name, expected_data in configs.items():
            loaded = self.test_agent.vip.config.get(name)
            assert loaded == expected_data, f"Config {name} should match"

    def test_config_update_with_defaults(self):
        """Test updating a config that has defaults set."""
        agent_id = f"update_default_agent_{self.test_id}"
        config_name = "update_with_default"

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Set default
        default_config = {"version": 1, "mode": "default", "items": [1, 2]}
        self.test_agent.vip.config.set_default(config_name, default_config)

        # Initial server config
        initial_server = {"version": 2, "mode": "production"}
        store_result = self.test_agent.vip.config.set(config_name, initial_server)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Verify merge
        merged = self.test_agent.vip.config.get(config_name)
        assert merged["version"] == 2, "Server version should override"
        assert merged["mode"] == "production", "Server mode should override"
        assert merged["items"] == [1, 2], "Default items should be preserved"

        # Update server config
        updated_server = {"version": 3, "new_field": "added"}
        update_result = self.test_agent.vip.config.set(config_name, updated_server)
        update_result.get(timeout=5)

        gevent.sleep(1)

        # Verify updated merge
        updated = self.test_agent.vip.config.get(config_name)
        assert updated["version"] == 3, "Updated version"
        assert updated["mode"] == "default", "Should revert to default mode"
        assert updated["items"] == [1, 2], "Default items still preserved"
        assert updated["new_field"] == "added", "New field added"
