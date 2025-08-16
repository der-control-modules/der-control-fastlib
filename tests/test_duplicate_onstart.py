#!/usr/bin/env python3
"""
Test to check if onstart is still being called twice
"""
import gevent
import pytest

from aems.client.agent import Agent, Core


class DuplicateTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.onstart_count = 0

    @Core.receiver("onstart")
    def _onstart(self, sender=None, **kwargs):
        self.onstart_count += 1
        print(f"ONSTART called! Count: {self.onstart_count}")


def test_duplicate_onstart_issue(message_bus):
    """Test that onstart is only called once."""
    print("Testing for duplicate onstart issue...")

    agent = DuplicateTestAgent("test_agent", port=8888)

    try:
        print("1. Connecting agent...")
        agent.connect()

        # Wait for events to be processed
        gevent.sleep(2)

        print(f"Final onstart count: {agent.onstart_count}")

        # Check that onstart was called exactly once
        assert agent.onstart_count == 1, f"Expected onstart=1, got {agent.onstart_count}"
        print("✓ onstart was called exactly once!")

    finally:
        if hasattr(agent, "connected") and agent.connected:
            agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
