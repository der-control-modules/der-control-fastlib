"""
Test pattern matching for config subscriptions (VOLTTRON parity).
Tests fnmatch-style patterns for config callbacks.
"""

import gevent
import pytest

from aems.client.agent import Agent


class PatternMatchingAgent(Agent):
    """Test agent that tracks callbacks from pattern-matched configs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callbacks_received = []
        self.wildcard_callbacks = []
        self.prefix_callbacks = []
        self.suffix_callbacks = []

    def on_any_config(self, config_name, action, contents):
        """Callback for any config (*)."""
        self.callbacks_received.append({
            "pattern": "*",
            "config_name": config_name,
            "action": action,
            "contents": contents
        })

    def on_wildcard_config(self, config_name, action, contents):
        """Callback for wildcard pattern."""
        self.wildcard_callbacks.append({
            "config_name": config_name,
            "action": action,
            "contents": contents
        })

    def on_prefix_config(self, config_name, action, contents):
        """Callback for prefix pattern."""
        self.prefix_callbacks.append({
            "config_name": config_name,
            "action": action,
            "contents": contents
        })

    def on_suffix_config(self, config_name, action, contents):
        """Callback for suffix pattern."""
        self.suffix_callbacks.append({
            "config_name": config_name,
            "action": action,
            "contents": contents
        })


class TestConfigPatternMatching:
    """Test pattern matching for config subscriptions."""

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

    def test_wildcard_all_configs(self):
        """Test * pattern matches all configs."""
        agent_id = f"wildcard_agent_{self.test_id}"
        
        # Create test agent
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe to all configs with *
        self.agent.vip.config.subscribe(
            self.agent.on_any_config,
            pattern="*"  # Should match any config name
        )
        
        gevent.sleep(0.5)
        
        # Set multiple configs
        configs = {
            "config1": {"value": 1},
            "config2": {"value": 2},
            "test_config": {"value": 3}
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Should have received callbacks for all configs
        assert len(self.agent.callbacks_received) == 3
        config_names = {cb["config_name"] for cb in self.agent.callbacks_received}
        assert config_names == {"config1", "config2", "test_config"}

    def test_prefix_pattern_matching(self):
        """Test prefix patterns like device_*."""
        agent_id = f"prefix_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe to configs starting with "device_"
        self.agent.vip.config.subscribe(
            self.agent.on_prefix_config,
            pattern="device_*"
        )
        
        gevent.sleep(0.5)
        
        # Set various configs
        configs = {
            "device_1": {"type": "sensor", "id": 1},
            "device_2": {"type": "actuator", "id": 2},
            "device_sensor": {"type": "sensor", "id": 3},
            "config_1": {"value": "not_device"},  # Should NOT match
            "my_device": {"value": "not_prefix"}  # Should NOT match
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Should only match device_* configs
        assert len(self.agent.prefix_callbacks) == 3
        matched_names = {cb["config_name"] for cb in self.agent.prefix_callbacks}
        assert matched_names == {"device_1", "device_2", "device_sensor"}

    def test_suffix_pattern_matching(self):
        """Test suffix patterns like *_config."""
        agent_id = f"suffix_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe to configs ending with "_config"
        self.agent.vip.config.subscribe(
            self.agent.on_suffix_config,
            pattern="*_config"
        )
        
        gevent.sleep(0.5)
        
        # Set various configs
        configs = {
            "main_config": {"section": "main"},
            "device_config": {"section": "device"},
            "test_config": {"section": "test"},
            "config_file": {"section": "not_suffix"},  # Should NOT match
            "configuration": {"section": "not_match"}   # Should NOT match
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Should only match *_config configs
        assert len(self.agent.suffix_callbacks) == 3
        matched_names = {cb["config_name"] for cb in self.agent.suffix_callbacks}
        assert matched_names == {"main_config", "device_config", "test_config"}

    def test_complex_wildcard_patterns(self):
        """Test complex patterns like device_?_config."""
        agent_id = f"complex_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe with pattern containing ? (single character wildcard)
        self.agent.vip.config.subscribe(
            self.agent.on_wildcard_config,
            pattern="device_?_config"
        )
        
        gevent.sleep(0.5)
        
        # Set various configs
        configs = {
            "device_1_config": {"id": 1},      # Matches
            "device_2_config": {"id": 2},      # Matches
            "device_a_config": {"id": "a"},    # Matches
            "device_10_config": {"id": 10},    # Should NOT match (two characters)
            "device_config": {"id": "none"},   # Should NOT match (no character)
            "device__config": {"id": "empty"}  # Should NOT match (no character between _)
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Should only match single character in the ? position
        assert len(self.agent.wildcard_callbacks) == 3
        matched_names = {cb["config_name"] for cb in self.agent.wildcard_callbacks}
        assert matched_names == {"device_1_config", "device_2_config", "device_a_config"}

    def test_multiple_pattern_subscriptions(self):
        """Test multiple patterns can coexist and trigger appropriately."""
        agent_id = f"multi_pattern_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe with multiple patterns
        self.agent.vip.config.subscribe(
            self.agent.on_prefix_config,
            pattern="sensor_*"
        )
        
        self.agent.vip.config.subscribe(
            self.agent.on_suffix_config,
            pattern="*_settings"
        )
        
        self.agent.vip.config.subscribe(
            self.agent.on_any_config,
            pattern="*"
        )
        
        gevent.sleep(0.5)
        
        # Set configs that match different patterns
        configs = {
            "sensor_1": {"type": "temperature"},     # Matches sensor_* and *
            "sensor_settings": {"interval": 60},     # Matches all three patterns
            "device_settings": {"enabled": True},    # Matches *_settings and *
            "random_config": {"value": 42}          # Matches only *
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Check each pattern got the right callbacks
        prefix_names = {cb["config_name"] for cb in self.agent.prefix_callbacks}
        assert prefix_names == {"sensor_1", "sensor_settings"}
        
        suffix_names = {cb["config_name"] for cb in self.agent.suffix_callbacks}
        assert suffix_names == {"sensor_settings", "device_settings"}
        
        all_names = {cb["config_name"] for cb in self.agent.callbacks_received}
        assert all_names == {"sensor_1", "sensor_settings", "device_settings", "random_config"}

    def test_pattern_with_actions_filter(self):
        """Test pattern matching combined with action filtering."""
        agent_id = f"action_filter_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Track UPDATE actions only for device_* configs
        update_callbacks = []
        def on_device_update(config_name, action, contents):
            if action == "UPDATE":
                update_callbacks.append({
                    "config_name": config_name,
                    "action": action
                })
        
        # Subscribe to device_* but only for UPDATE actions
        self.agent.vip.config.subscribe(
            on_device_update,
            pattern="device_*",
            actions=["UPDATE"]
        )
        
        gevent.sleep(0.5)
        
        # First set will be NEW
        result = self.agent.vip.config.set("device_1", {"value": "initial"})
        result.get(timeout=5)
        gevent.sleep(0.5)
        
        # This should not trigger callback (NEW action)
        assert len(update_callbacks) == 0
        
        # Second set will be UPDATE
        result = self.agent.vip.config.set("device_1", {"value": "updated"})
        result.get(timeout=5)
        gevent.sleep(0.5)
        
        # This should trigger callback (UPDATE action)
        assert len(update_callbacks) == 1
        assert update_callbacks[0]["action"] == "UPDATE"

    def test_case_insensitive_pattern_matching(self):
        """Test that pattern matching is case-insensitive for config names."""
        agent_id = f"case_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe with lowercase pattern
        self.agent.vip.config.subscribe(
            self.agent.on_prefix_config,
            pattern="device_*"
        )
        
        gevent.sleep(0.5)
        
        # Set configs with various cases
        configs = {
            "Device_1": {"id": 1},      # Mixed case
            "DEVICE_2": {"id": 2},      # Upper case
            "device_3": {"id": 3},      # Lower case
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # All should match despite case differences
        # Note: VOLTTRON converts config names to lowercase internally
        assert len(self.agent.prefix_callbacks) == 3

    def test_character_class_patterns(self):
        """Test patterns with character classes like [0-9]."""
        agent_id = f"charset_agent_{self.test_id}"
        
        self.agent = self.manager.create_connected_agent(agent_id, agent_class=PatternMatchingAgent)
        
        # Subscribe with character class pattern
        self.agent.vip.config.subscribe(
            self.agent.on_wildcard_config,
            pattern="sensor_[0-9]"  # Single digit sensors
        )
        
        gevent.sleep(0.5)
        
        # Set various configs
        configs = {
            "sensor_1": {"type": "temp"},     # Matches
            "sensor_5": {"type": "humidity"}, # Matches  
            "sensor_9": {"type": "pressure"}, # Matches
            "sensor_a": {"type": "invalid"},  # Should NOT match
            "sensor_10": {"type": "multi"},   # Should NOT match (two digits)
        }
        
        for name, value in configs.items():
            result = self.agent.vip.config.set(name, value)
            result.get(timeout=5)
        
        gevent.sleep(1)
        
        # Should only match single digit sensors
        assert len(self.agent.wildcard_callbacks) == 3
        matched_names = {cb["config_name"] for cb in self.agent.wildcard_callbacks}
        assert matched_names == {"sensor_1", "sensor_5", "sensor_9"}


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])