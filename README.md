# AEMS FastAPI - VOLTTRON-Compatible Agent Communication Library

## Overview

AEMS FastAPI provides a **100% VOLTTRON-compatible agent communication library** built on modern FastAPI and WebSocket technology. This library enables Python applications to use VOLTTRON's proven agent patterns (RPC, PubSub, Config Store, Scheduling) without requiring the full VOLTTRON platform infrastructure.

**Key Benefits:**
- ✅ **Drop-in VOLTTRON compatibility** - Use familiar `@RPC.export`, `@config.subscribe`, `@periodic` decorators
- ✅ **Modern FastAPI backend** - WebSocket-based communication, REST APIs, automatic OpenAPI docs
- ✅ **Zero infrastructure overhead** - No ZMQ brokers, no complex platform setup
- ✅ **Production ready** - Comprehensive test coverage, proven in AEMS energy management systems

## Architecture

```
┌─────────────────────┐    WebSocket    ┌──────────────────────────┐
│   VOLTTRON Agent    │ ──────────────→ │   FastAPI Message Bus    │
│                     │                 │                          │
│ • RPC Methods       │                 │ • Agent Management       │
│ • PubSub Topics     │                 │ • Message Routing        │
│ • Config Store      │ ←────────────── │ • Config Store           │
│ • Periodic Tasks    │    HTTP REST    │ • Health Monitoring      │
└─────────────────────┘                 └──────────────────────────┘
```

## Quick Start

### 1. Installation

```bash
pip install aems-lib-fastapi
```

### 2. Start the Message Bus Server

```bash
# Start server on default port 8000
aems-server

# Or specify custom host/port
aems-server --host 0.0.0.0 --port 9000
```

### 3. Create a VOLTTRON-Compatible Agent

```python
import time
from aems.client.agent import Agent

# Create agent with VOLTTRON-compatible interface
agent = Agent(identity="my_agent", address="ws://localhost:8000")

# Use familiar VOLTTRON decorators
from aems.client.agent import RPC, config, periodic

@RPC.export
def get_status(self):
    """Export RPC method - callable by other agents"""
    return {"status": "running", "timestamp": time.time()}

@config.subscribe("main", actions=["NEW", "UPDATE"])
def on_config_update(self, config_name, action, contents):
    """Handle configuration updates"""
    print(f"Config {config_name} updated: {contents}")

@periodic(30)  # Run every 30 seconds
def heartbeat(self):
    """Periodic task - runs automatically"""
    print("Agent is alive!")
    # Publish status to other agents
    self.vip.pubsub.publish("", "heartbeat", {"agent": "my_agent"})

# Connect and run
agent.connect()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    agent.disconnect()
```

## Core Features

### 🔄 **RPC (Remote Procedure Calls)**
```python
# Export methods for other agents to call
@RPC.export
def calculate_setpoint(self, current_temp, target_temp):
    return {"setpoint": (current_temp + target_temp) / 2}

# Call methods on other agents
result = agent.vip.rpc.call("thermostat_agent", "set_temperature", 72.5)
```

### 📢 **PubSub (Publish/Subscribe Messaging)**
```python
# Subscribe to topics
agent.vip.pubsub.subscribe("", "sensors/temperature", self.on_temperature)

# Publish messages
agent.vip.pubsub.publish("", "actuators/damper", {"position": 45})
```

### ⚙️ **Config Store**
```python
# React to configuration changes
@config.subscribe("device_settings")
def on_device_config(self, config_name, action, contents):
    self.device_settings = contents
    self.reconfigure_device()

# Access stored configurations
config = agent.vip.config.get("device_settings")
```

### ⏰ **Scheduling & Periodic Tasks**
```python
# Periodic execution
@periodic(60)  # Every 60 seconds
def collect_data(self):
    data = self.read_sensors()
    self.vip.pubsub.publish("", "data/sensors", data)

# Cron-based scheduling
agent.core.schedule("0 */6 * * *", self.daily_report)  # Every 6 hours
```

