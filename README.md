# AEMS Server - Message Bus and Configuration Management

## Overview

The AEMS (Agent Energy Management System) server provides a message bus for agent communication and a configuration store for agent settings. It's built on FastAPI and implements a WebSocket-based communication protocol with REST API endpoints for configuration management.

## Installation

### Prerequisites

- Python 3.10 or higher
- pip

### Install from PyPI

```bash
pip install aems-lib-fastapi
```

### Install from source

```bash
git clone https://github.com/VOLTTRON/aems-lib-fastapi.git
cd aems-lib-fastapi
pip install -e .
```

## Starting the Server

### Command Line

```bash
aems-server --host 127.0.0.1 --port 8000
```

### Available Options

- `--host`: Host address to bind to (default: 127.0.0.1)
- `--port`: Port to listen on (default: 8000)
- `--volttron-home`: VOLTTRON_HOME directory (uses environment variable if not specified)
- `--config-dir`: Config store directory (defaults to VOLTTRON_HOME/aems_config_store)

### Environment Variables

- `VOLTTRON_HOME`: Sets the base directory for VOLTTRON-related files

### Programmatically

```python
from aems.server.messagebus import start_server

# Start the server with default settings
server = start_server()

# Start with custom settings
server = start_server(
    host='0.0.0.0',
    port=9000,
    config_store_dir='/path/to/config'
)

# Keep the server running
try:
    import time
    while server.is_running():
        time.sleep(1)
except KeyboardInterrupt:
    server.stop()
```

## API Documentation

Once the server is running, you can access the API documentation at:

- OpenAPI UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

## API Endpoints

### WebSocket Connection

- `/ws/{identity}`: Connect an agent to the message bus
  - Path parameter: `identity` - The unique identity of the agent

### Configuration Store

- `GET /config-store/list`: List all available configurations
  - Query parameter: `agent_id` (optional) - Filter by agent ID

- `GET /config-store/{agent_id}/{config_name}`: Get a specific configuration
  - Path parameters:
    - `agent_id`: The identity of the agent
    - `config_name`: The name of the configuration
  - Query parameter: `raw` (boolean, optional) - Get raw file content instead of parsed data

- `PUT /config-store/{agent_id}/{config_name}`: Store a configuration
  - Path parameters:
    - `agent_id`: The identity of the agent
    - `config_name`: The name of the configuration
  - Request body: JSON object (for JSON configs) or text content (for other formats)
  - Content-Type header: `application/json` for JSON configs, appropriate type for others

- `DELETE /config-store/{agent_id}/{config_name}`: Delete a configuration
  - Path parameters:
    - `agent_id`: The identity of the agent
    - `config_name`: The name of the configuration

- `POST /config-store/{agent_id}/{config_name}/file`: Upload a configuration file
  - Path parameters:
    - `agent_id`: The identity of the agent
    - `config_name`: The name of the configuration
  - Form data: `file` - The file to upload
  - Query parameter: `config_type` (optional) - Override the configuration type

### Health Status

- `GET /health`: Get health status for all connected agents

- `GET /health/{agent_id}`: Get health status for a specific agent
  - Path parameter: `agent_id`: The identity of the agent

## Message Types

The WebSocket connection supports these message types:

### Publish/Subscribe

- **Publish**: Send a message to a topic
  ```json
  {
    "type": "publish",
    "topic": "example/topic",
    "message": {"key": "value"},
    "headers": {"header1": "value1"}
  }
  ```

- **Subscribe**: Subscribe to a topic pattern
  ```json
  {
    "type": "subscribe",
    "prefix": "example/",
    "id": "subscription-id"
  }
  ```

- **Subscribe with Regex**: Subscribe using a regex pattern
  ```json
  {
    "type": "subscribe",
    "pattern": "^example/\\d+/status$",
    "id": "subscription-id"
  }
  ```

### RPC

- **RPC Call**: Call a method on another agent
  ```json
  {
    "type": "rpc",
    "peer": "target_agent",
    "method": "method_name",
    "args": ["arg1", "arg2"],
    "kwargs": {"key1": "value1"},
    "msg_id": "unique-message-id"
  }
  ```

- **RPC Response**: Response from an RPC call
  ```json
  {
    "type": "rpc_response",
    "msg_id": "unique-message-id",
    "result": "method result"
  }
  ```

## Security Considerations

- The server binds to 127.0.0.1 by default for security. Use `--host 0.0.0.0` to accept connections from any IP address (not recommended for production without additional security).
- The server does not currently implement authentication or TLS encryption. For production use, consider running behind a reverse proxy with TLS termination.

## Troubleshooting

- Check server logs for detailed error information
- Verify that agents are using the correct host and port
- Ensure the VOLTTRON_HOME directory is writable if using the default config store location
- For WebSocket connection issues, check network connectivity and firewall settings