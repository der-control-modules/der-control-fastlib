"""
Integration tests for FastAPI config store implementation.

These tests run against actual FastAPI server and client to ensure
the complete system behaves like VOLTTRON.
"""

import multiprocessing

# Import the actual FastAPI implementation
import sys
import tempfile
import threading
import time

import httpx
import pytest

sys.path.insert(0, "/home/volttron/aems-lib-fastapi/src")

from aems.client.agent import Agent
from aems.server.fastapi_message_bus import FastAPIMessageBus


class ConfigCallbackMonitor:
    """Monitor and record all config callbacks for verification."""

    def __init__(self):
        self.callbacks: list[dict] = []
        self.lock = threading.Lock()
        self.callback_event = threading.Event()

    def record_callback(self, config_name: str, action: str, contents: any):
        """Record a callback invocation."""
        with self.lock:
            self.callbacks.append(
                {"config_name": config_name, "action": action, "contents": contents, "timestamp": time.time()}
            )
            self.callback_event.set()

    def wait_for_callbacks(self, count: int, timeout: float = 2.0) -> bool:
        """Wait for a specific number of callbacks."""
        start = time.time()
        while time.time() - start < timeout:
            with self.lock:
                if len(self.callbacks) >= count:
                    return True
            time.sleep(0.01)
        return False

    def get_callback_count(self, config_name: str | None = None) -> int:
        """Get count of callbacks, optionally filtered by config name."""
        with self.lock:
            if config_name:
                return len([c for c in self.callbacks if c["config_name"] == config_name])
            return len(self.callbacks)

    def clear(self):
        """Clear all recorded callbacks."""
        with self.lock:
            self.callbacks.clear()
            self.callback_event.clear()


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def fastapi_server(temp_config_dir):
    """Start a FastAPI server in a separate process."""

    def run_server():
        server = FastAPIMessageBus(
            host="127.0.0.1",
            port=8765,
            config_store_dir=temp_config_dir,  # Use non-standard port to avoid conflicts
        )
        import uvicorn

        uvicorn.run(server.app, host="127.0.0.1", port=8765)

    process = multiprocessing.Process(target=run_server)
    process.start()

    # Wait for server to start
    time.sleep(2)

    yield "http://127.0.0.1:8765"

    process.terminate()
    process.join()


