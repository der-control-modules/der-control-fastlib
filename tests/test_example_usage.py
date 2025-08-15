"""
Example test demonstrating the use of test utilities.
"""

import time

import httpx

from aems.client.agent import Agent, Core
from tests.test_utils import (
    TestMessageBusManager,
    create_connected_test_agent,
    create_test_agent,
    get_test_base_url,
    get_test_port,
    get_test_ws_url,
)


class TestAgent(Agent):
    """Example custom agent class for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_data = "custom_agent_data"
        self.message_count = 0

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        """Called when agent starts."""
        print(f"Custom test agent {self.identity} started!")
        self.test_data = "agent_started"


def test_message_bus_manager_example(message_bus_manager_fixture):
    """Example test using TestMessageBusManager fixture."""
    # Start the message bus
    bus, port = message_bus_manager_fixture.start_bus()

    # Verify the bus is running
    assert bus.is_running()
    assert port > 1024

    # Test HTTP endpoint
    with httpx.Client() as client:
        response = client.get(f"{message_bus_manager_fixture.get_base_url()}/health")
        assert response.status_code == 200

    # Create an agent (not connected)
    agent = message_bus_manager_fixture.create_agent("test_agent")
    assert agent.identity == "test_agent"
    assert not agent.connected

    # Create a connected agent
    connected_agent = message_bus_manager_fixture.create_connected_agent("connected_agent")
    assert connected_agent.identity == "connected_agent"
    assert connected_agent.connected

    # Get URLs
    assert (
        message_bus_manager_fixture.get_ws_url("test_agent")
        == f"ws://127.0.0.1:{port}/ws/test_agent"
    )


def test_with_fixture_message_bus(message_bus, test_port):
    """Example test using the message_bus fixture."""
    # The message_bus fixture automatically starts a bus
    assert message_bus.is_running()

    # test_port gives us the port number
    assert test_port > 1024

    # Test the health endpoint
    with httpx.Client() as client:
        response = client.get(f"http://127.0.0.1:{test_port}/health")
        assert response.status_code == 200


def test_with_bus_manager_fixture(message_bus_manager_fixture):
    """Example test using the message_bus_manager_fixture."""
    # Start the bus manually
    bus, port = message_bus_manager_fixture.start_bus()

    assert bus.is_running()
    assert port == message_bus_manager_fixture.get_port()

    # Create multiple agents
    agent1 = message_bus_manager_fixture.create_agent("agent1")
    agent2 = message_bus_manager_fixture.create_agent("agent2")

    assert agent1.identity == "agent1"
    assert agent2.identity == "agent2"


def test_convenience_functions():
    """Example test using convenience functions directly."""
    with TestMessageBusManager() as manager:
        bus, port = manager.start_bus()

        # Use convenience functions
        test_port = get_test_port()
        assert test_port == port

        base_url = get_test_base_url()
        assert base_url == f"http://127.0.0.1:{port}"

        ws_url = get_test_ws_url("my_agent")
        assert ws_url == f"ws://127.0.0.1:{port}/ws/my_agent"

        # Create agent using convenience function (not connected)
        agent = create_test_agent("my_agent")
        assert agent.identity == "my_agent"
        assert not agent.connected

        # Create connected agent using convenience function
        connected_agent = create_connected_test_agent("my_connected_agent")
        assert connected_agent.identity == "my_connected_agent"
        assert connected_agent.connected


def test_connected_agents():
    """Example test specifically for connected agents."""
    with TestMessageBusManager() as manager:
        bus, port = manager.start_bus()

        # Create multiple connected agents
        agent1 = manager.create_connected_agent("agent1")
        agent2 = manager.create_connected_agent("agent2")

        # Verify they are connected
        assert agent1.connected
        assert agent2.connected

        # Agents should be able to communicate
        # (This would require additional test setup for actual message passing)


def test_agent_factory(message_bus, agent_factory):
    """Example test using the agent_factory fixture."""
    # Create agents using the factory
    agent1 = agent_factory("factory_agent_1")
    agent2 = agent_factory("factory_agent_2")

    assert agent1.identity == "factory_agent_1"
    assert agent2.identity == "factory_agent_2"
    assert not agent1.connected  # Factory creates unconnected agents
    assert not agent2.connected


def test_connected_agent_factory(message_bus, connected_agent_factory):
    """Example test using the connected_agent_factory fixture."""
    # Create connected agents using the factory
    agent1 = connected_agent_factory("connected_factory_agent_1")
    agent2 = connected_agent_factory("connected_factory_agent_2")

    assert agent1.identity == "connected_factory_agent_1"
    assert agent2.identity == "connected_factory_agent_2"
    assert agent1.connected  # Factory creates connected agents
    assert agent2.connected

    # Create connected custom agent using the factory
    custom_agent = connected_agent_factory("custom_agent", agent_class=TestAgent)
    assert custom_agent.identity == "custom_agent"
    assert custom_agent.connected
    assert isinstance(custom_agent, TestAgent)

    # Give time for onstart event to be processed
    time.sleep(0.1)

    assert custom_agent.test_data == "agent_started"  # onstart was called


def test_custom_agent_classes():
    """Example test using custom agent classes."""
    with TestMessageBusManager() as manager:
        bus, port = manager.start_bus()

        # Create regular agent
        regular_agent = manager.create_connected_agent("regular_agent")
        assert isinstance(regular_agent, Agent)
        assert regular_agent.connected

        # Create custom agent
        custom_agent = manager.create_connected_agent("custom_agent", agent_class=TestAgent)
        assert isinstance(custom_agent, TestAgent)
        assert custom_agent.connected

        # Give time for onstart event to be processed
        import time

        time.sleep(0.1)

        assert custom_agent.test_data == "agent_started"
        assert custom_agent.message_count == 0

        # Create another custom agent with convenience function
        custom_agent2 = create_connected_test_agent("custom_agent2", agent_class=TestAgent)
        assert isinstance(custom_agent2, TestAgent)
        assert custom_agent2.connected

        # Give time for onstart event to be processed
        time.sleep(0.1)

        assert custom_agent2.test_data == "agent_started"


def test_random_port_fixture(random_port):
    """Example test using random_port fixture."""
    # Gets a random port without starting anything
    assert 1024 <= random_port <= 65535

    # Can use this port for custom setups
    print(f"Got random port: {random_port}")


if __name__ == "__main__":
    # Run a simple test
    test_message_bus_manager_example()
    print("✅ Example test passed!")
