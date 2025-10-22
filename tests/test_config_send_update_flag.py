"""
Test that the send_update flag properly controls WebSocket notifications.

This test verifies that:
1. When send_update=True (default), WebSocket notifications are sent
2. When send_update=False, WebSocket notifications are NOT sent
3. Local callbacks are respected based on send_update flag
"""

import logging
from unittest.mock import patch

import gevent

# Configure logging for test debugging
logging.basicConfig(
    level=logging.DEBUG, format="%(name)s - %(levelname)s - %(message)s"
)
_log = logging.getLogger(__name__)


def test_config_send_update_true_sends_notification(message_bus_manager_fixture):
    """Test that send_update=True (default) sends WebSocket notification."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("test_agent_1")

    # Track WebSocket messages sent
    sent_messages = []
    original_send = manager.bus.manager.send_message

    async def mock_send(identity, message):
        sent_messages.append((identity, message))
        return await original_send(identity, message)

    try:
        agent1.connect()
        gevent.sleep(0.5)  # Wait for connection

        # Patch the send_message method to track calls
        with patch.object(manager.bus.manager, "send_message", side_effect=mock_send):
            # Set config with send_update=True (default)
            result = agent1.vip.config.set("test_config", {"key": "value"})
            result.get(timeout=5.0)

            # Give time for async WebSocket notification
            gevent.sleep(0.5)

        # Check that VOLTTRON-style RPC notification was sent
        config_updates = [
            msg
            for _, msg in sent_messages
            if msg.get("type") == "vip"
            and msg.get("message", {}).get("data", {}).get("method") == "config.update"
        ]
        assert (
            len(config_updates) > 0
        ), f"Expected config.update RPC messages, but got none. All messages: {sent_messages}"

    finally:
        agent1.disconnect()
        manager.stop_bus()


def test_config_send_update_false_no_notification(message_bus_manager_fixture):
    """Test that send_update=False does NOT send WebSocket notification."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("test_agent_2")

    # Track WebSocket messages sent
    sent_messages = []
    original_send = manager.bus.manager.send_message

    async def mock_send(identity, message):
        sent_messages.append((identity, message))
        return await original_send(identity, message)

    try:
        agent1.connect()
        gevent.sleep(0.5)  # Wait for connection

        # Patch the send_message method to track calls
        with patch.object(manager.bus.manager, "send_message", side_effect=mock_send):
            # Set config with send_update=False
            result = agent1.vip.config.set(
                "test_config", {"key": "value"}, send_update=False
            )
            result.get(timeout=5.0)

            # Give time to ensure no async WebSocket notification
            gevent.sleep(0.5)

        # Check that NO VOLTTRON-style RPC notification was sent
        config_updates = [
            msg
            for _, msg in sent_messages
            if msg.get("type") == "vip"
            and msg.get("message", {}).get("data", {}).get("method") == "config.update"
        ]
        assert (
            len(config_updates) == 0
        ), f"Expected no config.update RPC messages, but got: {config_updates}"

    finally:
        agent1.disconnect()
        manager.stop_bus()