class TestFastAPIConfigStoreIntegration:
    """Integration tests for FastAPI config store implementation."""

    def test_default_configs_do_not_trigger_callbacks(self, fastapi_server):
        """
        Test that set_default() does not trigger callbacks on initialization.

        This is the key test for the duplicate callback issue.
        """
        monitor = ConfigCallbackMonitor()

        # Create an agent with a default config
        agent = Agent(identity="test.agent.1", host="127.0.0.1", port=8765)

        # Set up callback BEFORE setting defaults
        def config_callback(config_name, action, contents):
            monitor.record_callback(config_name, action, contents)

        # Subscribe to config updates
        agent.vip.config.subscribe(config_callback, actions=["NEW", "UPDATE"], pattern="config")

        # Set default config - this should NOT trigger callback
        default_config = {"test_setting": "default_value", "test_number": 42}
        agent.vip.config.set_default("config", default_config)

        # Wait briefly to ensure no callbacks are triggered
        time.sleep(0.5)

        # Verify no callbacks were triggered
        assert monitor.get_callback_count() == 0, "set_default() should not trigger any callbacks"

        agent.stop()

    def test_initialization_only_triggers_callbacks_for_server_configs(self, fastapi_server):
        """
        Test that only configs existing on the server trigger callbacks during init.

        Default configs should not trigger callbacks during initialization.
        """
        monitor = ConfigCallbackMonitor()

        # First, create a config on the server
        with httpx.Client() as client:
            response = client.put(
                f"{fastapi_server}/config-store/test.agent.2/server_config", json={"server_setting": "server_value"}
            )
            assert response.status_code == 200

        # Now create agent and set defaults
        agent = Agent(identity="test.agent.2", host="127.0.0.1", port=8765)

        def config_callback(config_name, action, contents):
            monitor.record_callback(config_name, action, contents)

        # Set default configs BEFORE subscribing
        agent.vip.config.set_default("default_only_config", {"default": "value"})
        agent.vip.config.set_default("server_config", {"server_setting": "default_value"})

        # Subscribe to all configs
        agent.vip.config.subscribe(config_callback, actions=["NEW", "UPDATE"], pattern="*")

        # Connect the agent (this triggers initialization)
        agent.start()

        # Wait for initialization to complete
        time.sleep(1)

        # Check callbacks
        callbacks = monitor.callbacks

        # Should only have callback for server_config, not default_only_config
        assert monitor.get_callback_count("server_config") == 1, "Server config should trigger exactly one callback"
        assert monitor.get_callback_count("default_only_config") == 0, "Default-only config should not trigger callback"

        # Verify the server config value (not default value) was in callback
        server_callback = [c for c in callbacks if c["config_name"] == "server_config"][0]
        assert (
            server_callback["contents"]["server_setting"] == "server_value"
        ), "Callback should contain server value, not default"

        agent.stop()

    def test_single_update_triggers_single_callback(self, fastapi_server):
        """
        Test that a single config update triggers exactly one callback.

        This verifies the fix for duplicate callbacks.
        """
        monitor = ConfigCallbackMonitor()

        agent = Agent(identity="test.agent.3", host="127.0.0.1", port=8765)

        def config_callback(config_name, action, contents):
            monitor.record_callback(config_name, action, contents)
            # Simulate some work in the callback
            time.sleep(0.01)

        # Subscribe to config updates
        agent.vip.config.subscribe(config_callback, actions=["NEW", "UPDATE"], pattern="test_config")

        agent.start()
        time.sleep(0.5)  # Let agent fully connect

        # Update the config once
        test_data = {"update_test": "single_update"}
        agent.vip.config.set("test_config", test_data)

        # Wait for callback
        time.sleep(1)

        # Verify exactly one callback
        assert monitor.get_callback_count("test_config") == 1, "Single update should trigger exactly one callback"

        # Verify callback contents
        callback = monitor.callbacks[0]
        assert callback["config_name"] == "test_config"
        assert callback["action"] == "UPDATE"
        assert callback["contents"] == test_data

        agent.stop()

    def test_multiple_rapid_updates(self, fastapi_server):
        """
        Test that multiple rapid updates each trigger exactly one callback.

        This tests for race conditions and duplicate callbacks.
        """
        monitor = ConfigCallbackMonitor()

        agent = Agent(identity="test.agent.4", host="127.0.0.1", port=8765)

        update_count = 5

        def config_callback(config_name, action, contents):
            monitor.record_callback(config_name, action, contents)

        agent.vip.config.subscribe(config_callback, actions=["UPDATE"], pattern="rapid_config")

        agent.start()
        time.sleep(0.5)

        # Perform rapid updates
        for i in range(update_count):
            agent.vip.config.set("rapid_config", {"count": i})
            time.sleep(0.05)  # Small delay between updates

        # Wait for all callbacks
        time.sleep(1)

        # Verify correct number of callbacks
        assert (
            monitor.get_callback_count("rapid_config") == update_count
        ), f"Should have exactly {update_count} callbacks for {update_count} updates"

        # Verify each update was received in order
        for i, callback in enumerate(monitor.callbacks):
            assert callback["contents"]["count"] == i, "Updates should be received in order"

        agent.stop()

    def test_send_update_flag_controls_callbacks(self, fastapi_server):
        """
        Test that send_update=False prevents callbacks from being triggered.

        This is critical for avoiding infinite loops.
        """
        monitor = ConfigCallbackMonitor()

        agent = Agent(identity="test.agent.5", host="127.0.0.1", port=8765)

        def config_callback(config_name, action, contents):
            monitor.record_callback(config_name, action, contents)

        agent.vip.config.subscribe(config_callback, actions=["UPDATE"], pattern="*")

        agent.start()
        time.sleep(0.5)

        # Update with send_update=True (should trigger callback)
        agent.vip.config.set("config1", {"test": 1}, send_update=True)
        time.sleep(0.5)

        assert monitor.get_callback_count("config1") == 1, "send_update=True should trigger callback"

        # Update with send_update=False (should NOT trigger callback)
        agent.vip.config.set("config2", {"test": 2}, send_update=False)
        time.sleep(0.5)

        assert monitor.get_callback_count("config2") == 0, "send_update=False should not trigger callback"

        agent.stop()

    def test_pattern_matching_subscriptions(self, fastapi_server):
        """
        Test that subscription patterns work correctly.
        """
        monitor = ConfigCallbackMonitor()

        agent = Agent(identity="test.agent.6", host="127.0.0.1", port=8765)

        # Different callbacks for different patterns
        def all_callback(name, action, contents):
            monitor.record_callback(f"all_{name}", action, contents)

        def device_callback(name, action, contents):
            monitor.record_callback(f"device_{name}", action, contents)

        def specific_callback(name, action, contents):
            monitor.record_callback(f"specific_{name}", action, contents)

        # Subscribe with different patterns
        agent.vip.config.subscribe(all_callback, pattern="*")
        agent.vip.config.subscribe(device_callback, pattern="device.*")
        agent.vip.config.subscribe(specific_callback, pattern="config")

        agent.start()
        time.sleep(0.5)

        # Test various config names
        test_configs = [
            ("config", ["all", "specific"]),  # Matches * and config
            ("device.rtu1", ["all", "device"]),  # Matches * and device.*
            ("other", ["all"]),  # Matches only *
        ]

        for config_name, expected_patterns in test_configs:
            monitor.clear()
            agent.vip.config.set(config_name, {"test": "data"})
            time.sleep(0.5)

            # Check which callbacks were triggered
            for pattern in expected_patterns:
                callback_name = f"{pattern}_{config_name}"
                assert monitor.get_callback_count(callback_name) == 1, f"Pattern {pattern} should match {config_name}"

        agent.stop()

    def test_callback_exception_isolation(self, fastapi_server):
        """
        Test that an exception in one callback doesn't affect others.
        """
        monitor = ConfigCallbackMonitor()

        agent = Agent(identity="test.agent.7", host="127.0.0.1", port=8765)

        def failing_callback(name, action, contents):
            raise RuntimeError("Intentional failure")

        def working_callback(name, action, contents):
            monitor.record_callback(name, action, contents)

        # Register both callbacks
        agent.vip.config.subscribe(failing_callback, pattern="config")
        agent.vip.config.subscribe(working_callback, pattern="config")

        agent.start()
        time.sleep(0.5)

        # Update config (failing callback should not prevent working one)
        agent.vip.config.set("config", {"test": "data"})
        time.sleep(0.5)

        # Verify working callback was still executed
        assert monitor.get_callback_count("config") == 1, "Working callback should execute despite failing callback"

        agent.stop()

    def test_concurrent_agents_config_isolation(self, fastapi_server):
        """
        Test that configs are properly isolated between agents.
        """
        monitor1 = ConfigCallbackMonitor()
        monitor2 = ConfigCallbackMonitor()

        agent1 = Agent(identity="test.agent.8a", host="127.0.0.1", port=8765)
        agent2 = Agent(identity="test.agent.8b", host="127.0.0.1", port=8765)

        def callback1(name, action, contents):
            monitor1.record_callback(name, action, contents)

        def callback2(name, action, contents):
            monitor2.record_callback(name, action, contents)

        agent1.vip.config.subscribe(callback1, pattern="*")
        agent2.vip.config.subscribe(callback2, pattern="*")

        agent1.start()
        agent2.start()
        time.sleep(0.5)

        # Each agent updates its own config
        agent1.vip.config.set("config", {"agent": "1"})
        agent2.vip.config.set("config", {"agent": "2"})
        time.sleep(1)

        # Verify each agent only got its own callbacks
        assert monitor1.get_callback_count() == 1, "Agent1 should only get its own callbacks"
        assert monitor2.get_callback_count() == 1, "Agent2 should only get its own callbacks"

        assert monitor1.callbacks[0]["contents"]["agent"] == "1"
        assert monitor2.callbacks[0]["contents"]["agent"] == "2"

        agent1.stop()
        agent2.stop()


