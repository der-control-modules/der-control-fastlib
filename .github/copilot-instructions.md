# AEMS FastAPI Library: AI Development Guide

## Project Overview

AEMS (Agent Energy Management System) is a FastAPI-based server providing:
1. A WebSocket message bus for agent communication
2. A configuration store system for agent settings
3. REST API endpoints for configuration management

This library follows the VOLTTRON Interface Protocol (VIP) patterns with a modern FastAPI implementation.

## Architecture

### Core Components

#### Server Component (`src/derhost/server/`)
- `fastapi_message_bus.py`: Main FastAPI server with WebSocket endpoints
- `connection_manager.py`: Manages WebSocket connections and message routing
- `config_store.py`: Configuration storage with file watching capabilities
- `config_store_handler.py`: Watchdog-based file system monitoring for config changes
- `models.py`: Core data models and message types

#### Client Component (`src/derhost/client/`)
- `agent.py`: Base Agent class with VIP subsystems
- `jsonrpc.py`: JSON-RPC protocol implementation for inter-agent communication

### Communication Patterns

1. **WebSocket Message Bus**: Agents connect via WebSocket to exchange messages
   - Connection URL: `ws://{host}:{port}/ws/{identity}`

2. **Pub/Sub Pattern**: Topic-based publish/subscribe for broadcasts
   ```python
   # Publishing
   agent.vip.pubsub.publish("", "example/topic", {"data": "value"})

   # Subscribing with prefix
   agent.vip.pubsub.subscribe("example/")

   # Subscribing with regex
   agent.vip.pubsub.subscribe_regex("^example/\\d+/status$")
   ```

3. **RPC Pattern**: Direct agent-to-agent method calls using JSON-RPC
   ```python
   # Exporting an RPC method
   @RPC.export
   def add(self, x, y):
       return x + y

   # Calling an RPC method on another agent
   result = agent.vip.rpc.call("target_agent", "add", 5, 3).get(timeout=5)
   ```

4. **Config Store**: REST API for configuration management
   - Configurations stored in `VOLTTRON_HOME/aems_config_store` by default
   - Supports JSON, CSV, and YAML formats

## Development Workflow

### Setup and Environment

```bash
# Initial setup (creates venv, installs deps, sets up pre-commit)
./setup-dev.sh

# Activate virtual environment
source .venv/bin/activate

# Install development dependencies
pip install -e ".[dev]"
```

### Code Quality Commands

```bash

# Run all pre-commit hooks

### Testing Commands

```bash
# Run all tests
make test

# Run tests with coverage
make test-cov

# Run a specific test file
.venv/bin/pytest tests/test_agent_rpc.py

# Run a specific test function
.venv/bin/pytest tests/test_agent_rpc.py::test_rpc_call
```

### Running the Server


# With custom config directory
## Agent Development Patterns


class MyAgent(Agent):

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        """Called when agent stops."""
        # Cleanup operations
        """Handle incoming data messages."""
        # Process the message
    def get_status(self):
        """RPC method that can be called by other agents."""

```python
    # Agent is connected within this block
    result = agent.vip.rpc.call("target", "method").get()

# Agent is automatically disconnected when leaving the context

# Manual disconnection
agent.disconnect()
```

### AsyncResult Pattern

The library uses the AsyncResult pattern for non-blocking operations:

```python
# Making an RPC call (non-blocking)
async_result = agent.vip.rpc.call("target_agent", "method", arg1, arg2)

# Do other work while waiting
# ...

# Get the result when needed (blocking)
result = async_result.get(timeout=5)  # Waits up to 5 seconds
```

## Testing Patterns

### Using the MessageBusManager

The test suite provides utilities to create message buses and agents for testing:

```python
from tests.utils import MessageBusManager, create_connected_test_agent

def test_agent_functionality():
    with MessageBusManager() as manager:
        # Start a message bus for testing
        bus, port = manager.start_bus()

        # Create connected agents
        agent1 = manager.create_connected_agent("agent1")
        agent2 = manager.create_connected_agent("agent2")

        # Export RPC method on agent1
        agent1.vip.rpc.export("add", lambda x, y: x + y)

        # Call the method from agent2
        result = agent2.vip.rpc.call("agent1", "add", 2, 3).get(timeout=5)
        assert result == 5
