# AEMS Test Utilities

This module provides convenient utilities for testing AEMS applications.

## Quick Start

### Simple Message Bus Testing

```python
from tests.test_utils import TestMessageBusManager

def test_my_feature():
    with TestMessageBusManager() as manager:
        # Start message bus on random port
        bus, port = manager.start_bus()

        # Create agents
        agent = manager.create_agent("my_agent")

        # Test your functionality
        # ...
```

### Using Fixtures

```python
def test_with_fixtures(message_bus, test_port, agent_factory):
    # message_bus is already started
    assert message_bus.is_running()

    # test_port gives you the port number
    print(f"Bus running on port {test_port}")

    # agent_factory creates agents connected to the bus
    agent = agent_factory("test_agent")
```

## Available Classes

### TestMessageBusManager

Main class for managing test message bus instances.

**Methods:**
- `start_bus(port=None, host="127.0.0.1")` - Start message bus
- `stop_bus()` - Stop and cleanup
- `create_agent(identity, agent_class=None, **kwargs)` - Create agent (not connected)
- `create_connected_agent(identity, agent_class=None, **kwargs)` - Create agent and establish connection
- `get_port()` - Get current port
- `get_base_url()` - Get HTTP base URL
- `get_ws_url(identity)` - Get WebSocket URL for agent

Note: `agent_class=None` defaults to using the base `Agent` class.

**Context Manager:**
```python
class MyCustomAgent(Agent):
    def onstart(self, sender, **kwargs):
        print(f"Custom agent {self.identity} started!")

with TestMessageBusManager() as manager:
    bus, port = manager.start_bus()

    # Create unconnected agent
    agent = manager.create_agent("my_agent")

    # Create connected agent (ready to use)
    connected_agent = manager.create_connected_agent("connected_agent")

    # Create custom agent class
    custom_agent = manager.create_connected_agent("custom_agent", agent_class=MyCustomAgent)

    # Automatic cleanup on exit
```

## Available Functions

### Convenience Functions
- `get_random_open_port()` - Get available port
- `start_test_message_bus(port=None, host="127.0.0.1")` - Quick bus start
- `create_test_agent(identity, agent_class=None, **kwargs)` - Quick agent creation (not connected)
- `create_connected_test_agent(identity, agent_class=None, **kwargs)` - Quick connected agent creation
- `get_test_port()` - Get current test port from env var
- `get_test_base_url(host="127.0.0.1")` - Get test HTTP URL
- `get_test_ws_url(identity, host="127.0.0.1")` - Get test WebSocket URL

Note: `agent_class=None` defaults to using the base `Agent` class.

## Available Fixtures

### Core Fixtures
- `message_bus` - Started message bus instance
- `test_bus_manager` - TestMessageBusManager instance
- `test_port` - Current test port number
- `random_port` - Random available port
- `agent_factory` - Function to create test agents (not connected)
- `connected_agent_factory` - Function to create connected test agents

### Usage Examples

```python
def test_simple(message_bus):
    """Use pre-started message bus."""
    assert message_bus.is_running()

def test_custom(test_bus_manager):
    """Control bus lifecycle manually."""
    bus, port = test_bus_manager.start_bus(port=9999)
    agent = test_bus_manager.create_agent("custom_agent")  # Not connected
    connected_agent = test_bus_manager.create_connected_agent("connected_agent")  # Connected

def test_agent_creation(agent_factory, connected_agent_factory):
    """Create multiple agents easily."""
    agent1 = agent_factory("agent1")  # Not connected
    agent2 = connected_agent_factory("agent2")  # Connected and ready
    custom_agent = connected_agent_factory("custom", agent_class=MyCustomAgent)  # Custom agent

def test_port_info(test_port, random_port):
    """Access port information."""
    print(f"Test bus port: {test_port}")
    print(f"Available port: {random_port}")
```

## Environment Variables

- `AEMS_FASTAPI_TEST_PORT` - Set by TestMessageBusManager to current port

## Custom Agent Classes

All factory functions and methods support custom agent classes:

```python
class MyTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_data = "initialized"

    def onstart(self, sender, **kwargs):
        print(f"Custom agent {self.identity} started!")
        self.test_data = "started"

# Use with TestMessageBusManager
with TestMessageBusManager() as manager:
    bus, port = manager.start_bus()
    custom_agent = manager.create_connected_agent("my_agent", agent_class=MyTestAgent)
    assert isinstance(custom_agent, MyTestAgent)
    assert custom_agent.test_data == "started"

# Use with convenience functions
custom_agent = create_connected_test_agent("my_agent", agent_class=MyTestAgent)

# Use with fixtures
def test_custom_agents(connected_agent_factory):
    agent = connected_agent_factory("test_agent", agent_class=MyTestAgent)
    assert isinstance(agent, MyTestAgent)
```

## Best Practices

1. **Use Context Managers**: Always use `with TestMessageBusManager()` for automatic cleanup
2. **Fixture vs Manager**: Use fixtures for simple tests, manager for complex scenarios
3. **Port Management**: Let the system choose random ports to avoid conflicts
4. **Agent Cleanup**: Agents are cleaned up automatically when the bus stops
5. **Resource Isolation**: Each test gets its own temporary config directory
6. **Connected vs Unconnected Agents**:
   - Use `create_agent()` when you need to control connection timing
   - Use `create_connected_agent()` when you need agents ready for immediate communication
   - Connected agents have established WebSocket connections and fired `onstart` events

## Error Handling

- `RuntimeError` - Raised if bus fails to start within timeout
- `RuntimeError` - Raised if trying to use manager without starting bus first

## Integration with Existing Tests

This module is designed to work alongside existing pytest fixtures and testing patterns. You can gradually migrate existing tests or use both approaches side-by-side.