class TestManagerAgentScenario:
    """
    Test the specific scenario from the Manager agent that revealed the bug.

    This simulates the exact initialization sequence that causes duplicate
    greenlets and callbacks.
    """

    def test_manager_initialization_sequence(self, fastapi_server):
        """
        Simulate the Manager agent's initialization to verify no duplicate callbacks.
        """
        monitor = ConfigCallbackMonitor()
        callback_counts = {}

        # Simulate the Manager agent
        agent = Agent(identity="manager.rtu02", host="127.0.0.1", port=8765)

        # Track callbacks by config name
        def track_callback(config_name):
            def callback(name, action, contents):
                monitor.record_callback(name, action, contents)
                callback_counts[name] = callback_counts.get(name, 0) + 1

            return callback

        # Simulate Manager's config references
        config_references = {
            "schedule": track_callback("schedule"),
            "temperature_setpoints": track_callback("temperature_setpoints"),
            "holidays": track_callback("holidays"),
            "optimal_start": track_callback("optimal_start"),
            "occupancy_overrides": track_callback("occupancy_overrides"),
            "location": track_callback("location"),
            "control": track_callback("control"),
        }

        # Subscribe to main config
        def update_default_callback(name, action, contents):
            monitor.record_callback(f"default_{name}", action, contents)
            # Simulate update_default_config being called
            callback_counts[f"default_{name}"] = callback_counts.get(f"default_{name}", 0) + 1

        agent.vip.config.subscribe(update_default_callback, actions=["NEW", "UPDATE"], pattern="config")

        # Subscribe to each config reference
        for key, callback in config_references.items():
            agent.vip.config.subscribe(callback, actions=["NEW", "UPDATE"], pattern=key)

        # Set default config (like Manager does)
        default_config = {
            "system": "RTU02",
            "campus": "PNNL",
            "building": "ROB",
            "setpoint_validate_frequency": 120,
            "occupancy_validate_frequency": 60,
        }

        agent.vip.config.set_default("config", default_config)

        # Start the agent (triggers initialization)
        agent.start()
        time.sleep(1)

        # Simulate setting configs like Manager does
        agent.vip.config.set(
            "set_points",
            {"OccupiedSetPoint": 76, "DeadBand": 1, "UnoccupiedCoolingSetPoint": 82, "UnoccupiedHeatingSetPoint": 70.5},
        )

        time.sleep(1)

        # Verify no duplicate callbacks
        for config_name, count in callback_counts.items():
            assert count <= 1, f"Config {config_name} triggered {count} callbacks, expected at most 1"

        agent.stop()


if __name__ == "__main__":
    # Run with pytest
    pytest.main([__file__, "-v", "--tb=short", "-s"])
