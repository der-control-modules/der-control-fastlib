#!/usr/bin/env python3
"""
Summary test demonstrating config store isolation per agent
"""

import gevent
import pytest
import requests

from aems.client.agent import Agent


class ConfigIsolationTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notifications = []

    def on_config_update(self, config_name, action, config_value):
        """Handle configuration updates."""
        self.notifications.append({"config_name": config_name, "action": action, "agent_identity": self.identity})
        print(f"[{self.identity}] Received {action} for '{config_name}'")


def test_config_store_isolation_summary(message_bus_manager_fixture):
    """
    Comprehensive test demonstrating that:
    1. Each agent has its own isolated config store
    2. Updates to one agent's config only notify that agent
    3. Agents can have configs with the same name without interference
    4. Both REST API and agent.config.set() respect isolation
    """
    print("Testing comprehensive config store isolation...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    # Create two agents using the test manager
    agent_a = manager.create_agent("agent_a", ConfigIsolationTestAgent)
    agent_b = manager.create_agent("agent_b", ConfigIsolationTestAgent)

    try:
        print("\n1. Connecting both agents...")
        agent_a.connect()
        agent_b.connect()
        gevent.sleep(1)

        print("\n2. Both agents subscribe to configs with identical names...")
        # Both agents subscribe to configs with the same name
        agent_a.config.subscribe(
            callback=agent_a.on_config_update,
            pattern="shared_config_name",
            actions=["UPDATE", "NEW"],
        )
        agent_b.config.subscribe(
            callback=agent_b.on_config_update,
            pattern="shared_config_name",
            actions=["UPDATE", "NEW"],
        )
        gevent.sleep(0.5)

        print("\n3. Agent A stores a config via agent.config.set()...")
        # Agent A should get a notification for its own config update
        agent_a.config.set("shared_config_name", {"owner": "agent_a", "method": "config.set"}, send_update=True)
        gevent.sleep(2)

        print("\n4. Agent B stores a config via REST API...")
        config_data = {"owner": "agent_b", "method": "rest_api"}
        response = requests.put(
            f"{manager.get_base_url()}/config-store/agent_b/shared_config_name",
            json=config_data,
            headers={"Content-Type": "application/json"},
        )
        print(f"REST API response: {response.status_code}")
        gevent.sleep(2)

        print("\n5. Verifying isolation...")
        print(f"Agent A notifications: {len(agent_a.notifications)}")
        print(f"Agent B notifications: {len(agent_b.notifications)}")

        # Each agent should have received exactly one notification for their own config
        assert len(agent_a.notifications) == 1, f"Agent A should have 1 notification, got {len(agent_a.notifications)}"
        assert len(agent_b.notifications) == 1, f"Agent B should have 1 notification, got {len(agent_b.notifications)}"

        # Verify the notifications are for the correct agent
        assert agent_a.notifications[0]["agent_identity"] == "agent_a"
        assert agent_b.notifications[0]["agent_identity"] == "agent_b"

        print("\n✅ SUCCESS: Config store is properly isolated per agent!")
        print("   - Each agent only receives notifications for its own configs")
        print("   - Agents can have configs with identical names without interference")
        print("   - Both REST API and agent.config.set() respect isolation")

    finally:
        for agent in [agent_a, agent_b]:
            if hasattr(agent, "connected") and agent.connected:
                agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
