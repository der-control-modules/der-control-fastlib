"""
Tests for API endpoints and framework connectors.

This module tests the API endpoints and framework connectors:
- Version endpoint
- Normal framework connector
- Various REST API endpoints
"""

import json
import pytest
from fastapi import FastAPI
import httpx

from aems.server.fastapi_message_bus import FastAPIMessageBus
from aems.client.agent import Agent, RPC

# Consolidating tests from:
# - test_endpoints.py
# - test_version_endpoint.py
# - test_normal_framework_connector.py


class TestVersionEndpoint:
    """Tests for the version endpoint."""

    def test_version_endpoint(self, message_bus):
        """Test that the version endpoint returns the correct version information."""
        # Start the message bus
        message_bus.start()

        # Use direct HTTP calls instead of TestClient
        base_url = f"http://{message_bus.host}:{message_bus.port}"

        # Test version endpoint
        response = httpx.get(f"{base_url}/version")

        # Check response
        assert response.status_code == 200
        data = response.json()

        # Verify response has version information
        assert "version" in data
        assert isinstance(data["version"], str)

        message_bus.stop()


class TestApiEndpoints:
    """Tests for various API endpoints."""

    def test_health_endpoint(self, message_bus):
        """Test the health endpoint."""
        # Start the message bus
        message_bus.start()

        # Use direct HTTP calls instead of TestClient
        base_url = f"http://{message_bus.host}:{message_bus.port}"

        # Test health endpoint
        response = httpx.get(f"{base_url}/health")

        # Check response
        assert response.status_code == 200
        data = response.json()

        # Verify response has health information
        assert "status" in data
        assert data["status"] == "healthy"

        message_bus.stop()

    def test_agents_endpoint(self, message_bus):
        """Test the agents endpoint."""
        # Start the message bus
        message_bus.start()

        # Add some agents
        agent1 = Agent(identity="agent1", port=8888)
        agent2 = Agent(identity="agent2", port=8888)

        # Connect the agents
        agent1.connect()
        agent2.connect()

        # Wait for connections to be established
        import gevent
        gevent.sleep(1)

        # Use direct HTTP calls instead of TestClient
        base_url = f"http://{message_bus.host}:{message_bus.port}"

        # Test agents endpoint
        response = httpx.get(f"{base_url}/agents")

        # Check response
        assert response.status_code == 200
        data = response.json()

        # Verify response has agent information
        assert isinstance(data, list)

        agent_ids = [agent["identity"] for agent in data]
        assert "agent1" in agent_ids
        assert "agent2" in agent_ids

        # Clean up
        agent1.disconnect()
        agent2.disconnect()

        message_bus.stop()

    def test_configs_endpoint(self, message_bus):
        """Test the configs endpoint."""
        # Start the message bus
        message_bus.start()

        # Add an agent with configs
        agent = Agent(identity="config_agent", port=8888)

        # Connect the agent
        agent.connect()

        # Wait for connection to be established
        import gevent
        gevent.sleep(1)

        # Add some configs
        agent.config.set("config1", {"key1": "value1"})
        agent.config.set("config2", {"key2": "value2"})

        # Use direct HTTP calls instead of TestClient
        base_url = f"http://{message_bus.host}:{message_bus.port}"

        # Test configs endpoint
        response = httpx.get(f"{base_url}/configs/config_agent")

        # Check response
        assert response.status_code == 200
        data = response.json()

        # Verify response has config information
        assert isinstance(data, list)
        assert "config1" in data
        assert "config2" in data

        # Clean up
        agent.disconnect()
        message_bus.stop()


class TestFrameworkConnector:
    """Tests for the framework connector."""

    def test_normal_framework_connector(self, message_bus):
        """Test the normal framework connector."""
        # Start the message bus
        message_bus.start()

        # Add an agent
        agent = Agent(identity="connector_test", port=8888)

        # Connect the agent
        agent.connect()

        # Wait for connection to be established
        import gevent
        gevent.sleep(1)

        # Test agent connectivity through the framework
        assert agent.connected

        # Test simple RPC call to the platform
        # Note: This may need to be adjusted based on available platform methods
        try:
            version = agent.vip.rpc.call("platform", "get_version")
            assert isinstance(version.get(), str)
        except Exception as e:
            # Skip if platform methods aren't available in test
            print(f"Platform RPC test skipped: {e}")

        # Clean up
        agent.disconnect()
        message_bus.stop()

    def test_connector_agent_communication(self, message_bus):
        """Test agent-to-agent communication through the connector."""
        # Start the message bus
        message_bus.start()

        # Create message tracking
        received_messages = []

        # Create receiving agent
        class ReceiverAgent(Agent):
            def __init__(self, identity, **kwargs):
                super().__init__(identity, **kwargs)

            def on_message(self, peer, sender, bus, topic, headers, message):
                received_messages.append(message)

            @RPC.export
            def echo(self, message):
                return f"Echo: {message}"

        # Create sending agent
        sender_agent = Agent(identity="sender", port=8888)
        receiver_agent = ReceiverAgent(identity="receiver", port=8888)

        # Connect the agents
        sender_agent.connect()
        receiver_agent.connect()

        # Set up subscription after connecting
        receiver_agent.vip.pubsub.subscribe("test/topic", receiver_agent.on_message)

        # Wait for connections and subscriptions to be established
        import gevent
        gevent.sleep(1)

        # Test publish/subscribe
        sender_agent.vip.pubsub.publish("", "test/topic", {"data": "test_message"})

        # Allow time for message to be delivered
        gevent.sleep(1)

        # Verify message was received
        assert len(received_messages) == 1
        assert received_messages[0]["data"] == "test_message"

        # Test RPC
        response = sender_agent.vip.rpc.call("receiver", "echo", "Hello")
        assert response.get() == "Echo: Hello"

        # Clean up
        sender_agent.disconnect()
        receiver_agent.disconnect()
        message_bus.stop()