## VOLTTRON Compatibility

This library provides **100% API compatibility** with VOLTTRON's core agent communication features:

| VOLTTRON Feature | Compatibility | Status |
|------------------|---------------|---------|
| `@RPC.export` decorator | ✅ 100% | All method signatures and behaviors match |
| `vip.rpc.call()` | ✅ 100% | Peer-to-peer RPC with identical interface |
| `vip.pubsub` subscribe/publish | ✅ 100% | Topic patterns, message routing, headers |
| `@config.subscribe()` | ✅ 100% | Configuration callbacks without duplicates |
| `@periodic()` decorator | ✅ 100% | Interval-based tasks with proper cleanup |
| `core.schedule()` cron | ✅ 100% | Cron expressions and event scheduling |
| Agent lifecycle | ✅ 100% | Connect, disconnect, health monitoring |

**Not Included:** VOLTTRON platform security features (ZMQ encryption, ZAP authentication) - use standard web security practices instead.

## Development Setup

### Prerequisites
- Python 3.10+
- For AEMS integration: VOLTTRON development environment

### Quick Development Setup
```bash
# Clone and setup
git clone <repository-url>
cd aems-lib-fastapi

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Code Quality
This project uses **ruff** for all linting and formatting:
```bash
# Format and lint
ruff check --fix .
ruff format .

# Or use pre-commit
pre-commit run --all-files
```

### Testing
```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov

# Run specific test categories
python -m pytest tests/test_volttron_compatibility.py  # VOLTTRON compatibility
python -m pytest tests/test_config_store_*.py         # Config store behavior
```

## API Documentation

Once the server is running, access interactive documentation at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Configuration

### Environment Variables
```bash
# Server configuration
VOLTTRON_HOME=/path/to/volttron    # Base directory for configs
AEMS_CONFIG_DEBUG=1               # Enable config store debugging
AEMS_AGENT_DEBUG=1               # Enable agent communication debugging

# Start server with debugging
aems-server --host 0.0.0.0 --port 8000
```

### Server Options
```bash
aems-server --help
  --host TEXT        Server host address [default: 127.0.0.1]
  --port INTEGER     Server port [default: 8000]
  --volttron-home    VOLTTRON_HOME directory for configs
  --config-dir       Custom config store directory
```

## Use Cases

### 🏢 **Building Energy Management**
- HVAC control agents communicating via RPC
- Sensor data published via PubSub topics
- Configuration-driven device management
- Scheduled optimization routines

### 🔌 **IoT Device Integration**
- Lightweight agent communication without VOLTTRON platform
- Modern web-based APIs for external integration
- Real-time data streaming via WebSockets

### 🧪 **VOLTTRON Development & Testing**
- Test VOLTTRON agent logic without full platform setup
- Develop agents with modern Python tooling
- Prototype agent interactions quickly

## Production Deployment

### Security Considerations
- **Network Security**: Use TLS/HTTPS in production
- **Authentication**: Implement JWT or OAuth2 for agent authentication
- **Firewall**: Restrict WebSocket port access
- **Monitoring**: Use health endpoints for agent monitoring

### Performance
- **Concurrent Agents**: Supports hundreds of connected agents
- **Message Throughput**: High-performance async WebSocket handling
- **Resource Usage**: Minimal overhead compared to full VOLTTRON platform

## Migration from VOLTTRON

Migrating existing VOLTTRON agents is straightforward:

1. **Change imports**: `from aems.client.agent import Agent, RPC, config, periodic`
2. **Update connection**: Use WebSocket address instead of ZMQ
3. **Keep all decorators**: `@RPC.export`, `@config.subscribe`, `@periodic` work identically
4. **Test compatibility**: Run with `python -m pytest tests/test_volttron_compatibility.py`

## Support

This library is actively developed for the **AEMS (Autonomous Energy Management Software)** project and provides production-grade VOLTTRON compatibility for modern Python applications.

For issues or questions, please refer to the project documentation or submit issues to the repository.