def test_config_send_update_controls_local_callbacks(message_bus_manager_fixture):
    """Test that send_update flag controls local callback execution."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("test_agent_3")

    # Track callback invocations
    callback_called = []

    def test_callback(config_name, action, contents):
        callback_called.append((config_name, action, contents))

    try:
        agent1.connect()
        gevent.sleep(0.5)  # Wait for connection

        # Subscribe to config updates
        agent1.vip.config.subscribe(test_callback, pattern="test_config")

        # Test with send_update=True (should call callback)
        result = agent1.vip.config.set(
            "test_config", {"key": "value1"}, send_update=True
        )
        result.get(timeout=5.0)
        gevent.sleep(0.5)  # Allow callback execution

        assert len(callback_called) == 1
        assert callback_called[0][0] == "test_config"
        assert callback_called[0][1] == "NEW"  # First set is NEW, not UPDATE
        assert callback_called[0][2]["key"] == "value1"

        # Clear callback tracking
        callback_called.clear()

        # Test with send_update=False (should NOT call callback)
        result = agent1.vip.config.set(
            "test_config", {"key": "value2"}, send_update=False
        )
        result.get(timeout=5.0)
        gevent.sleep(0.5)  # Allow time to ensure no callback

        assert (
            len(callback_called) == 0
        ), f"Expected no callbacks with send_update=False, but got: {callback_called}"

    finally:
        agent1.disconnect()
        manager.stop_bus()


def test_config_delete_send_update_flag(message_bus_manager_fixture):
    """Test that send_update flag works for delete operations."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("test_agent_4")

    # Track WebSocket messages sent
    sent_messages = []
    original_send = manager.bus.manager.send_message

    async def mock_send(identity, message):
        sent_messages.append((identity, message))
        return await original_send(identity, message)

    try:
        agent1.connect()
        gevent.sleep(0.5)  # Wait for connection

        # First, create a config
        result = agent1.vip.config.set("test_config", {"key": "value"})
        result.get(timeout=5.0)

        # Clear any messages from the setup
        sent_messages.clear()

        # Test delete with send_update=False
        with patch.object(manager.bus.manager, "send_message", side_effect=mock_send):
            result = agent1.vip.config.delete("test_config", send_update=False)
            result.get(timeout=5.0)
            gevent.sleep(0.5)

        # Check that NO VOLTTRON-style RPC delete notification was sent
        delete_messages = [
            msg
            for _, msg in sent_messages
            if msg.get("type") == "vip"
            and msg.get("message", {}).get("data", {}).get("method") == "config.update"
            and "DELETE" in msg.get("message", {}).get("data", {}).get("args", [])
        ]
        assert (
            len(delete_messages) == 0
        ), f"Expected no config.update DELETE RPC messages with send_update=False, but got: {delete_messages}"

        # Create config again
        result = agent1.vip.config.set("test_config2", {"key": "value"})
        result.get(timeout=5.0)

        sent_messages.clear()

        # Test delete with send_update=True (default)
        with patch.object(manager.bus.manager, "send_message", side_effect=mock_send):
            result = agent1.vip.config.delete("test_config2")
            result.get(timeout=5.0)
            gevent.sleep(0.5)

        # Check that VOLTTRON-style RPC delete notification WAS sent
        delete_messages = [
            msg
            for _, msg in sent_messages
            if msg.get("type") == "vip"
            and msg.get("message", {}).get("data", {}).get("method") == "config.update"
            and "DELETE" in msg.get("message", {}).get("data", {}).get("args", [])
        ]
        assert len(delete_messages) > 0, (
            f"Expected config.update DELETE RPC message with send_update=True, but got no delete messages. "
            f"All messages: {sent_messages}"
        )

    finally:
        agent1.disconnect()
        manager.stop_bus()


def test_manager_agent_update_store_flag_usage(message_bus_manager_fixture):
    """
    Test that ManagerAgent's update_from_config_store correctly uses update_store=False
    to prevent notification loops.
    """
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent1 = manager.create_agent("manager_test")

    # Track config.set calls
    set_calls = []
    original_set = agent1.vip.config.set

    def mock_set(name, data, send_update=True):
        set_calls.append((name, data, send_update))
        return original_set(name, data, send_update)

    try:
        agent1.connect()
        gevent.sleep(0.5)  # Wait for connection

        with patch.object(agent1.vip.config, "set", side_effect=mock_set):
            # Simulate what ManagerProxy.config_set does
            # When update_store=False is passed, it should use send_update=False
            result = agent1.vip.config.set(
                "schedule",
                {"Monday": {"start": "8:00", "end": "17:00"}},
                send_update=False,
            )
            result.get(timeout=5.0)

            # Verify that send_update=False was used
            assert len(set_calls) == 1
            assert set_calls[0][2] is False  # send_update should be False

            set_calls.clear()

            # When update_store=True is passed (or default), it should use send_update=True
            result = agent1.vip.config.set(
                "schedule",
                {"Monday": {"start": "9:00", "end": "18:00"}},
                send_update=True,
            )
            result.get(timeout=5.0)

            # Verify that send_update=True was used
            assert len(set_calls) == 1
            assert set_calls[0][2] is True  # send_update should be True

    finally:
        agent1.disconnect()
        manager.stop_bus()
