#!/usr/bin/env python3
"""
Comprehensive test for agent lifecycle signals
"""

import gevent
import pytest

from derhost.client.agent import Agent, Core


class LifecycleTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Track all lifecycle events
        self.lifecycle_events = []
        self.event_counts = {
            "onconnected": 0,
            "onstart": 0,
            "onconfigure": 0,
            "onstop": 0,
            "ondisconnected": 0,
            "onfinish": 0,
        }

    @Core.receiver("onconnected")
    def on_connected(self, sender, **kwargs):
        self.event_counts["onconnected"] += 1
        self.lifecycle_events.append("onconnected")
        print(f"ONCONNECTED called! Count: {self.event_counts['onconnected']}")

    @Core.receiver("onstart")
    def on_start(self, sender, **kwargs):
        self.event_counts["onstart"] += 1
        self.lifecycle_events.append("onstart")
        print(f"ONSTART called! Count: {self.event_counts['onstart']}")

    @Core.receiver("onconfigure")
    def on_configure(self, sender, **kwargs):
        self.event_counts["onconfigure"] += 1
        self.lifecycle_events.append("onconfigure")
        configs = kwargs.get("configs", [])
        print(
            f"ONCONFIGURE called! Count: {self.event_counts['onconfigure']}, configs: {len(configs)}"
        )

    @Core.receiver("onstop")
    def on_stop(self, sender, **kwargs):
        self.event_counts["onstop"] += 1
        self.lifecycle_events.append("onstop")
        print(f"ONSTOP called! Count: {self.event_counts['onstop']}")

    @Core.receiver("ondisconnected")
    def on_disconnected(self, sender, **kwargs):
        self.event_counts["ondisconnected"] += 1
        self.lifecycle_events.append("ondisconnected")
        print(f"ONDISCONNECTED called! Count: {self.event_counts['ondisconnected']}")

    @Core.receiver("onfinish")
    def on_finish(self, sender, **kwargs):
        self.event_counts["onfinish"] += 1
        self.lifecycle_events.append("onfinish")
        print(f"ONFINISH called! Count: {self.event_counts['onfinish']}")


def test_agent_lifecycle(message_bus_manager_fixture):
    """Test the complete agent lifecycle and all its signals."""
    print("Testing agent lifecycle events...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    # Create test agent
    agent = manager.create_agent("lifecycle_test_agent", LifecycleTestAgent)

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
        # ondisconnected can fire multiple times (programmatic disconnect + websocket close)
        assert (
            agent.event_counts["ondisconnected"] >= 1
        ), f"Expected ondisconnected>=1, got {agent.event_counts['ondisconnected']}"

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
        except Exception:
            pass


def test_signal_decorators(message_bus_manager_fixture):
    """Test using @Core.receiver decorators for lifecycle events."""
    print("\n=== Testing @Core.receiver Decorators ===")

    manager = message_bus_manager_fixture
    manager.start_bus()

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

    agent = manager.create_agent("decorator_test_agent", DecoratorTestAgent)

    try:
        agent.connect()
        gevent.sleep(2)

        # Verify decorator handlers were called
        assert (
            "decorator_onstart" in agent.decorator_events
        ), "Decorator onstart handler not called"
        assert (
            "decorator_onconnected" in agent.decorator_events
        ), "Decorator onconnected handler not called"

        print("✓ @Core.receiver decorators work correctly")

    finally:
        if agent.connected:
            agent.disconnect()


def test_onstart_called_once(message_bus_manager_fixture):
    """Test that onstart is only called once during agent lifecycle (original test)."""
    print("\n=== Testing onstart is called exactly once ===")

    manager = message_bus_manager_fixture
    manager.start_bus()

    class OnstartTestAgent(Agent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.onstart_count = 0
            # Register handler
            self.core._handlers["onstart"].append(self.on_onstart)

        def on_onstart(self, sender, **kwargs):
            self.onstart_count += 1
            print(f"ONSTART CALLED! Count: {self.onstart_count}")

    agent = manager.create_agent("onstart_test_agent", OnstartTestAgent)

    try:
        agent.connect()
        gevent.sleep(2)

        # Check that onstart was called exactly once
        assert (
            agent.onstart_count == 1
        ), f"Expected onstart=1, got {agent.onstart_count}"
        print("✓ onstart was called exactly once!")

    finally:
        if agent.connected:
            agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
