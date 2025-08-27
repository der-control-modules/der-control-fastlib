"""
Test agent configuration loading at startup and external config updates.
Tests both loading configs when agent starts and handling external updates.
"""

import json
import tempfile
import time
from pathlib import Path

import gevent
import pytest
import requests


class ConfigTrackingAgent:
    """Custom agent that tracks config updates for testing."""

    def __init__(self, identity):
        self.identity = identity
        self.config_updates = []
        self.initial_configs = None
        self.startup_config_loaded = False

    def onconfigure(self, config_name, action, contents):
        """Track configuration updates."""
        self.config_updates.append(
            {"config_name": config_name, "action": action, "contents": contents, "timestamp": time.time()}
        )


class TestConfigStoreAgentStartup:
    """Test configuration loading at agent startup and external updates."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create unique test ID
        import uuid

        self.test_id = str(uuid.uuid4())[:8]

        # Setup config directory for testing
        self.config_dir = Path(self.manager.temp_config_dir) if self.manager.temp_config_dir else None
        if not self.config_dir:
            # If config dir not set, use a temp directory
            self.config_dir = Path(tempfile.mkdtemp(prefix="aems_test_config_"))

        # Store the base URL
        self.base_url = f"http://localhost:{self.manager.port}/config-store"

        yield

        # Cleanup any test agents if they exist
        for attr in ["test_agent", "tracking_agent", "external_agent"]:
            if hasattr(self, attr):
                agent = getattr(self, attr)
                if hasattr(agent, "disconnect"):
                    agent.disconnect()

    def _store_config_via_admin(self, agent_id, config_name, config_data):
        """Store configuration directly via REST API (simulating admin/server update)."""
        url = f"{self.base_url}/{agent_id}/{config_name}"
        # No requesting_agent parameter = admin override
        response = requests.put(url, json=config_data, timeout=5)
        assert response.status_code == 200, f"Failed to store admin config: {response.text}"
        return response.json()

    def _update_config_file_directly(self, agent_id, config_name, config_data):
        """Update config file directly on disk (simulating external file change)."""
        # Create agent config directory
        agent_dir = self.config_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Write config file
        config_file = agent_dir / f"{config_name}.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f, indent=2)

        # Give file watcher time to detect change
        gevent.sleep(0.5)

    def test_agent_loads_existing_config_at_startup(self):
        """Test that agent loads existing configurations when it starts up."""
        agent_id = f"startup_test_agent_{self.test_id}"
        config_name = "startup_config"

        # Pre-store configuration before agent starts
        startup_config = {
            "mode": "production",
            "interval": 30,
            "devices": ["sensor1", "sensor2"],
            "thresholds": {"high": 100, "low": 10},
        }

        # Store config via admin before agent connects
        self._store_config_via_admin(agent_id, config_name, startup_config)

        # Give server time to process
        gevent.sleep(0.5)

        # Now create and connect the agent
        # Agent will automatically load configs on connection
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Give time for connection and config loading
        gevent.sleep(1)

        # Verify config was loaded - directly check if config is accessible
        # The agent should have loaded the config automatically on startup

        # Verify config can be retrieved
        loaded_config = self.test_agent.vip.config.get(config_name)
        assert loaded_config == startup_config, f"Loaded config should match stored: {loaded_config}"

    def test_agent_loads_multiple_configs_at_startup(self):
        """Test that agent loads all its configurations at startup."""
        agent_id = f"multi_config_agent_{self.test_id}"

        # Pre-store multiple configurations
        configs = {
            "database_config": {"host": "localhost", "port": 5432, "name": "testdb"},
            "logging_config": {"level": "DEBUG", "file": "agent.log"},
            "device_config": {"devices": ["dev1", "dev2", "dev3"], "poll_rate": 60},
        }

        # Store all configs before agent starts (via admin)
        for name, data in configs.items():
            self._store_config_via_admin(agent_id, name, data)

        gevent.sleep(0.5)

        # Create and connect agent (configs will be loaded automatically)
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Give time for configs to load
        gevent.sleep(1)

        # Verify all configs were loaded by checking if we can retrieve them
        for config_name, expected_data in configs.items():
            loaded_config = self.test_agent.vip.config.get(config_name)
            assert (
                loaded_config == expected_data
            ), f"Config '{config_name}' should match expected: got {loaded_config}, expected {expected_data}"

    def test_admin_config_update_notifies_agent(self):
        """Test that admin/server config updates are propagated to the agent."""
        agent_id = f"update_test_agent_{self.test_id}"
        config_name = "dynamic_config"

        # Create and connect agent first
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Store initial configuration using the agent itself
        initial_config = {"version": 1, "mode": "test"}
        store_result = self.test_agent.vip.config.set(config_name, initial_config)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Verify initial config is set
        assert self.test_agent.vip.config.get(config_name) == initial_config

        # Simulate admin/server update (no requesting_agent parameter = admin override)
        updated_config = {"version": 2, "mode": "production", "new_field": "added"}

        # Use REST API without requesting_agent (simulating admin/server update)
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=updated_config, timeout=5)
        assert response.status_code == 200, f"Admin update should succeed: {response.text}"

        # Wait for update notification to propagate
        gevent.sleep(3)

        # Cache should now be automatically updated
        current_config = self.test_agent.vip.config.get(config_name)
        assert (
            current_config == updated_config
        ), f"Agent should have updated config: got {current_config}, expected {updated_config}"

    def test_file_based_config_update(self):
        """Test that file-based config changes are detected and propagated."""
        agent_id = f"file_update_agent_{self.test_id}"
        config_name = "file_config"

        # Create and connect agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Store initial config using agent
        initial_config = {"setting": "initial", "value": 100}
        store_result = self.test_agent.vip.config.set(config_name, initial_config)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Verify initial config
        assert self.test_agent.vip.config.get(config_name) == initial_config

        # Update config file directly
        updated_config = {"setting": "updated", "value": 200, "extra": "field"}
        self._update_config_file_directly(agent_id, config_name, updated_config)

        # Wait for file watcher to detect and propagate change
        gevent.sleep(2)

        # Verify agent has updated config
        current_config = self.test_agent.vip.config.get(config_name)
        assert current_config == updated_config, f"File update should be reflected in agent: {current_config}"

    def test_config_deletion_notifies_agent(self):
        """Test that config deletion is propagated to the agent."""
        agent_id = f"delete_test_agent_{self.test_id}"
        config_name = "temporary_config"

        # Create and connect agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Store configuration using agent
        config_data = {"temporary": True, "ttl": 3600}
        store_result = self.test_agent.vip.config.set(config_name, config_data)
        store_result.get(timeout=5)

        gevent.sleep(1)

        # Verify config exists
        assert self.test_agent.vip.config.get(config_name) == config_data

        # Delete configuration via API
        delete_url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.delete(delete_url, timeout=5)
        assert response.status_code == 200, f"Failed to delete config: {response.text}"

        gevent.sleep(1)

        # Verify config is deleted from agent's perspective
        with pytest.raises(Exception):
            self.test_agent.vip.config.get(config_name)

    def test_agent_with_default_and_server_config_at_startup(self):
        """Test agent startup with both default and server configurations."""
        agent_id = f"merged_config_agent_{self.test_id}"
        config_name = "merged_startup"

        # Pre-store server configuration
        server_config = {"server_setting": "from_server", "port": 8080, "override_me": "server_value"}
        self._store_config_via_admin(agent_id, config_name, server_config)

        gevent.sleep(0.5)

        # Create agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Set default configuration
        default_config = {
            "default_setting": "from_default",
            "timeout": 30,
            "override_me": "default_value",
            "features": ["feature1", "feature2"],
        }
        self.test_agent.vip.config.set_default(config_name, default_config)

        # Load configs
        self.test_agent._load_configs()

        gevent.sleep(1)

        # Get merged configuration
        merged_config = self.test_agent.vip.config.get(config_name)

        # Verify merging occurred correctly
        assert merged_config["server_setting"] == "from_server", "Server setting should be present"
        assert merged_config["default_setting"] == "from_default", "Default setting should be preserved"
        assert merged_config["override_me"] == "server_value", "Server should override default"
        assert merged_config["features"] == ["feature1", "feature2"], "Default list should be preserved"

    def test_concurrent_external_updates(self):
        """Test that concurrent external updates are handled correctly."""
        agent_id = f"concurrent_update_agent_{self.test_id}"

        # Create and connect agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Create multiple configs using agent
        configs = {}
        for i in range(5):
            config_name = f"concurrent_config_{i}"
            configs[config_name] = {"index": i, "initial": True}
            store_result = self.test_agent.vip.config.set(config_name, configs[config_name])
            store_result.get(timeout=5)

        gevent.sleep(1)

        # Update all configs concurrently
        def update_config(name, index):
            updated = {"index": index, "initial": False, "updated_at": time.time()}
            self._store_config_via_admin(agent_id, name, updated)
            return name, updated

        # Launch concurrent updates
        greenlets = []
        for i, config_name in enumerate(configs.keys()):
            g = gevent.spawn(update_config, config_name, i)
            greenlets.append(g)

        # Wait for all updates to complete
        gevent.joinall(greenlets, timeout=5)

        # Give time for propagation
        gevent.sleep(2)

        # Verify all configs were updated
        for config_name in configs.keys():
            current = self.test_agent.vip.config.get(config_name)
            assert current["initial"] is False, f"Config {config_name} should be updated"
            assert "updated_at" in current, f"Config {config_name} should have update timestamp"

    def test_agent_reconnection_preserves_config(self):
        """Test that agent configs are preserved across reconnections."""
        agent_id = f"reconnect_test_agent_{self.test_id}"
        config_name = "persistent_config"

        # Store configuration
        config_data = {"persistent": True, "value": "should_survive_reconnect"}
        self._store_config_via_admin(agent_id, config_name, config_data)

        # Create and connect first agent instance
        agent1 = self.manager.create_connected_agent(agent_id)
        gevent.sleep(1)

        # Verify config is loaded
        assert agent1.vip.config.get(config_name) == config_data

        # Disconnect agent
        agent1.disconnect()
        gevent.sleep(1)

        # Create new agent instance with same ID
        agent2 = self.manager.create_connected_agent(agent_id)
        gevent.sleep(1)

        # Verify config is still available
        assert agent2.vip.config.get(config_name) == config_data, "Config should persist across agent reconnections"

        # Cleanup
        agent2.disconnect()

    def test_cross_agent_config_access_denied(self):
        """Test that agents cannot update other agents' configs."""
        agent_a_id = f"agent_a_{self.test_id}"
        agent_b_id = f"agent_b_{self.test_id}"
        config_name = "restricted_config"

        # Create two agents
        agent_a = self.manager.create_connected_agent(agent_a_id)
        agent_b = self.manager.create_connected_agent(agent_b_id)

        gevent.sleep(1)

        # Agent A creates its own config (should work)
        config_data = {"owner": "agent_a", "secret": "data"}
        result = agent_a.vip.config.set(config_name, config_data)
        result.get(timeout=5)

        # Verify Agent A can read its own config
        assert agent_a.vip.config.get(config_name) == config_data

        # Try to make Agent B update Agent A's config via REST API (should fail)
        url = f"{self.base_url}/{agent_a_id}/{config_name}"
        malicious_data = {"hacked": True, "owner": "agent_b"}
        params = {"requesting_agent": agent_b_id}

        response = requests.put(url, json=malicious_data, params=params, timeout=5)
        assert response.status_code == 403, f"Cross-agent update should be denied: {response.text}"

        # Verify Agent A's config was not changed
        original_config = agent_a.vip.config.get(config_name)
        assert original_config == config_data, "Agent A's config should be unchanged"

        # Admin update should still work (no requesting_agent)
        admin_data = {"updated_by": "admin", "timestamp": "2023"}
        response = requests.put(url, json=admin_data, timeout=5)
        assert response.status_code == 200, f"Admin update should work: {response.text}"

        gevent.sleep(1)

        # Agent A should see the admin update
        updated_config = agent_a.vip.config.get(config_name)
        assert updated_config == admin_data, "Agent should see admin update"

        # Cleanup
        agent_a.disconnect()
        agent_b.disconnect()
