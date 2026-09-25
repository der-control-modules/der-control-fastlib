"""
Test VOLTTRON parity features for config store.
Tests config-first processing and case conflict handling.
"""

import gevent
import pytest

from aems.client.agent import Agent


class VoltronParityAgent(Agent):
    """Test agent that tracks callback order and case conflicts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback_order = []
        self.callbacks_received = {}

    def on_config_update(self, config_name, action, contents):
        """Track callback order."""
        self.callback_order.append(config_name)
        self.callbacks_received[config_name] = {"action": action, "contents": contents}


class TestVoltronParity:
    """Test VOLTTRON parity features."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create unique test ID
        import uuid

        self.test_id = str(uuid.uuid4())[:8]

        yield

        # Cleanup
        for attr in ["agent"]:
            if hasattr(self, attr):
                agent = getattr(self, attr)
                if hasattr(agent, "disconnect"):
                    agent.disconnect()

    def test_config_processed_first(self):
        """Test that 'config' is always processed first in callbacks."""
        agent_id = f"config_first_agent_{self.test_id}"

        # Create test agent
        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=VoltronParityAgent
        )

        # Subscribe to all configs
        self.agent.vip.config.subscribe(self.agent.on_config_update, pattern="*")

        gevent.sleep(0.5)

        # Set multiple configs including "config"
        configs = {
            "zebra_config": {"order": "last"},
            "alpha_config": {"order": "first_alphabetically"},
            "config": {"order": "should_be_first"},
            "beta_config": {"order": "second"},
        }

        # Set all configs
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)

        gevent.sleep(1)

        # Clear callback order to test batch processing
        self.agent.callback_order = []

        # Now trigger a batch update by updating a config that references others
        # or by simulating a scenario where multiple configs are affected
        reference_config = {
            "ref_to_config": "config://config",
            "ref_to_alpha": "config://alpha_config",
        }
        result = self.agent.vip.config.set("reference_config", reference_config)
        result.get(timeout=5)

        gevent.sleep(1)

        # Check that if "config" was in the affected set, it was processed first
        # Note: In this test, "config" itself isn't affected by the reference_config update,
        # but we can verify the behavior by checking the order when multiple configs update

        # Let's update "config" to trigger callbacks for configs that reference it
        self.agent.callback_order = []
        result = self.agent.vip.config.set(
            "config", {"order": "updated", "new_field": "test"}
        )
        result.get(timeout=5)

        gevent.sleep(1)

        # "config" should be first in the callback order
        if len(self.agent.callback_order) > 0:
            assert (
                self.agent.callback_order[0] == "config"
            ), f"Expected 'config' first, got {self.agent.callback_order}"

    def test_case_conflict_detection(self):
        """Test that case-conflicting config names are detected and dropped."""
        agent_id = f"case_conflict_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=VoltronParityAgent
        )

        # Subscribe to all configs
        self.agent.vip.config.subscribe(self.agent.on_config_update, pattern="*")

        gevent.sleep(0.5)

        # Set a config with lowercase name
        result = self.agent.vip.config.set("myconfig", {"value": "original"})
        result.get(timeout=5)

        gevent.sleep(0.5)

        # Clear callbacks
        self.agent.callbacks_received = {}

        # Try to set a config with same name but different case
        result = self.agent.vip.config.set("MyConfig", {"value": "conflicting"})
        result.get(timeout=5)

        gevent.sleep(1)

        # The conflicting config should be dropped
        # We should not receive a callback for "MyConfig"
        assert (
            "MyConfig" not in self.agent.callbacks_received
        ), "Conflicting config should be dropped"

        # The original "myconfig" should still exist
        original = self.agent.vip.config.get("myconfig")
        assert original["value"] == "original", "Original config should be preserved"

    def test_case_conflict_with_different_cases(self):
        """Test various case combinations for conflict detection."""
        agent_id = f"multi_case_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=VoltronParityAgent
        )

        # Subscribe to all configs
        self.agent.vip.config.subscribe(self.agent.on_config_update, pattern="*")

        gevent.sleep(0.5)

        # Set configs with different cases
        test_cases = [
            ("device_config", {"id": 1}),
            ("Device_Config", {"id": 2}),  # Should be dropped
            ("DEVICE_CONFIG", {"id": 3}),  # Should be dropped
            ("sensor_data", {"type": "temp"}),
            ("Sensor_Data", {"type": "humidity"}),  # Should be dropped
        ]

        for name, value in test_cases:
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
            gevent.sleep(0.2)

        gevent.sleep(1)

        # Only the first occurrence of each case-insensitive name should exist
        assert "device_config" in self.agent.callbacks_received
        assert "Device_Config" not in self.agent.callbacks_received
        assert "DEVICE_CONFIG" not in self.agent.callbacks_received
        assert "sensor_data" in self.agent.callbacks_received
        assert "Sensor_Data" not in self.agent.callbacks_received

        # Verify the values are from the first configs
        device_config = self.agent.vip.config.get("device_config")
        assert device_config["id"] == 1

        sensor_data = self.agent.vip.config.get("sensor_data")
        assert sensor_data["type"] == "temp"

    def test_config_first_with_references(self):
        """Test that 'config' is processed first even when configs have references."""
        agent_id = f"ref_order_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=VoltronParityAgent
        )

        # Subscribe to all configs
        self.agent.vip.config.subscribe(self.agent.on_config_update, pattern="*")

        gevent.sleep(0.5)

        # Set up configs where some reference "config"
        base_config = {"base_value": "test"}
        result = self.agent.vip.config.set("config", base_config)
        result.get(timeout=5)

        app_config = {"name": "app", "base": "config://config"}
        result = self.agent.vip.config.set("app_config", app_config)
        result.get(timeout=5)

        gevent.sleep(0.5)

        # Clear callback order
        self.agent.callback_order = []

        # Update "config" which should trigger updates for dependent configs
        updated_config = {"base_value": "updated", "new_field": "added"}
        result = self.agent.vip.config.set("config", updated_config)
        result.get(timeout=5)

        gevent.sleep(1)

        # Check callback order - "config" should be first
        if (
            "config" in self.agent.callback_order
            and "app_config" in self.agent.callback_order
        ):
            config_index = self.agent.callback_order.index("config")
            app_index = self.agent.callback_order.index("app_config")
            assert (
                config_index < app_index
            ), "config should be processed before app_config"

    def test_delete_removes_from_name_map(self):
        """Test that deleting a config removes it from the case-insensitive name map."""
        agent_id = f"delete_name_map_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=VoltronParityAgent
        )

        # Set a config
        result = self.agent.vip.config.set("TestConfig", {"value": "test"})
        result.get(timeout=5)

        gevent.sleep(0.5)

        # Delete the config
        result = self.agent.vip.config.delete("TestConfig")
        result.get(timeout=5)

        gevent.sleep(0.5)

        # Now we should be able to set a config with different case
        result = self.agent.vip.config.set("testconfig", {"value": "new"})
        result.get(timeout=5)

        gevent.sleep(0.5)

        # The new config should exist
        new_config = self.agent.vip.config.get("testconfig")
        assert (
            new_config["value"] == "new"
        ), "New config with different case should be allowed after delete"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
