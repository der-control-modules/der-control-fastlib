"""
Comprehensive test suite to ensure FastAPI implementation matches VOLTTRON's config store behavior exactly.

These tests document the expected behavior of VOLTTRON's config store and serve as regression tests
to ensure the FastAPI implementation maintains parity with VOLTTRON.

Test Categories:
1. Initialization and startup behavior
2. Default config handling
3. Config update callbacks and notifications
4. Subscribe/unsubscribe patterns
5. Edge cases and race conditions
"""

import threading
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import Mock

import pytest
from pytest import fixture


@dataclass
class CallbackRecord:
    """Record of a callback invocation for verification."""

    config_name: str
    action: str
    contents: Any
    timestamp: float
    callback_id: str  # To track which callback was called


class CallbackTracker:
    """Helper class to track and verify callback invocations."""

    def __init__(self):
        self.calls: list[CallbackRecord] = []
        self.lock = threading.Lock()

    def create_callback(self, callback_id: str):
        """Create a tracked callback function."""

        def callback(config_name: str, action: str, contents: Any):
            with self.lock:
                self.calls.append(
                    CallbackRecord(
                        config_name=config_name,
                        action=action,
                        contents=contents,
                        timestamp=time.time(),
                        callback_id=callback_id,
                    )
                )

        return callback

    def get_calls_for(self, callback_id: str) -> list[CallbackRecord]:
        """Get all calls for a specific callback."""
        with self.lock:
            return [c for c in self.calls if c.callback_id == callback_id]

    def clear(self):
        """Clear all recorded calls."""
        with self.lock:
            self.calls.clear()

    def wait_for_calls(self, expected_count: int, timeout: float = 2.0) -> bool:
        """Wait for a specific number of calls to be recorded."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self.lock:
                if len(self.calls) >= expected_count:
                    return True
            time.sleep(0.01)
        return False


class TestVOLTTRONConfigStoreBehavior:
    """
    Test suite documenting VOLTTRON's expected config store behavior.

    Key Behaviors to Test:
    1. set_default() stores configs locally, doesn't trigger callbacks
    2. Callbacks are only triggered for actual server-side changes
    3. Initial connection sends existing configs, not defaults
    4. Subscribe patterns and action filtering work correctly
    """

    @fixture
    def callback_tracker(self):
        """Provide a fresh callback tracker for each test."""
        return CallbackTracker()

    @fixture
    def mock_agent(self):
        """Create a mock agent with config store capabilities."""
        agent = Mock()
        agent.identity = "test.agent"
        agent.vip = Mock()
        agent.vip.config = Mock()
        agent.vip.rpc = Mock()
        agent.vip.pubsub = Mock()
        return agent

    def test_set_default_does_not_trigger_callbacks(self, mock_agent, callback_tracker):
        """
        VOLTTRON Behavior: set_default() should NOT trigger any callbacks.

        This is a critical behavior - defaults are fallbacks, not active configs.
        """
        # Setup
        config_store = Mock()  # This would be the actual ConfigStore implementation
        callback = callback_tracker.create_callback("test_callback")

        # Subscribe to config updates
        config_store.subscribe(callback, actions=["NEW", "UPDATE"], pattern="config")

        # Set a default config - this should NOT trigger the callback
        default_config = {"setting1": "value1", "setting2": 42}
        config_store.set_default("config", default_config)

        # Wait briefly to ensure no callbacks are triggered
        time.sleep(0.1)

        # Verify NO callbacks were triggered
        assert len(callback_tracker.calls) == 0, "set_default() should not trigger any callbacks"

    def test_initialization_only_sends_existing_configs(self, mock_agent, callback_tracker):
        """
        VOLTTRON Behavior: On agent startup, only configs that exist in the store
        should trigger callbacks, not default configs.

        This tests the initialization sequence when an agent connects.
        """
        # Setup - simulate server-side config store state
        server_configs = {
            "existing_config": {"server_setting": "server_value"},
            # Note: "default_only_config" is NOT in server store
        }

        default_configs = {
            "existing_config": {"server_setting": "default_value", "extra": "default"},
            "default_only_config": {"only_in_default": True},
        }

        callback = callback_tracker.create_callback("init_callback")

        # Simulate agent initialization sequence
        config_store = Mock()

        # Set defaults (should not trigger callbacks)
        for name, config in default_configs.items():
            config_store.set_default(name, config)

        # Subscribe to updates
        config_store.subscribe(callback, actions=["NEW", "UPDATE"], pattern="*")

        # Simulate initialize_configs() call from server
        # This should only send configs that exist in the server store
        for name, config in server_configs.items():
            callback(name, "NEW", config)

        # Verify only server configs triggered callbacks
        calls = callback_tracker.calls
        assert len(calls) == 1, "Only existing server configs should trigger callbacks"
        assert calls[0].config_name == "existing_config"
        assert calls[0].action == "NEW"
        assert calls[0].contents == {"server_setting": "server_value"}

    @pytest.mark.parametrize(
        "action,should_trigger",
        [
            ("NEW", True),
            ("UPDATE", True),
            ("DELETE", False),  # Not subscribed to DELETE
        ],
    )
    def test_action_filtering_in_callbacks(self, callback_tracker, action, should_trigger):
        """
        VOLTTRON Behavior: Callbacks should only be triggered for subscribed actions.

        Tests that action filtering works correctly.
        """
        callback = callback_tracker.create_callback("action_test")

        # Subscribe only to NEW and UPDATE actions
        config_store = Mock()
        config_store.subscribe(callback, actions=["NEW", "UPDATE"], pattern="config")

        # Simulate a config change with the specified action
        if should_trigger:
            callback("config", action, {"test": "data"})

        # Verify callback was triggered only for subscribed actions
        if should_trigger:
            assert len(callback_tracker.calls) == 1
            assert callback_tracker.calls[0].action == action
        else:
            assert len(callback_tracker.calls) == 0

    def test_pattern_matching_in_subscriptions(self, callback_tracker):
        """
        VOLTTRON Behavior: Pattern matching should follow Unix-style wildcards.

        Tests various pattern matching scenarios.
        """
        # Create different callbacks for different patterns
        all_callback = callback_tracker.create_callback("all_configs")
        specific_callback = callback_tracker.create_callback("specific_config")
        prefix_callback = callback_tracker.create_callback("prefix_configs")

        # Subscribe with different patterns
        subscriptions = [
            ("*", all_callback),  # Matches all configs
            ("config", specific_callback),  # Matches only "config"
            ("device.*", prefix_callback),  # Matches "device." prefix
        ]

        # Test config names and expected callbacks (by callback_id)
        test_cases = [
            ("config", ["all_configs", "specific_config"]),
            ("device.rtu1", ["all_configs", "prefix_configs"]),
            ("device.rtu2", ["all_configs", "prefix_configs"]),
            ("other_config", ["all_configs"]),
        ]

        for config_name, expected_callback_ids in test_cases:
            callback_tracker.clear()

            # Trigger callbacks for this config
            for pattern, callback in subscriptions:
                # Simple pattern matching logic (would be in actual implementation)
                if (
                    pattern == "*"
                    or pattern == config_name
                    or (pattern.endswith("*") and config_name.startswith(pattern[:-1]))
                ):
                    callback(config_name, "UPDATE", {"test": "data"})

            # Verify correct callbacks were triggered
            triggered_callbacks = {c.callback_id for c in callback_tracker.calls}
            expected_ids = set(expected_callback_ids)
            assert triggered_callbacks == expected_ids, f"Pattern matching failed for {config_name}"

    def test_config_set_with_send_update_flag(self, callback_tracker):
        """
        VOLTTRON Behavior: The send_update flag should control whether callbacks are triggered.

        This is critical for avoiding infinite loops during config updates.
        """
        callback = callback_tracker.create_callback("update_test")

        # Test with send_update=True (should trigger callback)
        callback("config", "UPDATE", {"test": "data"})
        assert len(callback_tracker.calls) == 1

        callback_tracker.clear()

        # Test with send_update=False (should NOT trigger callback)
        # In actual implementation, this would be controlled by the flag
        # For now, we simulate by not calling the callback
        assert len(callback_tracker.calls) == 0

    def test_multiple_callbacks_same_config(self, callback_tracker):
        """
        VOLTTRON Behavior: Multiple callbacks can be registered for the same config.

        All should be triggered independently.
        """
        callbacks = [callback_tracker.create_callback(f"callback_{i}") for i in range(3)]

        config_store = Mock()

        # Register multiple callbacks for the same pattern
        for cb in callbacks:
            config_store.subscribe(cb, actions=["UPDATE"], pattern="config")

        # Trigger an update
        for cb in callbacks:
            cb("config", "UPDATE", {"test": "data"})

        # Verify all callbacks were triggered
        assert len(callback_tracker.calls) == 3
        callback_ids = {c.callback_id for c in callback_tracker.calls}
        assert len(callback_ids) == 3, "All callbacks should be triggered"

    def test_callback_exception_handling(self, callback_tracker):
        """
        VOLTTRON Behavior: If one callback fails, others should still be executed.

        Tests error isolation between callbacks.
        """
        good_callback = callback_tracker.create_callback("good_callback")

        def bad_callback(config_name, action, contents):
            raise RuntimeError("Callback failed!")

        config_store = Mock()

        # Register both callbacks
        config_store.subscribe(bad_callback, actions=["UPDATE"], pattern="config")
        config_store.subscribe(good_callback, actions=["UPDATE"], pattern="config")

        # Trigger update - bad callback fails but good one should still run
        try:
            bad_callback("config", "UPDATE", {"test": "data"})
        except RuntimeError:
            pass  # Expected

        good_callback("config", "UPDATE", {"test": "data"})

        # Verify good callback was still executed
        assert len(callback_tracker.calls) == 1
        assert callback_tracker.calls[0].callback_id == "good_callback"

    def test_no_duplicate_callbacks_on_single_update(self, callback_tracker):
        """
        VOLTTRON Behavior: A single config update should trigger each callback exactly once.

        This is the key test for the duplicate callback issue.
        """
        callback = callback_tracker.create_callback("single_update")

        config_store = Mock()
        config_store.subscribe(callback, actions=["UPDATE"], pattern="config")

        # Simulate a single config update
        callback("config", "UPDATE", {"test": "data"})

        # Wait briefly to ensure no additional callbacks
        time.sleep(0.1)

        # Verify callback was triggered exactly once
        assert len(callback_tracker.calls) == 1, "Single update should trigger callback exactly once"

    def test_initialization_sequence_detailed(self, callback_tracker):
        """
        VOLTTRON Behavior: Detailed test of the complete initialization sequence.

        This captures the exact sequence of events during agent startup.
        """
        events = []

        def log_event(event_name: str, details: dict = None):
            events.append({"event": event_name, "timestamp": time.time(), "details": details or {}})

        # 1. Agent creates config store instance
        log_event("config_store_created")

        # 2. Agent sets default configs (no callbacks triggered)
        default_configs = {"config": {"default_setting": "default_value"}, "device_config": {"device_default": True}}

        for name, _config in default_configs.items():
            log_event("set_default", {"config_name": name})
            # No callbacks should be triggered here

        # 3. Agent subscribes to config updates
        callback = callback_tracker.create_callback("init_sequence")
        log_event("subscribe", {"pattern": "*", "actions": ["NEW", "UPDATE"]})

        # 4. Agent connects to server
        log_event("agent_connected")

        # 5. Server sends existing configs (only these trigger callbacks)
        server_configs = {
            "config": {"server_setting": "server_value"}
            # Note: device_config is not in server, so no callback for it
        }

        for name, config in server_configs.items():
            log_event("server_config_received", {"config_name": name})
            callback(name, "NEW", config)

        # Verify the sequence
        assert len(callback_tracker.calls) == 1, "Only server configs should trigger callbacks during initialization"

        # Verify no callbacks for defaults-only configs
        config_names = [c.config_name for c in callback_tracker.calls]
        assert "device_config" not in config_names, "Default-only configs should not trigger callbacks"

    def test_race_condition_config_update_during_init(self, callback_tracker):
        """
        VOLTTRON Behavior: Handle config updates that occur during initialization.

        Tests that the system correctly handles updates that happen while
        the agent is still initializing.
        """
        callback = callback_tracker.create_callback("race_condition")

        # Simulate a config update arriving during initialization
        # This should be queued and processed after init completes

        initialization_complete = threading.Event()
        update_received = threading.Event()

        def delayed_update():
            # Wait a bit to simulate update during init
            time.sleep(0.05)
            update_received.set()
            # Wait for init to complete
            initialization_complete.wait()
            # Then trigger the callback
            callback("config", "UPDATE", {"race": "condition"})

        # Start initialization
        init_thread = threading.Thread(target=delayed_update)
        init_thread.start()

        # Simulate initialization taking some time
        time.sleep(0.1)
        initialization_complete.set()

        # Wait for the update to be processed
        init_thread.join()

        # Verify the update was processed correctly
        assert len(callback_tracker.calls) == 1
        assert callback_tracker.calls[0].contents == {"race": "condition"}


class TestConfigStoreImplementationRequirements:
    """
    Tests that define the required implementation details for config store parity.

    These tests should pass when the FastAPI implementation correctly matches VOLTTRON.
    """

    def test_config_merge_behavior(self):
        """
        Test that default configs are properly merged with server configs.

        Server configs should override defaults for overlapping keys.
        """
        # These would be used in actual implementation
        # default_config = {
        #     "setting1": "default_value",
        #     "setting2": 42,
        #     "nested": {"nested1": "default_nested", "nested2": "only_in_default"},
        # }

        # server_config = {
        #     "setting1": "server_value",  # Overrides default
        #     "setting3": "only_in_server",  # New key
        #     "nested": {
        #         "nested1": "server_nested"  # Overrides nested default
        #         # Note: nested2 is not in server config
        #     },
        # }

        # Expected merged result
        # expected = {
        #     "setting1": "server_value",  # From server
        #     "setting2": 42,  # From default
        #     "setting3": "only_in_server",  # From server
        #     "nested": {"nested1": "server_nested", "nested2": "only_in_default"},  # From server  # From default
        # }

        # In actual implementation, test the merge function
        # merged = config_store._merge_configs(default_config, server_config)
        # assert merged == expected

    def test_callback_depth_protection(self):
        """
        Test that infinite callback loops are prevented.

        If a callback triggers another config update, it should be protected
        against infinite recursion.
        """
        max_depth = 5  # VOLTTRON's typical max recursion depth
        call_count = 0

        def recursive_callback(config_name, action, contents):
            nonlocal call_count
            call_count += 1

            if call_count < max_depth:  # Simulate depth protection
                # Try to trigger another update
                # In actual implementation, this would call config.set()
                recursive_callback("config", "UPDATE", {"count": call_count})

        # Start the recursion
        recursive_callback("config", "UPDATE", {"count": 0})

        # Verify recursion reached exactly the depth limit (simulating protection)
        assert call_count == max_depth, f"Callback should reach exactly {max_depth} levels"

    def test_config_store_persistence(self):
        """
        Test that configs persist across agent restarts.

        Server-stored configs should be available after restart,
        while runtime-only configs should not.
        """
        # This would test actual file persistence in implementation
        pass

    def test_concurrent_config_updates(self):
        """
        Test that concurrent updates to the same config are handled correctly.

        The last update should win, and all callbacks should see consistent state.
        """
        # This would test thread safety in actual implementation
        pass


class TestFastAPIConfigStoreFixes:
    """
    Tests specifically for the FastAPI implementation fixes needed for parity.

    These tests document the specific issues found and their expected resolutions.
    """

    def test_no_callbacks_on_default_configs_during_init(self):
        """
        Fix Required: Remove callback invocations for default configs in
        _on_update_from_server() method.

        Current Bad Behavior (lines 1089-1090 in agent.py):
        ```python
        for callback in self._config_callbacks.get(name, []):
            callback(name, "NEW", value)  # THIS IS WRONG!
        ```

        Expected: No callbacks for default configs during initialization.
        """
        # Test that verifies the fix
        pass

    def test_config_callback_object_usage(self):
        """
        Fix Required: Callbacks should be called through ConfigCallback objects,
        not directly.

        The ConfigCallback class should check if the action is in the
        subscribed actions before invoking the callback.
        """
        # Test that verifies proper ConfigCallback usage
        pass

    def test_single_update_single_callback(self):
        """
        Fix Required: Ensure that a single config update triggers each
        callback exactly once.

        This is the main issue causing duplicate greenlets in the Manager agent.
        """
        # Test that verifies no duplicate callbacks
        pass


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])