```

### Using Test Fixtures

```python
def test_with_fixtures(message_bus, connected_agent_factory):
    # message_bus fixture automatically starts a bus

    # Create agents using the factory
    agent1 = connected_agent_factory("agent1")
    agent2 = connected_agent_factory("agent2")

    # Test agent interactions
    # ...
```

### Testing Custom Agent Classes

```python
class MyCustomAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = "custom_data"

def test_custom_agent(message_bus_manager_fixture):
    bus, port = message_bus_manager_fixture.start_bus()

    # Create custom agent
    agent = message_bus_manager_fixture.create_connected_agent(
        "custom_agent",
        agent_class=MyCustomAgent
    )

    assert isinstance(agent, MyCustomAgent)
    assert agent.data == "custom_data"
```

## Configuration Management

### Config Store Access

```python
# In agent code, access configurations
config = self.vip.config.get("my_config")

# Subscribe to config changes
def on_config_update(self, config_name, action, contents):
    if config_name == "my_config" and action == "UPDATE":
        self.my_config = contents

self.vip.config.subscribe(self.on_config_update)
```

### Configuration Initialization

When an agent connects to the server:

1. The agent's internal config cache is cleared to ensure fresh data
2. The agent fetches all server configs via HTTP request to `/config-store/list`
3. For each config found, it fetches the content and merges with any default configs
4. The agent's `onconfigure` event is triggered with the loaded configs
5. Any config subscriptions are set up

This process ensures the agent's internal cache is synchronized with the persisted files on the server at startup.

### REST API for Config Store- `GET /config-store/list`: List all configurations
- `GET /config-store/{agent_id}/{config_name}`: Get a configuration
- `PUT /config-store/{agent_id}/{config_name}`: Store a configuration
- `DELETE /config-store/{agent_id}/{config_name}`: Delete a configuration

## Best Practices

1. **Always disconnect agents** or use the connection context manager
2. **Export RPC methods** with the `@RPC.export` decorator
3. **Register lifecycle handlers** with `@Core.receiver` (e.g., "onstart", "onstop")
4. **Use AsyncResult.get()** with timeouts to avoid blocking indefinitely
5. **Handle errors in callbacks** to prevent agent crashes
6. **Follow the project's code style** (Black with 120-char line length)

## Code Examples

### Complete PubSub Example

```python
from derhost.client.agent import Agent, Core

class PubSubExampleAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_messages = []

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        # Subscribe to topics
        self.vip.pubsub.subscribe("example/", self.on_message)
        self.vip.pubsub.subscribe_regex("^test/\\d+$", self.on_regex_message)

        # Publish a message
        self.vip.pubsub.publish("", "example/start", {"status": "started"})

    def on_message(self, peer, sender, bus, topic, headers, message):
        self.received_messages.append({"topic": topic, "message": message})
        print(f"Received on {topic}: {message}")

    def on_regex_message(self, peer, sender, bus, topic, headers, message):
        print(f"Regex match on {topic}: {message}")
```

### Complete RPC Example

```python
from derhost.client.agent import Agent, Core, RPC

class RPCExampleAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = 0

    @RPC.export
    def increment(self, amount=1):
        """Increment the counter by the given amount."""
        self.counter += amount
        return self.counter

    @RPC.export
    def get_counter(self):
        """Get the current counter value."""
        return self.counter

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        # Make an RPC call to another agent
        def call_other_agent():
            try:
                result = self.vip.rpc.call(
                    "other_agent",
                    "some_method",
                    arg1="value"
                ).get(timeout=5)
                print(f"RPC result: {result}")
            except Exception as e:
                print(f"RPC error: {e}")

        # Schedule the RPC call
        self.core.schedule(call_other_agent, delay=1)
```

## Reference Documentation

- Complete [agent.py API documentation](src/derhost/client/agent.py) for all agent classes and methods
- [test_example_usage.py](tests/test_example_usage.py) for comprehensive examples of using test utilities
- REST API OpenAPI documentation available at `/docs` when server is running
