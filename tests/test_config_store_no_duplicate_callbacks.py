"""
Test to verify that the duplicate callback issue is fixed.

This test specifically verifies that:
1. Default configs don't trigger callbacks during initialization
2. Single config updates trigger exactly one callback
3. The Manager agent scenario doesn't create duplicate greenlets
"""

import threading
import time

import pytest

from tests.utils import create_test_agent, start_test_message_bus


class CallbackCounter:
    """Track callback invocations."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def create_callback(self, name):
        def callback(config_name, action, contents):
            with self.lock:
                self.calls.append(
                    {
                        "callback_name": name,
                        "config_name": config_name,
                        "action": action,
                        "contents": contents,
                        "timestamp": time.time(),
                    }
                )
                print(f"Callback {name} triggered: {config_name} - {action}")

        return callback

    def get_count(self, callback_name=None, config_name=None):
        with self.lock:
            filtered = self.calls
            if callback_name:
                filtered = [c for c in filtered if c["callback_name"] == callback_name]
            if config_name:
                filtered = [c for c in filtered if c["config_name"] == config_name]
            return len(filtered)


class TestNoDuplicateCallbacks:
    """Test that duplicate callbacks are not triggered."""

    @pytest.fixture
    def message_bus(self, tmp_path):
        """Start a test message bus."""
        bus, port = start_test_message_bus()
        yield bus
        bus.stop()

    def test_set_default_does_not_trigger_callbacks(self, message_bus):
        """
        Verify that set_default() does not trigger any callbacks.

        This is the core fix - defaults are fallbacks only.
        """
        counter = CallbackCounter()

        # Create agent (not connected yet)
        agent = create_test_agent("test_agent_no_callbacks")

        # Subscribe BEFORE setting defaults
        callback = counter.create_callback("test_callback")
        agent.vip.config.subscribe(callback, actions=["NEW", "UPDATE"], pattern="*")

        # Connect the agent
        agent.start()
        time.sleep(0.5)

        # Set default config - this should NOT trigger callback
        agent.vip.config.set_default("config", {"test": "default_value"})

        # Wait to ensure no callbacks
        time.sleep(1)

        # Verify NO callbacks were triggered
        assert (
            counter.get_count() == 0
        ), f"set_default() should not trigger callbacks, but got {counter.get_count()} calls"

        agent.stop()

    def test_single_update_single_callback(self, message_bus):
        """
        Verify that a single config update triggers exactly one callback.
        """
        counter = CallbackCounter()

        # Create and connect agent
        agent = create_test_agent("test_agent_single_callback")

        # Subscribe to updates
        callback = counter.create_callback("update_callback")
        agent.vip.config.subscribe(
            callback, actions=["NEW", "UPDATE"], pattern="test_config"
        )

        agent.start()
        time.sleep(0.5)

        # Make single update
        agent.vip.config.set("test_config", {"value": 42})

        # Wait for callback
        time.sleep(1)

        # Verify exactly one callback
        assert (
            counter.get_count(config_name="test_config") == 1
        ), f"Expected 1 callback, got {counter.get_count(config_name='test_config')}"

        agent.stop()

    def test_manager_scenario_no_duplicate_greenlets(self, message_bus):
        """
        Test the Manager agent scenario that was causing duplicate greenlets.

        This simulates the exact initialization that was problematic.
        """
        counter = CallbackCounter()

        # Create agent like Manager
        agent = create_test_agent("manager.test")

        # Set up callbacks like Manager does
        def update_default(config_name, action, contents):
            counter.create_callback("update_default")(config_name, action, contents)
            # Simulate what update_default does - spawns greenlets
            print(f"update_default called with {config_name}")

        # Subscribe to main config
        agent.vip.config.subscribe(
            update_default, actions=["NEW", "UPDATE"], pattern="config"
        )

        # Set default config like Manager
        default_config = {
            "setpoint_validate_frequency": 120,
            "occupancy_validate_frequency": 60,
        }
        agent.vip.config.set_default("config", default_config)

        # Connect (this is where the bug was triggering)
        agent.start()
        time.sleep(1)

        # Verify update_default was NOT called for the default config
        count = counter.get_count(callback_name="update_default")
        assert (
            counter.get_count(callback_name="update_default") == 0
        ), f"update_default should not be called for defaults, got {count} calls"

        # Now do an actual update
        agent.vip.config.set("config", {"new_setting": "value"})
        time.sleep(1)

        # Should have exactly one callback for the actual update
        assert (
            counter.get_count(callback_name="update_default") == 1
        ), f"Expected 1 callback for actual update, got {counter.get_count(callback_name='update_default')}"

        agent.stop()

    def test_multiple_agents_no_cross_callbacks(self, message_bus):
        """
        Verify that multiple agents don't trigger each other's callbacks.
        """
        counter1 = CallbackCounter()
        counter2 = CallbackCounter()

        # Create two agents
        agent1 = create_test_agent("agent1")
        agent2 = create_test_agent("agent2")

        # Set up callbacks
        callback1 = counter1.create_callback("agent1_callback")
        callback2 = counter2.create_callback("agent2_callback")

        agent1.vip.config.subscribe(callback1, pattern="*")
        agent2.vip.config.subscribe(callback2, pattern="*")

        agent1.start()
        agent2.start()
        time.sleep(0.5)

        # Each agent updates its own config
        agent1.vip.config.set("config", {"agent": 1})
        agent2.vip.config.set("config", {"agent": 2})
        time.sleep(1)

        # Verify isolation
        assert counter1.get_count() == 1, "Agent1 should only get its own callback"
        assert counter2.get_count() == 1, "Agent2 should only get its own callback"

        agent1.stop()
        agent2.stop()

    def test_rapid_updates_no_duplicates(self, message_bus):
        """
        Test that rapid config updates don't cause duplicate callbacks.
        """
        counter = CallbackCounter()

        agent = create_test_agent("rapid_test")
        callback = counter.create_callback("rapid_callback")
        agent.vip.config.subscribe(callback, pattern="rapid_config")

        agent.start()
        time.sleep(0.5)

        # Rapid updates
        update_count = 5
        for i in range(update_count):
            agent.vip.config.set("rapid_config", {"count": i})
            time.sleep(0.1)

        time.sleep(1)

        # Should have exactly the same number of callbacks as updates
        assert (
            counter.get_count(config_name="rapid_config") == update_count
        ), f"Expected {update_count} callbacks, got {counter.get_count(config_name='rapid_config')}"

        agent.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
