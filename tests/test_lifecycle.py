#!/usr/bin/env python3
"""
Comprehensive test for agent lifecycle signals
"""
import gevent
import pytest

from aems.client.agent import Agent, Core


class LifecycleTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Track all lifecycle events
        self.lifecycle_events = []
        self.event_counts = {
            "onsetup": 0,
            "onconnected": 0,
            "onstart": 0,
            "onconfigure": 0,
            "onstop": 0,
            "ondisconnected": 0,
            "onfinish": 0,
        }

        # Register event handlers using decorators
        self._register_lifecycle_handlers()

    def _register_lifecycle_handlers(self):
        """Register all lifecycle event handlers."""

        # Register event handlers after core is created
        def register_handlers():
            self.core._handlers["onsetup"].append(self.on_setup)
            self.core._handlers["onconnected"].append(self.on_connected)
            self.core._handlers["onstart"].append(self.on_start)
            self.core._handlers["onconfigure"].append(self.on_configure)
            self.core._handlers["onstop"].append(self.on_stop)
            self.core._handlers["ondisconnected"].append(self.on_disconnected)
            self.core._handlers["onfinish"].append(self.on_finish)

        # Delay registration until after initialization
        gevent.spawn_later(0, register_handlers)

    def on_setup(self, sender, **kwargs):
        self.event_counts["onsetup"] += 1
        self.lifecycle_events.append("onsetup")
        print(f"ONSETUP called! Count: {self.event_counts['onsetup']}")

    def on_connected(self, sender, **kwargs):
        self.event_counts["onconnected"] += 1
        self.lifecycle_events.append("onconnected")
        print(f"ONCONNECTED called! Count: {self.event_counts['onconnected']}")

    def on_start(self, sender, **kwargs):
        self.event_counts["onstart"] += 1
        self.lifecycle_events.append("onstart")
        print(f"ONSTART called! Count: {self.event_counts['onstart']}")

    def on_configure(self, sender, **kwargs):
        self.event_counts["onconfigure"] += 1
        self.lifecycle_events.append("onconfigure")
        configs = kwargs.get("configs", [])
        print(
            f"ONCONFIGURE called! Count: {self.event_counts['onconfigure']}, configs: {len(configs)}"
        )

    def on_stop(self, sender, **kwargs):
        self.event_counts["onstop"] += 1
        self.lifecycle_events.append("onstop")
        print(f"ONSTOP called! Count: {self.event_counts['onstop']}")

    def on_disconnected(self, sender, **kwargs):
        self.event_counts["ondisconnected"] += 1
        self.lifecycle_events.append("ondisconnected")
        print(f"ONDISCONNECTED called! Count: {self.event_counts['ondisconnected']}")

    def on_finish(self, sender, **kwargs):
        self.event_counts["onfinish"] += 1
        self.lifecycle_events.append("onfinish")
        print(f"ONFINISH called! Count: {self.event_counts['onfinish']}")


