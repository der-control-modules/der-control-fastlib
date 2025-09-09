"""
Test all three config update propagation flows:
1. External update → Cache update → AgentClass callback
2. AgentClass set() → Store update → Optional self-callback
3. Pubsub notification → Cache update → AgentClass callback
"""

import time

import gevent
import pytest
import requests

from aems.client.agent import Agent


class CallbackTrackingAgent(Agent):
    """Custom agent that tracks all config callbacks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.callback_history = []
        self.config_values = {}

    def on_config_update(self, config_name, action, contents):
        """Track config updates with timestamps."""
        self.callback_history.append(
            {"config_name": config_name, "action": action, "contents": contents, "timestamp": time.time()}
        )
        # Store the latest value
        if action == "DELETE":
            self.config_values.pop(config_name, None)
        else:
            self.config_values[config_name] = contents

    def clear_history(self):
        """Clear callback history for fresh testing."""
        self.callback_history = []


class TestConfigUpdateFlows:
    """Test all config update propagation flows."""

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

        # Cleanup
        for attr in ["tracking_agent", "external_agent", "agent1", "agent2"]:
            if hasattr(self, attr):
                agent = getattr(self, attr)
                if hasattr(agent, "disconnect"):
                    agent.disconnect()

    def test_flow1_external_update_triggers_callback(self):
        """Test Flow 1: External update → Cache update → AgentClass callback."""
        agent_id = f"flow1_agent_{self.test_id}"
        config_name = "external_test"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Subscribe to config updates - use config_name parameter
        self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        # Store initial config
        initial_config = {"version": 1, "source": "internal"}
        result = self.tracking_agent.vip.config.set(config_name, initial_config)
        result.get(timeout=5)

        gevent.sleep(1)

        # Clear history after initial set
        self.tracking_agent.clear_history()

        # External update via REST API
        updated_config = {"version": 2, "source": "external", "updated": True}
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=updated_config, timeout=5)
        assert response.status_code == 200

        # Wait for propagation
        gevent.sleep(3)

        # Verify cache was updated automatically
        cached_value = self.tracking_agent.vip.config.get(config_name)
        assert cached_value == updated_config, f"Cache should be updated: {cached_value}"

        # Verify callback was triggered
        assert len(self.tracking_agent.callback_history) > 0, "Callback should have been triggered"

        last_callback = self.tracking_agent.callback_history[-1]
        assert last_callback["config_name"] == config_name
        assert last_callback["action"] == "UPDATE"
        assert last_callback["contents"] == updated_config

    def test_flow2_agent_set_with_callback(self):
        """Test Flow 2: AgentClass set() → Store update → Self-callback."""
        agent_id = f"flow2_agent_{self.test_id}"
        config_name = "self_update_test"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Subscribe to config updates - use config_name parameter
        self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        gevent.sleep(1)

        # Test with callback enabled (default)
        config_with_callback = {"test": "with_callback", "value": 1}
        result = self.tracking_agent.vip.config.set(
            config_name,
            config_with_callback,
            send_update=True,  # Default, but explicit for clarity
        )
        result.get(timeout=5)

        gevent.sleep(1)

        # Verify callback was triggered
        assert len(self.tracking_agent.callback_history) > 0, "Callback should be triggered when send_update=True"

        last_callback = self.tracking_agent.callback_history[-1]
        assert last_callback["action"] in ["UPDATE", "NEW"]  # Accept either NEW (first time) or UPDATE
        assert last_callback["contents"] == config_with_callback

        # Clear history
        self.tracking_agent.clear_history()

        # Test without callback
        config_without_callback = {"test": "without_callback", "value": 2}
        result = self.tracking_agent.vip.config.set(
            config_name,
            config_without_callback,
            send_update=False,  # Disable callbacks - no RPC notification should be sent
        )
        result.get(timeout=5)

        gevent.sleep(1)

        # When an agent sets its own config with send_update=False, no callbacks should be triggered
        # This matches VOLTTRON behavior where send_update controls RPC notifications
        assert (
            len(self.tracking_agent.callback_history) == 0
        ), "No callbacks should be triggered when send_update=False for self-updates"

        # But config should still be updated
        current = self.tracking_agent.vip.config.get(config_name)
        assert current == config_without_callback, "Config should still be updated even without callback"

    def test_flow3_pubsub_notification(self):
        """Test Flow 3: Config update via pubsub → Cache update → Callback."""
        agent_id = f"flow3_agent_{self.test_id}"
        config_name = "pubsub_test"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Subscribe to config updates - use config_name parameter
        self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        # Store initial config
        initial = {"pubsub": "initial", "value": 100}
        result = self.tracking_agent.vip.config.set(config_name, initial)
        result.get(timeout=5)

        gevent.sleep(1)
        self.tracking_agent.clear_history()

        # Create another agent to update the config
        # This simulates an external system updating via the message bus
        external_id = f"external_{self.test_id}"
        self.external_agent = self.manager.create_connected_agent(external_id)

        # External agent updates the first agent's config via REST
        # (In real system, this might be done via platform mechanisms)
        updated = {"pubsub": "updated", "value": 200, "via": "external"}
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=updated, timeout=5)
        assert response.status_code == 200

        # The config store should publish update via pubsub
        gevent.sleep(3)

        # Verify cache updated
        cached = self.tracking_agent.vip.config.get(config_name)
        assert cached == updated, f"Cache should be updated via pubsub: {cached}"

        # Verify callback triggered
        assert len(self.tracking_agent.callback_history) > 0, "Callback should be triggered via pubsub"

        last = self.tracking_agent.callback_history[-1]
        assert last["contents"] == updated

    def test_config_deletion_flow(self):
        """Test config deletion propagation through all stages."""
        agent_id = f"delete_flow_agent_{self.test_id}"
        config_name = "delete_test"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Set a default for this config
        default_config = {"default": True, "value": "fallback"}
        self.tracking_agent.vip.config.set_default(config_name, default_config)

        # Subscribe to updates
        self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        # Store server config
        server_config = {"server": True, "value": "active"}
        result = self.tracking_agent.vip.config.set(config_name, server_config)
        result.get(timeout=5)

        gevent.sleep(1)
        self.tracking_agent.clear_history()

        # Delete via REST API
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.delete(url, timeout=5)
        assert response.status_code == 200

        gevent.sleep(3)

        # Config should revert to default (not completely deleted)
        current = self.tracking_agent.vip.config.get(config_name)
        assert current == default_config, f"Should revert to default after deletion: {current}"

        # Callback should be triggered with UPDATE (reverted to default)
        assert len(self.tracking_agent.callback_history) > 0
        last = self.tracking_agent.callback_history[-1]
        assert last["action"] in ["UPDATE", "DELETE"], f"Action should be UPDATE or DELETE: {last['action']}"

        # If UPDATE, should have default value
        if last["action"] == "UPDATE":
            assert last["contents"] == default_config

    def test_concurrent_updates_with_callbacks(self):
        """Test concurrent updates trigger appropriate callbacks."""
        agent_id = f"concurrent_agent_{self.test_id}"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Subscribe to multiple configs
        num_configs = 5
        for i in range(num_configs):
            config_name = f"concurrent_{i}"
            self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        gevent.sleep(1)

        # Store configs concurrently
        def store_config(idx):
            name = f"concurrent_{idx}"
            data = {"index": idx, "timestamp": time.time()}
            result = self.tracking_agent.vip.config.set(name, data)
            return result.get(timeout=5)

        greenlets = [gevent.spawn(store_config, i) for i in range(num_configs)]
        gevent.joinall(greenlets, timeout=10)

        gevent.sleep(2)

        # Should have received callbacks for all configs
        callback_configs = {cb["config_name"] for cb in self.tracking_agent.callback_history}
        expected_configs = {f"concurrent_{i}" for i in range(num_configs)}

        assert expected_configs.issubset(
            callback_configs
        ), f"Missing callbacks for some configs: {expected_configs - callback_configs}"

        # Verify all configs in cache
        for i in range(num_configs):
            name = f"concurrent_{i}"
            cached = self.tracking_agent.vip.config.get(name)
            assert cached["index"] == i, f"Config {name} should have correct index"

    def test_mixed_defaults_and_server_updates(self):
        """Test update flows with both defaults and server configs."""
        agent_id = f"mixed_agent_{self.test_id}"
        config_name = "mixed_config"

        # Create tracking agent
        self.tracking_agent = self.manager.create_connected_agent(agent_id, agent_class=CallbackTrackingAgent)

        # Set default
        default = {"timeout": 30, "retries": 3, "url": "http://default.com", "features": ["basic"]}
        self.tracking_agent.vip.config.set_default(config_name, default)

        # Subscribe
        self.tracking_agent.vip.config.subscribe(self.tracking_agent.on_config_update, config_name=config_name)

        # Set server config (partial override)
        server = {"timeout": 60, "url": "http://production.com", "new_field": "added"}
        result = self.tracking_agent.vip.config.set(config_name, server)
        result.get(timeout=5)

        gevent.sleep(1)

        # Verify merged config in cache
        cached = self.tracking_agent.vip.config.get(config_name)
        assert cached["timeout"] == 60  # From server
        assert cached["url"] == "http://production.com"  # From server
        assert cached["retries"] == 3  # From default
        assert cached["features"] == ["basic"]  # From default
        assert cached["new_field"] == "added"  # From server

        # External update
        self.tracking_agent.clear_history()

        external_update = {"timeout": 90, "external": True}
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=external_update, timeout=5)
        assert response.status_code == 200

        gevent.sleep(3)

        # Verify updated merged config
        final = self.tracking_agent.vip.config.get(config_name)
        assert final["timeout"] == 90  # From external
        assert final["external"] is True  # From external
        assert final["retries"] == 3  # Still from default
        assert final["features"] == ["basic"]  # Still from default

        # Verify callback
        assert len(self.tracking_agent.callback_history) > 0
        last = self.tracking_agent.callback_history[-1]
        assert last["contents"] == final
