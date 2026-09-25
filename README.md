# AEMS FastAPI - VOLTTRON-Compatible Agent Communication Library

## Overview

AEMS FastAPI provides a **100% VOLTTRON-compatible agent communication library** built on modern FastAPI and WebSocket technology. This library enables Python applications to use VOLTTRON's proven agent patterns (RPC, PubSub, Config Store, Scheduling) without requiring the full VOLTTRON platform infrastructure.

> **Important for Legacy Agent Users:**
>
> To run existing VOLTTRON agents with the legacy launcher (`start-legacy.py`), you **must have a local clone of VOLTTRON**. The launcher provides the runtime platform, but the agent source code comes from the VOLTTRON repository.
>
> ```bash
> # Clone VOLTTRON before using legacy agent launcher
> git clone https://github.com/VOLTTRON/volttron.git ~/volttron
> # For PNNL applications (ILCAgent, etc.)
> git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git ~/volttron-pnnl-applications
> ```

**Key Benefits:**
- **Drop-in VOLTTRON compatibility** - Use familiar `@RPC.export`, `@config.subscribe`, `@periodic` decorators
- **Modern FastAPI backend** - WebSocket-based communication, REST APIs, automatic OpenAPI docs
- **Zero infrastructure overhead** - No ZMQ brokers, no complex platform setup
- **Production ready** - Comprehensive test coverage, proven in AEMS energy management systems

## Architecture

```
+---------------------+    WebSocket    +--------------------------+
|   VOLTTRON Agent    | --------------> |   FastAPI Message Bus    |
|                     |                 |                          |
| * RPC Methods       |                 | * Agent Management       |
| * PubSub Topics     |                 | * Message Routing        |
| * Config Store      | <-------------- | * Config Store           |
| * Periodic Tasks    |    HTTP REST    | * Health Monitoring      |
+---------------------+                 +--------------------------+
```

## Quick Start

### 1. Installation

```bash
# Basic installation
pip install der-control-fastlib

# Or install with optional dependencies for specific agent types
pip install -e ".[historians]"  # For SQLHistorian, MQTTHistorian, etc.
pip install -e ".[drivers]"     # For PlatformDriverAgent with device interfaces
pip install -e ".[ilc]"          # For ILCAgent (Intelligent Load Control)

# Install multiple at once
pip install -e ".[historians,drivers,ilc]"
```

#### Optional Dependencies

- **historians**: Required for VOLTTRON historian agents (SQLHistorian, MQTTHistorian)
  - `python-dateutil`, `ply`
  - Optional database drivers: MySQL, PostgreSQL, MQTT
- **drivers**: Required for PlatformDriverAgent with hardware interfaces
  - `bacpypes`, `pymodbus`, `modbus-tk`, `pyserial`
- **ilc**: Required for ILCAgent (demand response control)
  - `sympy` (symbolic math), `transitions` (state machines)

### 2. Start the Message Bus Server

```bash
# Start server on default port 8000
aems-server

# Or specify custom host/port
aems-server --host 0.0.0.0 --port 9000
```

### 3. Run an Agent

**Option A: Run Existing VOLTTRON Agents (No Code Changes)**

> **Important:** You must have a local clone of VOLTTRON to use the legacy agent launcher. The launcher runs agents from the VOLTTRON source code repository.

```bash
# First, clone VOLTTRON if you don't have it
git clone https://github.com/VOLTTRON/volttron.git ~/volttron

# Run any existing VOLTTRON agent without modification
./start-legacy.py \
    --agent-dir ~/volttron/examples/ListenerAgent \
    --config config \
    --identity listener \
    --address ws://localhost:8000
```

**Why VOLTTRON clone is needed:**
- Agent source code lives in the VOLTTRON repository
- Some agents depend on VOLTTRON helper modules (e.g., `BaseHistorian`)
- AEMS provides the runtime platform, but agents come from VOLTTRON source

**Option B: Create a New VOLTTRON-Compatible Agent**

```python
import time
from derhost.client.agent import Agent

# Create agent with VOLTTRON-compatible interface
agent = Agent(identity="my_agent", address="ws://localhost:8000")

# Use familiar VOLTTRON decorators
from derhost.client.agent import RPC, config, periodic

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

### **RPC (Remote Procedure Calls)**
```python
# Export methods for other agents to call
@RPC.export
def calculate_setpoint(self, current_temp, target_temp):
    return {"setpoint": (current_temp + target_temp) / 2}

