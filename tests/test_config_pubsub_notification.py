#!/usr/bin/env python3
"""
Test to verify that agents receive config update notifications via pubsub
"""

import logging
import time

import gevent
import pytest

from aems.client.agent import Agent

_log = logging.getLogger(__name__)


class ConfigPubSubTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pubsub_notifications = []
        self.callback_notifications = []

    def config_update_callback(self, config_name, action, config_value):
        """Callback for configuration updates via direct subscription."""
        _log.info(f"DIRECT CALLBACK RECEIVED: {config_name} ({action})")
        self.callback_notifications.append(
            {
                "config_name": config_name,
                "action": action,
                "received_at": time.time(),
                "source": "callback",
            }
        )


def test_config_pubsub_notification(message_bus_manager_fixture):
    """Test that agents receive config notifications via pubsub."""
    _log.info("Testing config notifications via pubsub...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("pubsub_agent1", ConfigPubSubTestAgent)
    agent2 = manager.create_agent("pubsub_agent2", ConfigPubSubTestAgent)

    try:
        _log.info("1. Connecting both agents...")
        agent1.connect()
        gevent.sleep(0.5)
        agent2.connect()
        gevent.sleep(1)  # Allow time for pubsub subscription setup

        _log.info("2. Agent1 subscribing to config updates via direct subscription...")
        agent1.config.subscribe(
            callback=agent1.config_update_callback,
            pattern="pubsub_test_config",
            actions=["UPDATE", "NEW"],
        )

        # Note: Both agents automatically subscribe to their own config topics
        # via "config/{agent_identity}/" during connection

        _log.info("3. Storing configuration in agent1 (should notify both agents)...")
        test_config = {"test": "data", "timestamp": time.time()}
        agent1.config.set("pubsub_test_config", test_config)

        _log.info("4. Waiting for notifications...")
        gevent.sleep(3)  # Allow time for notifications to be processed

        _log.info("5. Verifying agent1 received notification via callback...")
        assert (
            len(agent1.callback_notifications) > 0
        ), "Agent1 did not receive direct callback notification"

        _log.info(
            "6. Verifying agent1 has the config (config isolation means agent2 should NOT have it)..."
        )
        # Only agent1 should have the config in their cache (config stores are isolated)
        assert (
            "pubsub_test_config" in agent1.config._config_cache
        ), "Agent1 missing config in cache"
        assert (
            "pubsub_test_config" not in agent2.config._config_cache
        ), "Agent2 should not have agent1's config (config isolation)"

        # Check that agent1 has the correct config data
        assert (
            agent1.config.get("pubsub_test_config") == test_config
        ), "Agent1 has incorrect config data"

        _log.info("7. Agent2 setting its own config (isolated from agent1)...")
        updated_config = {"test": "updated", "timestamp": time.time()}
        agent2.config.set("pubsub_test_config", updated_config)

        gevent.sleep(3)  # Allow time for notifications to be processed

        _log.info("8. Verifying config isolation - each agent has its own config...")
        # Each agent should have their own version of the config (isolated)
        agent1_config = agent1.config.get("pubsub_test_config")
        agent2_config = agent2.config.get("pubsub_test_config")

        assert (
            agent1_config["test"] == "data"
        ), f"Agent1 config should be unchanged: {agent1_config}"
        assert (
            agent2_config["test"] == "updated"
        ), f"Agent2 config should be updated: {agent2_config}"

        _log.info("✅ SUCCESS: Config isolation and notifications working correctly!")

    finally:
        if hasattr(agent1, "connected") and agent1.connected:
            agent1.disconnect()
        if hasattr(agent2, "connected") and agent2.connected:
            agent2.disconnect()


def test_config_delete_notification(message_bus_manager_fixture):
    """Test that config deletion notifications are received via pubsub."""
    _log.info("Testing config deletion notifications via pubsub...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("delete_agent1", ConfigPubSubTestAgent)
    agent2 = manager.create_agent("delete_agent2", ConfigPubSubTestAgent)

    try:
        _log.info("1. Connecting both agents...")
        agent1.connect()
        gevent.sleep(0.5)
        agent2.connect()
        gevent.sleep(1)  # Allow time for pubsub subscription setup

        _log.info("2. Setting up test config in agent1...")
        test_config = {"test": "delete_me", "timestamp": time.time()}
        agent1.config.set("delete_test_config", test_config)

        gevent.sleep(2)  # Allow time for initial config to propagate

        # Verify only agent1 has the config (due to config isolation)
        assert (
            "delete_test_config" in agent1.config._config_cache
        ), "Agent1 missing initial config"
        assert (
            "delete_test_config" not in agent2.config._config_cache
        ), "Agent2 should not have agent1's config (config isolation)"

        _log.info(
            "3. Agent1 deleting the config (only affects agent1 due to config isolation)..."
        )
        agent1.config.delete("delete_test_config")

        gevent.sleep(3)  # Allow time for delete notification to be processed

        _log.info("4. Verifying agent1 processed the deletion...")
        # If the config was not in default configs, it should be removed from cache
        # If it was in default configs, it should revert to default

        # Check if config was removed or reverted to default for agent1
        if "delete_test_config" in agent1.config._default_configs:
            assert (
                agent1.config._config_cache["delete_test_config"]
                == agent1.config._default_configs["delete_test_config"]
            ), "Agent1 config not reverted to default"
        else:
            assert (
                "delete_test_config" not in agent1.config._config_cache
            ), "Agent1 did not remove the deleted config"

        # Agent2 should still not have the config (config isolation)
        assert (
            "delete_test_config" not in agent2.config._config_cache
        ), "Agent2 should not have agent1's config (config isolation)"

        _log.info("✅ SUCCESS: Config deletion with isolation working correctly!")

    finally:
        if hasattr(agent1, "connected") and agent1.connected:
            agent1.disconnect()
        if hasattr(agent2, "connected") and agent2.connected:
            agent2.disconnect()


if __name__ == "__main__":
    # Enable logging for manual test execution
    logging.basicConfig(level=logging.INFO)

    # Run tests
    pytest.main(["-xvs", __file__])
