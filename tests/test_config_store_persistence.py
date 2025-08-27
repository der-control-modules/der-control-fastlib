"""
Test config store persistence to disk and through agent restarts.
"""

import json
import os
import uuid

import gevent
import pytest


class TestConfigStorePersistence:
    """Test config store persistence functionality."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment with isolated config store."""
        # Store the message bus manager reference
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Store the config store directory for verification
        self.config_store_dir = self.manager.temp_config_dir

        # Create a unique test agent identity
        self.test_id = str(uuid.uuid4())[:8]
        self.agent_identity = f"persistence_test_agent_{self.test_id}"

        # Keep track of created agents for cleanup
        self.created_agents = []

        yield

        # Explicit cleanup of any agents created during the test
        for agent in self.created_agents:
            try:
                if hasattr(agent, "connected") and agent.connected:
                    agent.disconnect()
                    gevent.sleep(0.1)  # Small delay for cleanup
            except Exception as e:
                print(f"Warning: Failed to disconnect agent: {e}")

        # Clear the list
        self.created_agents.clear()

        # Additional cleanup - ensure the message bus is stopped
        if hasattr(self.manager, "bus") and self.manager.bus and self.manager.bus.is_running():
            self.manager.bus.stop()
            gevent.sleep(0.5)  # Give time for cleanup

    def create_tracked_agent(self, identity):
        """Create an agent and track it for cleanup."""
        agent = self.manager.create_connected_agent(identity)
        self.created_agents.append(agent)
        return agent

    def test_config_store_creates_files_on_disk(self):
        """Test that storing a config creates actual files on disk."""
        # Define test configuration
        config_name = "test_persistence_config"
        test_config = {
            "database_url": "sqlite:///test.db",
            "api_key": "test_api_key_12345",
            "settings": {"debug": True, "timeout": 30, "retry_count": 3},
            "feature_flags": ["feature_a", "feature_b"],
        }

        # Store config directly via HTTP API to test our updated endpoint
        import httpx

        url = f"http://{self.manager.host}:{self.manager.port}/config-store/{self.agent_identity}/{config_name}"

        with httpx.Client() as client:
            response = client.put(url, json=test_config)
            assert response.status_code == 200, f"Failed to store config: {response.text}"

        # Give some time for the file to be written
        gevent.sleep(0.5)

        # Verify files exist on disk
        agent_dir = os.path.join(self.config_store_dir, self.agent_identity)
        config_file = os.path.join(agent_dir, config_name)  # Config files have no extension
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")

        assert os.path.exists(agent_dir), f"Agent directory not created: {agent_dir}"
        assert os.path.exists(config_file), f"Config file not created: {config_file}"
        assert os.path.exists(metadata_file), f"Metadata file not created: {metadata_file}"

        # Verify config file contents
        with open(config_file) as f:
            stored_config = json.load(f)
        assert stored_config == test_config, "Stored config doesn't match original"

        # Verify metadata file contents
        with open(metadata_file) as f:
            metadata = json.load(f)
        assert metadata["type"] == "json", "Config type not recorded correctly"
        assert "created" in metadata, "Creation timestamp missing"
        assert "last_updated" in metadata, "Last updated timestamp missing"

    def test_config_store_csv_persistence(self):
        """Test that CSV configs are persisted correctly."""
        # No agent needed for this test since we're using direct HTTP calls

        # Test CSV data
        config_name = "test_csv_config"
        csv_data = "name,value,description\ndb_host,localhost,Database server\ndb_port,5432,Database port\napi_timeout,30,API timeout in seconds"

        # Store CSV config by directly calling the FastAPI endpoint
        import httpx

        url = f"http://{self.manager.host}:{self.manager.port}/config-store/{self.agent_identity}/{config_name}"

        with httpx.Client() as client:
            response = client.put(url, content=csv_data, headers={"Content-Type": "text/csv"})
            assert response.status_code == 200, f"Failed to store CSV config: {response.text}"

        # Verify files exist on disk
        agent_dir = os.path.join(self.config_store_dir, self.agent_identity)
        config_file = os.path.join(agent_dir, config_name)  # Config files have no extension
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")

        assert os.path.exists(config_file), f"CSV config file not created: {config_file}"
        assert os.path.exists(metadata_file), f"CSV metadata file not created: {metadata_file}"

        # Verify CSV file contents
        with open(config_file) as f:
            stored_csv = f.read()
        assert stored_csv == csv_data, "Stored CSV doesn't match original"

        # Verify metadata
        with open(metadata_file) as f:
            metadata = json.load(f)
        assert metadata["type"] == "csv", "CSV config type not recorded correctly"

    def test_config_persistence_through_agent_restart(self):
        """Test that configs persist through agent disconnect/reconnect cycles."""
        # Phase 1: Create agent and store config
        agent1 = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)  # Wait for connection

        config_name = "restart_persistence_config"
        original_config = {
            "version": "1.0.0",
            "module_settings": {
                "logging_level": "DEBUG",
                "cache_size": 1000,
                "enabled_features": ["auth", "analytics", "monitoring"],
            },
            "thresholds": {"max_connections": 100, "timeout_seconds": 45},
        }

        # Store config
        result = agent1.vip.config.set(config_name, original_config)
        assert result.get(), "Failed to store config in phase 1"

        # Verify config can be retrieved
        retrieved_config = agent1.vip.config.get(config_name)
        assert retrieved_config == original_config, "Config not retrievable after storage"

        # Disconnect agent
        agent1.disconnect()
        gevent.sleep(1)  # Allow cleanup time

        # Phase 2: Create new agent instance with same identity
        agent2 = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)  # Wait for connection

        # Verify config still exists and can be retrieved
        retrieved_after_restart = agent2.vip.config.get(config_name)
        assert retrieved_after_restart == original_config, "Config lost after agent restart"

        # Update the config to test persistence of changes
        updated_config = original_config.copy()
        updated_config["version"] = "1.1.0"
        updated_config["module_settings"]["logging_level"] = "INFO"
        updated_config["new_feature"] = {"enabled": True, "beta": False}

        result = agent2.vip.config.set(config_name, updated_config)
        assert result.get(), "Failed to update config"

        # Disconnect again
        agent2.disconnect()
        gevent.sleep(1)

        # Phase 3: Third agent instance to verify updated config persisted
        agent3 = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)

        final_config = agent3.vip.config.get(config_name)
        assert final_config == updated_config, "Updated config not persisted through restart"

    def test_multiple_configs_persistence(self):
        """Test that multiple configs for the same agent persist correctly."""
        agent = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)

        # Store multiple configs
        configs = {
            "database_config": {"host": "db.example.com", "port": 5432, "database": "production", "ssl": True},
            "api_config": {
                "base_url": "https://api.example.com",
                "version": "v2",
                "rate_limit": 1000,
                "endpoints": ["users", "orders", "products"],
            },
            "feature_flags": {"new_ui": True, "beta_features": False, "analytics": True},
        }

        # Store all configs
        for name, config in configs.items():
            result = agent.vip.config.set(name, config)
            assert result.get(), f"Failed to store config: {name}"

        # Verify all configs exist on disk
        agent_dir = os.path.join(self.config_store_dir, self.agent_identity)
        for name in configs.keys():
            config_file = os.path.join(agent_dir, name)  # Config files have no extension
            assert os.path.exists(config_file), f"Config file missing: {config_file}"

        # Disconnect and reconnect
        agent.disconnect()
        gevent.sleep(1)

        agent_new = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)

        # Verify all configs can be retrieved
        for name, expected_config in configs.items():
            retrieved = agent_new.vip.config.get(name)
            assert retrieved == expected_config, f"Config {name} not persisted correctly"

    def test_config_deletion_persistence(self):
        """Test that config deletion persists through restarts."""
        agent = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)

        config_name = "deletion_test_config"
        test_config = {"test": "data", "number": 42}

        # Store config
        result = agent.vip.config.set(config_name, test_config)
        assert result.get(), "Failed to store config"

        # Verify it exists
        retrieved = agent.vip.config.get(config_name)
        assert retrieved == test_config, "Config not stored correctly"

        # Delete the config
        delete_result = agent.vip.config.delete(config_name)
        assert delete_result.get(), "Failed to delete config"

        # Verify files are removed from disk
        agent_dir = os.path.join(self.config_store_dir, self.agent_identity)
        config_file = os.path.join(agent_dir, config_name)  # Config files have no extension
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")

        assert not os.path.exists(config_file), "Config file not deleted from disk"
        assert not os.path.exists(metadata_file), "Metadata file not deleted from disk"

        # Restart agent and verify config is still gone
        agent.disconnect()
        gevent.sleep(1)

        agent_new = self.create_tracked_agent(self.agent_identity)
        gevent.sleep(1)

        # Try to retrieve deleted config - should fail
        try:
            retrieved_after_restart = agent_new.vip.config.get(config_name)
            assert retrieved_after_restart is None, "Deleted config should not be retrievable"
        except KeyError:
            # This is expected - the config should not exist
            pass

    def test_config_store_directory_structure(self):
        """Test that the config store creates the correct directory structure."""
        # Create agents with different identities
        agent1_identity = f"agent1_{self.test_id}"
        agent2_identity = f"agent2_{self.test_id}"

        agent1 = self.create_tracked_agent(agent1_identity)
        agent2 = self.create_tracked_agent(agent2_identity)
        gevent.sleep(1)

        # Store configs for each agent
        config1 = {"agent1_setting": "value1"}
        config2 = {"agent2_setting": "value2"}

        result1 = agent1.vip.config.set("config1", config1)
        result2 = agent2.vip.config.set("config2", config2)

        # Ensure configs were stored successfully
        assert result1.get(), "Failed to store config1"
        assert result2.get(), "Failed to store config2"

        # Give time for files to be written
        gevent.sleep(0.5)

        # Verify directory structure
        agent1_dir = os.path.join(self.config_store_dir, agent1_identity)
        agent2_dir = os.path.join(self.config_store_dir, agent2_identity)

        assert os.path.exists(agent1_dir), "Agent1 directory not created"
        assert os.path.exists(agent2_dir), "Agent2 directory not created"

        # Verify configs are isolated
        config1_file = os.path.join(agent1_dir, "config1")  # Config files have no extension
        config2_file = os.path.join(agent2_dir, "config2")

        assert os.path.exists(config1_file), "Agent1 config file not created"
        assert os.path.exists(config2_file), "Agent2 config file not created"

        # Verify configs don't cross-contaminate
        assert not os.path.exists(os.path.join(agent1_dir, "config2")), "Config isolation violated"
        assert not os.path.exists(os.path.join(agent2_dir, "config1")), "Config isolation violated"