# Call methods on other agents
result = agent.vip.rpc.call("thermostat_agent", "set_temperature", 72.5)
```

### **PubSub (Publish/Subscribe Messaging)**
```python
# Subscribe to topics
agent.vip.pubsub.subscribe("", "sensors/temperature", self.on_temperature)

# Publish messages
agent.vip.pubsub.publish("", "actuators/damper", {"position": 45})
```

### **Config Store**
```python
# React to configuration changes
@config.subscribe("device_settings")
def on_device_config(self, config_name, action, contents):
    self.device_settings = contents
    self.reconfigure_device()

# Access stored configurations
config = agent.vip.config.get("device_settings")
```

### **Scheduling & Periodic Tasks**
```python
# Periodic execution
@periodic(60)  # Every 60 seconds
def collect_data(self):
    data = self.read_sensors()
    self.vip.pubsub.publish("", "data/sensors", data)

# Cron-based scheduling
agent.core.schedule("0 */6 * * *", self.daily_report)  # Every 6 hours
```

## Legacy Agent Launcher

The **start-legacy.py** script enables running existing VOLTTRON agents without any code modifications. It provides a compatibility layer that transparently redirects VOLTTRON imports to AEMS equivalents.

> **Prerequisites:**
> - AEMS installed with optional dependencies (if needed): `pip install -e ".[historians,drivers,ilc]"`
> - **A local clone of VOLTTRON** containing the agent source code
>
> ```bash
> # Standard VOLTTRON agents
> git clone https://github.com/VOLTTRON/volttron.git ~/volttron
>
> # PNNL applications (ILCAgent, etc.)
> git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git ~/volttron-pnnl-applications
> ```

### How It Works

1. **Auto-Detection**: Scans agent directory to find the module and class
2. **Import Hooks**: Intercepts VOLTTRON imports and redirects to AEMS shims
3. **Config Handling**: Intelligently parses configs and passes parameters to agent `__init__`
4. **Working Directory**: Changes to agent directory before running (like `vctl start`)
5. **Agent Source**: Loads agent code from your VOLTTRON clone

### Usage

```bash
./start-legacy.py \
    --agent-dir /path/to/AgentDirectory \
    --config config_file \
    --identity agent.identity \
    --address ws://localhost:8000 \
    --volttron-home /path/to/volttron_home \
    --debug
```

### Config Store Location

The launcher shows which config store directory the agent will use:

```
VOLTTRON_HOME: /home/volttron/.volttron
Config Store:  /home/volttron/.volttron/aems_config_store/platform.driver
  Existing configs: devices/PNNL/SRINIVAS/SCHNEIDER, registry_configs/schneider.csv
```

This makes it easy to verify the agent is reading from the correct location.

### Supported Agents

Successfully tested with:
- **ListenerAgent** - Basic agent with pub/sub
- **PlatformDriverAgent** - Complex agent with custom `__init__` parameters
- **Custom agents** - Any VOLTTRON agent following standard patterns

### Graceful Shutdown

Pressing Ctrl+C properly shuts down the agent:
- Stops the scheduler
- Disconnects from message bus
- Cleans up resources
- No zombie processes

## VOLTTRON Compatibility

This library provides **100% API compatibility** with VOLTTRON's core agent communication features:

| VOLTTRON Feature | Compatibility | Status |
|------------------|---------------|---------|
| `@RPC.export` decorator | 100% | All method signatures and behaviors match |
| `vip.rpc.call()` | 100% | Peer-to-peer RPC with identical interface |
| `vip.pubsub` subscribe/publish | 100% | Topic patterns, message routing, headers |
| `@config.subscribe()` | 100% | Configuration callbacks without duplicates |
| `@periodic()` decorator | 100% | Interval-based tasks with proper cleanup |
| `core.schedule()` cron | 100% | Cron expressions and event scheduling |
| Agent lifecycle | 100% | Connect, disconnect, health monitoring |

**Not Included:** VOLTTRON platform security features (ZMQ encryption, ZAP authentication) - use standard web security practices instead.

## Development Setup

### Prerequisites
- Python 3.10+
- For AEMS integration: VOLTTRON development environment

### Quick Development Setup
```bash
# Clone and setup
git clone <repository-url>
cd der-control-fastlib

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
# VOLTTRON_HOME - Base directory for agent configs
export VOLTTRON_HOME=/home/user/.volttron

# JWT_SECRET_KEY - Secret key for JWT token generation (production only)
export JWT_SECRET_KEY=your-secret-key-change-in-production

