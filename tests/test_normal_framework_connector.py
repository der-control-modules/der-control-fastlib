#!/usr/bin/env python3
"""
Test to simulate the NormalFrameworkConnector agent onstart issue
"""
import pytest
import gevent
from aems.client.agent import Agent, Core


class MockNormalFrameworkConnector(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.heartbeat_setup_count = 0

    @Core.receiver("onstart")
    def _onstart(self, sender=None, **kwargs):
        """Simulate the original NormalFrameworkConnector onstart."""
        print("Starting Normal Framework Connector")
        self.heartbeat_setup_count += 1
        print(f"Setup heartbeat call #{self.heartbeat_setup_count}")


def test_normal_framework_connector_onstart(message_bus):
    """Test that the NormalFrameworkConnector-style agent only sets up heartbeat once."""
    print("Testing NormalFrameworkConnector-style onstart behavior...")

    agent = MockNormalFrameworkConnector("test_connector", port=8888)

    try:
        print("1. Connecting agent...")
        agent.connect()

        # Wait for events to be processed
        gevent.sleep(2)

        print(f"Heartbeat setup count: {agent.heartbeat_setup_count}")

        # Check that heartbeat setup was called exactly once
        assert (
            agent.heartbeat_setup_count == 1
        ), f"Expected heartbeat_setup_count=1, got {agent.heartbeat_setup_count}"
        print("✓ Heartbeat setup was called exactly once!")

    finally:
        if hasattr(agent, "connected") and agent.connected:
            agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
