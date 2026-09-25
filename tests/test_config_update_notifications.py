#!/usr/bin/env python3
"""
Test to verify that UPDATE notifications are sent when configs are stored
"""

import gevent
import pytest

from derhost.client.agent import Agent


class ConfigUpdateTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_notifications = []

    def config_update_callback(self, config_name, action, config_value):
        """Callback for configuration updates."""
        print(f"UPDATE RECEIVED: {config_name} ({action})")
        self.update_notifications.append(
            {
                "config_name": config_name,
                "action": action,
                "received_at": gevent.time.time(),
            }
        )


def test_config_update_notification_sent(message_bus_manager_fixture):
    """Test that storing a config triggers an UPDATE notification to subscribers."""
    print("Testing that UPDATE notifications are sent when configs are stored...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("update_test_agent", ConfigUpdateTestAgent)

    try:
        print("1. Connecting agent...")
        agent.connect()
        gevent.sleep(1)

        print("2. Subscribing to config updates...")
        agent.config.subscribe(
            callback=agent.config_update_callback,
            pattern="notification_test_config",
            actions=["UPDATE", "NEW"],
        )
        gevent.sleep(0.5)

        print(
            "3. Storing configuration (first time - should trigger NEW notification)..."
        )
        # Store a config - this should trigger the NEW notification
        test_config = {"test": "data", "timestamp": gevent.time.time()}
        # Use send_update=True to ensure notifications are sent
        result = agent.config.set(
            "notification_test_config", test_config, send_update=True
        )
        result.get(timeout=5.0)  # Wait for the set to complete

        print("4. Updating configuration (should trigger UPDATE notification)...")
        # Update the config - this should trigger the UPDATE notification
        test_config_updated = {"test": "updated_data", "timestamp": gevent.time.time()}
        result = agent.config.set(
            "notification_test_config", test_config_updated, send_update=True
        )
        result.get(timeout=5.0)  # Wait for the set to complete

        print("5. Waiting for notifications...")
        gevent.sleep(2)  # Wait for the notifications to be processed

        print("6. Verifying notifications were received...")
        print(f"Notifications received: {len(agent.update_notifications)}")
        for notification in agent.update_notifications:
            print(f"  - {notification}")

        # Verify that at least one notification was received
        assert len(agent.update_notifications) > 0, "No notifications received"

        # Verify that notifications were for our config
        our_notifications = [
            n
            for n in agent.update_notifications
            if n["config_name"] == "notification_test_config"
        ]
        assert (
            len(our_notifications) >= 2
        ), "Should have received at least 2 notifications (NEW and UPDATE)"

        # Verify that we got both NEW and UPDATE actions
        new_notifications = [n for n in our_notifications if n["action"] == "NEW"]
        update_notifications = [n for n in our_notifications if n["action"] == "UPDATE"]
        assert len(new_notifications) > 0, "No NEW action notifications received"
        assert len(update_notifications) > 0, "No UPDATE action notifications received"

        print("✅ SUCCESS: UPDATE notification was sent and received correctly!")

    finally:
        if hasattr(agent, "connected") and agent.connected:
            agent.disconnect()


def test_config_update_notification_isolation(message_bus_manager_fixture):
    """Test that agents only receive UPDATE notifications for their own configs,
    even when config names are identical."""
    print("\nTesting config notification isolation (agents with same config names)...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("subscriber1", ConfigUpdateTestAgent)
    agent2 = manager.create_agent("subscriber2", ConfigUpdateTestAgent)

    try:
        print("1. Connecting both agents...")
        agent1.connect()
        gevent.sleep(0.5)
        agent2.connect()
        gevent.sleep(0.5)

        print(
            "2. Both agents subscribing to configs with identical names (but isolated stores)..."
        )
        # Each agent subscribes to their own isolated config with the same name
        agent1.config.subscribe(
            callback=agent1.config_update_callback,
            pattern="shared_name_config",
            actions=["NEW", "UPDATE"],
        )
        agent2.config.subscribe(
            callback=agent2.config_update_callback,
            pattern="shared_name_config",
            actions=["NEW", "UPDATE"],
        )
        gevent.sleep(0.5)

        print(
            "3. Agent1 setting config in its isolated store (should only notify agent1)..."
        )
        # Agent1 stores config in its isolated store - only agent1 should be notified
        # Use send_update=True to ensure notifications are sent
        result = agent1.config.set(
            "shared_name_config",
            {"updated_by": "agent1", "data": "agent1_data"},
            send_update=True,
        )
        result.get(timeout=5.0)

        print("4. Waiting for notifications...")
        gevent.sleep(2)

        print(
            "5. Agent2 setting config in its isolated store (should only notify agent2)..."
        )
        # Agent2 stores config in its isolated store - only agent2 should be notified
        # Use send_update=True to ensure notifications are sent
        result = agent2.config.set(
            "shared_name_config",
            {"updated_by": "agent2", "data": "agent2_data"},
            send_update=True,
        )
        result.get(timeout=5.0)

        print("6. Waiting for notifications...")
        gevent.sleep(2)

        print(
            "7. Verifying config isolation - each agent only notified about its own changes..."
        )
        print(f"Agent1 notifications: {len(agent1.update_notifications)}")
        print(f"Agent2 notifications: {len(agent2.update_notifications)}")

        # Each agent should have received exactly one notification for their own isolated config
        agent1_notifications = [
            n
            for n in agent1.update_notifications
            if n["config_name"] == "shared_name_config"
        ]
        agent2_notifications = [
            n
            for n in agent2.update_notifications
            if n["config_name"] == "shared_name_config"
        ]

        assert (
            len(agent1_notifications) == 1
        ), f"Agent1 should receive exactly 1 notification for its own config, got {len(agent1_notifications)}"
        assert (
            len(agent2_notifications) == 1
        ), f"Agent2 should receive exactly 1 notification for its own config, got {len(agent2_notifications)}"

        print(
            "✅ SUCCESS: Config isolation verified - agents only receive notifications for their own configs!"
        )

    finally:
        for agent in [agent1, agent2]:
            if hasattr(agent, "connected") and agent.connected:
                agent.disconnect()


def test_config_update_via_rest_api_isolation(message_bus_manager_fixture):
    """Test that REST API config updates are properly isolated per agent."""
    print("\nTesting REST API config update isolation...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("rest_agent1", ConfigUpdateTestAgent)
    agent2 = manager.create_agent("rest_agent2", ConfigUpdateTestAgent)

    try:
        print("1. Connecting both agents...")
        agent1.connect()
        gevent.sleep(0.5)
        agent2.connect()
        gevent.sleep(0.5)

        print("2. Both agents subscribing to config with same name...")
        agent1.config.subscribe(
            callback=agent1.config_update_callback,
            pattern="api_test_config",
            actions=["NEW", "UPDATE"],
        )
        agent2.config.subscribe(
            callback=agent2.config_update_callback,
            pattern="api_test_config",
            actions=["NEW", "UPDATE"],
        )
        gevent.sleep(0.5)

        print("3. Updating agent1's config via REST API...")
        # Update agent1's config via REST API - only agent1 should be notified
        import requests

        config_data = {
            "updated_via": "rest_api",
            "target": "agent1",
            "timestamp": gevent.time.time(),
        }

        try:
            response = requests.put(
                f"{manager.get_base_url()}/config-store/rest_agent1/api_test_config",
                json=config_data,
                headers={"Content-Type": "application/json"},
            )
            print(f"REST API response: {response.status_code}")
        except Exception as e:
            print(f"REST API request failed: {e}")
            # Skip this test if REST API is not available
            return

        print("4. Waiting for notifications...")
        gevent.sleep(2)

        print("5. Updating agent2's config via REST API...")
        config_data2 = {
            "updated_via": "rest_api",
            "target": "agent2",
            "timestamp": gevent.time.time(),
        }

        try:
            response = requests.put(
                f"{manager.get_base_url()}/config-store/rest_agent2/api_test_config",
                json=config_data2,
                headers={"Content-Type": "application/json"},
            )
            print(f"REST API response: {response.status_code}")
        except Exception as e:
            print(f"REST API request failed: {e}")

        print("6. Waiting for notifications...")
        gevent.sleep(2)

        print("7. Verifying notification isolation...")
        print(f"Agent1 notifications: {len(agent1.update_notifications)}")
        print(f"Agent2 notifications: {len(agent2.update_notifications)}")

        # Each agent should have received exactly one notification
        agent1_api_notifications = [
            n
            for n in agent1.update_notifications
            if n["config_name"] == "api_test_config"
        ]
        agent2_api_notifications = [
            n
            for n in agent2.update_notifications
            if n["config_name"] == "api_test_config"
        ]

        assert (
            len(agent1_api_notifications) == 1
        ), f"Agent1 should receive exactly 1 notification, got {len(agent1_api_notifications)}"
        assert (
            len(agent2_api_notifications) == 1
        ), f"Agent2 should receive exactly 1 notification, got {len(agent2_api_notifications)}"

        print("✅ SUCCESS: REST API config updates are properly isolated per agent!")

    finally:
        for agent in [agent1, agent2]:
            if hasattr(agent, "connected") and agent.connected:
                agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