# Start server
aems-server --host 0.0.0.0 --port 8000
```

### Server Options
```bash
aems-server --help

Options:
  --host TEXT          Server host address [default: 127.0.0.1]
  --port INTEGER       Server port [default: 8000]
  --volttron-home PATH VOLTTRON_HOME directory for config store
                       [default: ~/.volttron]
  --config-dir PATH    Custom config store directory
                       [default: $VOLTTRON_HOME/aems_config_store]
```

### Config Store Location

The config store is located at `$VOLTTRON_HOME/aems_config_store/` by default. Each agent gets its own subdirectory:

```
~/.volttron/aems_config_store/
+-- platform.driver/
|   +-- devices/PNNL/BUILDING/DEVICE
|   +-- registry_configs/device.csv
+-- platform.historian/
|   +-- config
+-- ilc.platform/
    +-- config
```

## Use Cases

### **Building Energy Management**
- HVAC control agents communicating via RPC
- Sensor data published via PubSub topics
- Configuration-driven device management
- Scheduled optimization routines

### **IoT Device Integration**
- Lightweight agent communication without VOLTTRON platform
- Modern web-based APIs for external integration
- Real-time data streaming via WebSockets

### **VOLTTRON Development & Testing**
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

### Option 1: Run Existing VOLTTRON Agents Without Modification

Use the **legacy agent launcher** to run existing VOLTTRON agents with zero code changes:

```bash
# Run any existing VOLTTRON agent without modifications
./start-legacy.py \
    --agent-dir /path/to/your/VolttronAgent \
    --config config \
    --identity my.agent \
    --address ws://localhost:8000
```

**Features:**
- **Zero code changes** - Run existing VOLTTRON agents as-is
- **Auto-detection** - Automatically finds agent module and class
- **Import compatibility** - Transparent redirection of VOLTTRON imports to AEMS
- **Config support** - Loads agent configs and intelligently passes parameters
- **Graceful shutdown** - Ctrl+C properly stops agents and cleans up
- **Config store integration** - Shows which config store directory is being used

**Example:**
```bash
# Run PlatformDriverAgent with existing config
./start-legacy.py \
    --agent-dir $HOME/volttron/services/core/PlatformDriverAgent \
    --config config \
    --identity platform.driver \
    --address ws://localhost:8000 \
    --volttron-home /home/volttron/.volttron

# Output shows:
# VOLTTRON_HOME: /home/volttron/.volttron
# Config Store:  /home/volttron/.volttron/aems_config_store/platform.driver
# PlatformDriverAgent is running
```

See `./start-legacy.py --help` for all options.

### Option 2: Migrate Agent Code

For new development or when you want to fully migrate, update your agent code:

1. **Change imports**: `from derhost.client.agent import Agent, RPC, config, periodic`
2. **Update connection**: Use WebSocket address instead of ZMQ
3. **Keep all decorators**: `@RPC.export`, `@config.subscribe`, `@periodic` work identically
4. **Test compatibility**: Run with `python -m pytest tests/test_volttron_compatibility.py`

## Documentation

### Complete Documentation

- **[Quick Start Guide](docs/QUICK_START_LEGACY_AGENTS.md)** - Get started running legacy VOLTTRON agents in 30 seconds
- **[Legacy Agent Support](docs/LEGACY_AGENT_SUPPORT.md)** - Comprehensive guide to running VOLTTRON agents on AEMS
- **[Implementation Details](docs/LEGACY_WRAPPER_SUMMARY.md)** - Technical details of the compatibility layer
- **[vctl-Style Launcher](docs/VCTL_STYLE_LAUNCHER.md)** - Using start-legacy.py like VOLTTRON's vctl
- **[Compatibility Status](docs/VOLTTRON_COMPATIBILITY_STATUS.md)** - Feature compatibility matrix
- **[AI Transformation Journey](docs/AI_TRANSFORMATION_JOURNEY.md)** - How this library was built with AI assistance
- **[Development Guide](CLAUDE.md)** - Developer setup and common commands

### Quick Links

- [Installation](#1-installation) - Install with optional dependencies
- [Running Legacy Agents](#legacy-agent-launcher) - Run VOLTTRON agents without modifications
- [VOLTTRON Compatibility](#volttron-compatibility) - Feature compatibility matrix
- [Development Setup](#development-setup) - Set up for development

## Support

This library is actively developed for the **AEMS (Autonomous Energy Management Software)** project and provides production-grade VOLTTRON compatibility for modern Python applications.

For issues or questions, please refer to the project documentation or submit issues to the repository.