def test_agent_lifecycle(message_bus):
    """Test the complete agent lifecycle and all its signals."""
    print("Testing agent lifecycle events...")

    # Create test agent
    agent = LifecycleTestAgent("lifecycle_test_agent", port=8888)

    try:
        print("\n=== Testing Connection Lifecycle ===")

        # Connect the agent
        print("1. Connecting agent...")
        agent.connect()

        # Wait for events to be processed
        gevent.sleep(2)

        print(f"Events fired during connection: {agent.lifecycle_events}")

        # Verify connection events
        assert (
            agent.event_counts["onconnected"] == 1
        ), f"Expected onconnected=1, got {agent.event_counts['onconnected']}"
        assert (
            agent.event_counts["onconfigure"] == 1
        ), f"Expected onconfigure=1, got {agent.event_counts['onconfigure']}"
        assert (
            agent.event_counts["onstart"] == 1
        ), f"Expected onstart=1, got {agent.event_counts['onstart']}"

        # Verify event order during connection
        expected_connection_order = ["onconnected", "onconfigure", "onstart"]
        actual_order = agent.lifecycle_events[:3]
        assert (
            actual_order == expected_connection_order
        ), f"Expected connection order {expected_connection_order}, got {actual_order}"

        print("✓ Connection lifecycle events fired correctly")

        print("\n=== Testing Disconnection Lifecycle ===")

        # Clear events list to focus on disconnect events
        agent.lifecycle_events.clear()

        # Disconnect the agent
        print("2. Disconnecting agent...")
        agent.disconnect()

        # Wait for events to be processed
        gevent.sleep(2)

        print(f"Events fired during disconnection: {agent.lifecycle_events}")

        # Verify disconnection events
        assert (
            agent.event_counts["onstop"] == 1
        ), f"Expected onstop=1, got {agent.event_counts['onstop']}"
        assert (
            agent.event_counts["ondisconnected"] == 1
        ), f"Expected ondisconnected=1, got {agent.event_counts['ondisconnected']}"

        print("✓ Disconnection lifecycle events fired correctly")

        print("\n=== Testing Core.stop() Lifecycle ===")

        # Test using core.stop() which should fire additional events
        agent.lifecycle_events.clear()

        print("3. Testing core.stop()...")
        try:
            result = agent.core.stop()
            if hasattr(result, "get"):
                result.get(timeout=5)
        except Exception as e:
            print(f"Note: core.stop() error (expected if already disconnected): {e}")

        # Wait for events
        gevent.sleep(1)

        print(f"Events fired during core.stop(): {agent.lifecycle_events}")

        # core.stop() should fire onfinish
        assert (
            agent.event_counts["onfinish"] >= 1
        ), f"Expected onfinish>=1, got {agent.event_counts['onfinish']}"

        print("✓ Core stop lifecycle events fired correctly")

        print("\n=== Final Event Summary ===")
        for event, count in agent.event_counts.items():
            print(f"  {event}: {count}")

        # Final assertions
        assert all(
            count >= 1
            for event, count in agent.event_counts.items()
            if event in ["onconnected", "onstart", "onconfigure"]
        ), "All connection events should have fired at least once"

        print("\n✅ SUCCESS: All agent lifecycle events fired correctly!")

    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        print(f"Event counts: {agent.event_counts}")
        print(f"Event sequence: {agent.lifecycle_events}")
        raise

    finally:
        # Ensure cleanup
        try:
            if hasattr(agent, "connected") and agent.connected:
                agent.disconnect()
        except:
            pass


def test_signal_decorators(message_bus):
    """Test using @Core.receiver decorators for lifecycle events."""
    print("\n=== Testing @Core.receiver Decorators ===")

    class DecoratorTestAgent(Agent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.decorator_events = []

        @Core.receiver("onstart")
        def handle_start(self, sender, **kwargs):
            self.decorator_events.append("decorator_onstart")
            print("Decorator onstart handler called!")

        @Core.receiver("onconnected")
        def handle_connected(self, sender, **kwargs):
            self.decorator_events.append("decorator_onconnected")
            print("Decorator onconnected handler called!")

    agent = DecoratorTestAgent("decorator_test_agent", port=8888)

    try:
        agent.connect()
        gevent.sleep(2)

        # Verify decorator handlers were called
        assert "decorator_onstart" in agent.decorator_events, "Decorator onstart handler not called"
        assert (
            "decorator_onconnected" in agent.decorator_events
        ), "Decorator onconnected handler not called"

        print("✓ @Core.receiver decorators work correctly")

    finally:
        if agent.connected:
            agent.disconnect()


def test_onstart_called_once(message_bus):
    """Test that onstart is only called once during agent lifecycle (original test)."""
    print("\n=== Testing onstart is called exactly once ===")

    class OnstartTestAgent(Agent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.onstart_count = 0
            # Register handler
            self.core._handlers["onstart"].append(self.on_onstart)

        def on_onstart(self, sender, **kwargs):
            self.onstart_count += 1
            print(f"ONSTART CALLED! Count: {self.onstart_count}")

    agent = OnstartTestAgent("onstart_test_agent", port=8888)

    try:
        agent.connect()
        gevent.sleep(2)

        # Check that onstart was called exactly once
        assert agent.onstart_count == 1, f"Expected onstart=1, got {agent.onstart_count}"
        print("✓ onstart was called exactly once!")

    finally:
        if agent.connected:
            agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
