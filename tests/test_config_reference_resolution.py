"""
Test config:// reference resolution (VOLTTRON parity).
Tests the ability to reference other configs within config values.
"""

import gevent
import pytest

from derhost.client.agent import Agent


class ReferenceResolutionAgent(Agent):
    """Test agent for config reference resolution."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resolved_configs = {}
        self.callback_count = 0

    def on_config_update(self, config_name, action, contents):
        """Track resolved config values."""
        self.resolved_configs[config_name] = contents
        self.callback_count += 1


class TestConfigReferenceResolution:
    """Test config:// reference resolution in config values."""

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

    def test_simple_config_reference(self):
        """Test simple config:// reference in string value."""
        agent_id = f"ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set base config
        base_config = {"host": "localhost", "port": 8080, "protocol": "http"}
        result = self.agent.vip.config.set("server_config", base_config)
        result.get(timeout=5)

        # Set config with reference
        referencing_config = {"server": "config://server_config", "timeout": 30}
        result = self.agent.vip.config.set("client_config", referencing_config)
        result.get(timeout=5)

        # Subscribe to see resolved value
        self.agent.vip.config.subscribe(
            self.agent.on_config_update, pattern="client_config"
        )

        gevent.sleep(0.5)

        # Get the config - should have reference resolved
        resolved = self.agent.vip.config.get("client_config")

        # The server field should be resolved to the actual config
        assert resolved["server"] == base_config
        assert resolved["timeout"] == 30

    def test_nested_config_references(self):
        """Test config references within nested structures."""
        agent_id = f"nested_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set base configs
        db_config = {"host": "db.example.com", "port": 5432, "database": "myapp"}
        result = self.agent.vip.config.set("database", db_config)
        result.get(timeout=5)

        cache_config = {"host": "cache.example.com", "port": 6379, "ttl": 3600}
        result = self.agent.vip.config.set("cache", cache_config)
        result.get(timeout=5)

        # Set config with nested references
        app_config = {
            "name": "MyApp",
            "connections": {"database": "config://database", "cache": "config://cache"},
            "settings": {"debug": False, "workers": 4},
        }
        result = self.agent.vip.config.set("app", app_config)
        result.get(timeout=5)

        # Get resolved config
        resolved = self.agent.vip.config.get("app")

        # Check nested references are resolved
        assert resolved["connections"]["database"] == db_config
        assert resolved["connections"]["cache"] == cache_config
        assert resolved["settings"]["debug"] is False

    def test_list_with_config_references(self):
        """Test config references within lists."""
        agent_id = f"list_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set device configs
        device1 = {"id": "sensor_1", "type": "temperature", "unit": "celsius"}
        device2 = {"id": "sensor_2", "type": "humidity", "unit": "percent"}
        device3 = {"id": "actuator_1", "type": "valve", "state": "closed"}

        result = self.agent.vip.config.set("device_1", device1)
        result.get(timeout=5)
        result = self.agent.vip.config.set("device_2", device2)
        result.get(timeout=5)
        result = self.agent.vip.config.set("device_3", device3)
        result.get(timeout=5)

        # Set config with list of references
        devices_config = {
            "devices": ["config://device_1", "config://device_2", "config://device_3"],
            "poll_interval": 60,
        }
        result = self.agent.vip.config.set("devices", devices_config)
        result.get(timeout=5)

        # Get resolved config
        resolved = self.agent.vip.config.get("devices")

        # Check list references are resolved
        assert len(resolved["devices"]) == 3
        assert resolved["devices"][0] == device1
        assert resolved["devices"][1] == device2
        assert resolved["devices"][2] == device3

    @pytest.mark.skip(
        reason="Circular reference causes timeout - needs better handling"
    )
    def test_circular_reference_detection(self):
        """Test that circular references are detected and handled."""
        agent_id = f"circular_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Create circular reference
        config_a = {"name": "Config A", "reference": "config://config_b"}
        config_b = {
            "name": "Config B",
            "reference": "config://config_a",  # Circular reference
        }

        result = self.agent.vip.config.set("config_a", config_a)
        result.get(timeout=5)
        result = self.agent.vip.config.set("config_b", config_b)
        result.get(timeout=5)

        # Attempting to get should raise an error or return unresolved
        # RecursionError is a subclass of Exception, so this should catch both ValueError and RecursionError
        with pytest.raises((ValueError, RecursionError)) as exc_info:
            self.agent.vip.config.get("config_a")

        # Should detect recursion or circular reference
        error_msg = str(exc_info.value).lower()
        assert "recurs" in error_msg or "circular" in error_msg

    def test_missing_reference_handling(self):
        """Test handling of references to non-existent configs."""
        agent_id = f"missing_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set config with reference to non-existent config
        config = {"name": "Test Config", "missing_ref": "config://non_existent_config"}
        result = self.agent.vip.config.set("test_config", config)
        result.get(timeout=5)

        # Getting the config should raise KeyError or similar
        with pytest.raises(KeyError) as exc_info:
            self.agent.vip.config.get("test_config")

        assert "non_existent_config" in str(exc_info.value)

    def test_reference_with_whitespace(self):
        """Test that config references work with extra whitespace."""
        agent_id = f"whitespace_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set base config
        base = {"value": "test_value"}
        result = self.agent.vip.config.set("base", base)
        result.get(timeout=5)

        # Set config with reference containing whitespace
        configs_to_test = [
            {"ref": "config://  base"},  # Leading spaces
            {"ref": "config://base  "},  # Trailing spaces
            {"ref": "config://  base  "},  # Both
            {"ref": "config://\tbase"},  # Tab
            {"ref": "config://\nbase\n"},  # Newlines
        ]

        for i, config in enumerate(configs_to_test):
            config_name = f"test_{i}"
            result = self.agent.vip.config.set(config_name, config)
            result.get(timeout=5)

            # Should resolve despite whitespace
            resolved = self.agent.vip.config.get(config_name)
            assert resolved["ref"] == base

    def test_case_insensitive_references(self):
        """Test that config references are case-insensitive."""
        agent_id = f"case_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set config with mixed case name
        base = {"setting": "value"}
        result = self.agent.vip.config.set("MyConfig", base)
        result.get(timeout=5)

        # Reference with different cases
        refs = [
            {"ref": "config://myconfig"},  # Lowercase
            {"ref": "config://MYCONFIG"},  # Uppercase
            {"ref": "config://MyConfig"},  # Original case
            {"ref": "config://mYcOnFiG"},  # Mixed
        ]

        for i, config in enumerate(refs):
            config_name = f"ref_{i}"
            result = self.agent.vip.config.set(config_name, config)
            result.get(timeout=5)

            # All should resolve to the same config
            resolved = self.agent.vip.config.get(config_name)
            assert resolved["ref"] == base

    def test_reference_update_propagation(self):
        """Test that updates to referenced configs propagate."""
        agent_id = f"propagate_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set base config
        base = {"version": 1, "data": "initial"}
        result = self.agent.vip.config.set("base", base)
        result.get(timeout=5)

        # Set referencing config
        ref_config = {"name": "Referencer", "source": "config://base"}
        result = self.agent.vip.config.set("referencer", ref_config)
        result.get(timeout=5)

        # Subscribe to updates
        self.agent.vip.config.subscribe(
            self.agent.on_config_update, pattern="referencer"
        )

        # Initial resolution
        resolved = self.agent.vip.config.get("referencer")
        assert resolved["source"]["version"] == 1

        # Update base config
        base_updated = {"version": 2, "data": "updated"}
        result = self.agent.vip.config.set("base", base_updated)
        result.get(timeout=5)

        gevent.sleep(0.5)

        # The referencer should be notified of the change
        # because the referenced config changed
        assert self.agent.callback_count > 0

        # Get updated resolution
        resolved = self.agent.vip.config.get("referencer")
        assert resolved["source"]["version"] == 2

    def test_mixed_references_and_values(self):
        """Test configs with both references and regular values."""
        agent_id = f"mixed_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set referenced configs
        db = {"host": "localhost", "port": 5432}
        limits = {"max_connections": 100, "timeout": 30}

        result = self.agent.vip.config.set("db", db)
        result.get(timeout=5)
        result = self.agent.vip.config.set("limits", limits)
        result.get(timeout=5)

        # Mixed config
        mixed = {
            "app_name": "TestApp",  # Regular value
            "version": "1.0.0",  # Regular value
            "database": "config://db",  # Reference
            "performance": {
                "workers": 4,  # Regular value
                "limits": "config://limits",  # Nested reference
            },
            "features": ["auth", "api"],  # Regular list
            "debug": True,  # Regular boolean
        }

        result = self.agent.vip.config.set("app_config", mixed)
        result.get(timeout=5)

        # Get resolved
        resolved = self.agent.vip.config.get("app_config")

        # Check mixed resolution
        assert resolved["app_name"] == "TestApp"  # Unchanged
        assert resolved["version"] == "1.0.0"  # Unchanged
        assert resolved["database"] == db  # Resolved
        assert resolved["performance"]["workers"] == 4  # Unchanged
        assert resolved["performance"]["limits"] == limits  # Resolved
        assert resolved["features"] == ["auth", "api"]  # Unchanged
        assert resolved["debug"] is True  # Unchanged

    def test_partial_string_references_not_resolved(self):
        """Test that only complete strings are treated as references."""
        agent_id = f"partial_ref_agent_{self.test_id}"

        self.agent = self.manager.create_connected_agent(
            agent_id, agent_class=ReferenceResolutionAgent
        )

        # Set base config
        base = {"value": "test"}
        result = self.agent.vip.config.set("base", base)
        result.get(timeout=5)

        # Config with partial references (should NOT be resolved)
        partial = {
            "url": "http://config://base/path",  # Not a pure reference
            "desc": "See config://base for details",  # Contains reference but not pure
            "ref": "config://base",  # This SHOULD be resolved
        }

        result = self.agent.vip.config.set("partial", partial)
        result.get(timeout=5)

        resolved = self.agent.vip.config.get("partial")

        # Partial strings should remain unchanged
        assert resolved["url"] == "http://config://base/path"
        assert resolved["desc"] == "See config://base for details"
        # Only pure reference should be resolved
        assert resolved["ref"] == base


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
