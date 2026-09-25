# Running Legacy VOLTTRON Agents on AEMS

This document explains how to run existing VOLTTRON agents on AEMS without modifying their code.

## Prerequisites

### Required: VOLTTRON Clone

**You MUST have a local clone of VOLTTRON** to use the legacy agent launcher. The launcher provides the runtime compatibility layer, but the agent source code must come from a VOLTTRON repository.

```bash
# Clone VOLTTRON for standard agents
git clone https://github.com/VOLTTRON/volttron.git ~/volttron

# Clone PNNL applications for ILCAgent and other PNNL-specific agents
git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git ~/volttron-pnnl-applications
```

**Why this is required:**
- Agent source code lives in the VOLTTRON repository, not in AEMS
- Some agents depend on VOLTTRON helper modules (e.g., `BaseHistorian`, `dnp3_master`)
- Configuration files and agent-specific dependencies are part of VOLTTRON agents
- AEMS provides the **runtime platform** (message bus, RPC, config store), agents provide the **application logic**

## Overview

The AEMS compatibility layer allows you to run VOLTTRON agents by:
1. **Import Hook System** - Automatically redirects VOLTTRON imports to AEMS equivalents
2. **Compatibility Shims** - Provides VOLTTRON-compatible APIs backed by AEMS
3. **Launcher Script** - `start-legacy.py` handles setup and execution
4. **Agent Source** - Loads agent code from your VOLTTRON clone

## Supported VOLTTRON Features

### Fully Supported

| Feature | VOLTTRON Import | AEMS Equivalent |
|---------|----------------|-----------------|
| Agent Base Class | `volttron.platform.vip.agent.Agent` | `derhost.client.agent.Agent` |
| RPC Calls | `@RPC.export` | `@RPC.export` (identical) |
| PubSub | `@PubSub.subscribe()` | Decorator + runtime subscription |
| Core Signals | `@Core.receiver()` | `@Core.receiver()` (identical) |
| Periodic Tasks | `@Core.periodic()` | `@Core.periodic()` (identical) |
| Config Loading | `utils.load_config()` | JSON config loader |
| Logging Setup | `utils.setup_logging()` | Python logging config |
| Main Entry | `utils.vip_main()` | Command-line parser |
| Health Status | `vip.health.set_status()` | Publishes to health topic |
| Heartbeat | `vip.heartbeat.start_with_period()` | Background thread publisher |
| Query Subsystem | `Query(core).query()` | Basic platform info |

### Partial Support

| Feature | Status | Notes |
|---------|--------|-------|
| `all_platforms=True` | Ignored | Single platform in AEMS |
| ZMQ Server Keys | Returns placeholder | AEMS uses WebSocket, not ZMQ |
| Platform Security | Not applicable | Use standard web security (TLS/JWT) |

### Not Supported

- VOLTTRON platform installation/management commands
- ZMQ-specific features
- Multi-platform deployments

## Quick Start

### 1. Start AEMS Server

```bash
# In one terminal
source .venv/bin/activate
aems-server --host 0.0.0.0 --port 8000
```

### 2. Run Your VOLTTRON Agent

```bash
# In another terminal
source .venv/bin/activate

./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener \
    --address ws://localhost:8000
```

## Usage Examples

### Example 1: ListenerAgent

```bash
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener
```

### Example 2: Custom Agent from Package

```bash
./start-legacy.py mypackage.agents.sensor:SensorAgent \
    --config /path/to/sensor_config.json \
    --identity sensor_agent_1 \
    --address ws://192.168.1.100:8000
```

### Example 3: No Configuration File

```bash
./start-legacy.py myagent.agent:MyAgent \
    --identity my_agent
```

### Example 4: With Debug Logging

```bash
./start-legacy.py listener.agent:ListenerAgent \
    --config config.json \
    --identity listener \
    --debug
```

## Command-Line Options

```
./start-legacy.py <module>:<class> [options]

Required:
  module:class           Module path and class name
                        Example: listener.agent:ListenerAgent

Optional:
  --config PATH         Path to agent configuration file (JSON)
  --identity ID         Agent identity/name (default: lowercase class name)
  --address URL         AEMS message bus address
                        Default: ws://localhost:8000
  --volttron-home PATH  VOLTTRON_HOME directory
                        Default: ~/.volttron
  --debug               Enable debug logging
  --help                Show help message
```

## How It Works

### Import Redirection

When you run an agent via `start-legacy.py`, the import hook intercepts VOLTTRON imports:

```python
# In your VOLTTRON agent code:
from volttron.platform.vip.agent import Agent, Core, RPC
from volttron.platform.agent import utils
from volttron.platform.messaging.health import STATUS_GOOD

# Gets automatically redirected to:
from derhost.compat.shims.vip_agent import Agent, Core, RPC
from derhost.compat.shims.platform_agent import utils
from derhost.compat.shims.health import STATUS_GOOD
```

### Compatibility Shims

Each shim provides a VOLTTRON-compatible interface:

**Agent Class:**
```python
class Agent(BaseAgent):
    # Extends AEMS agent with VOLTTRON subsystems
    def __init__(self, **kwargs):
        super().__init__(...)
        self.vip.heartbeat = Heartbeat(self)
        self.vip.health = Health(self)
```

**Utils Module:**
```python
class utils:
    @staticmethod
    def load_config(config_path):
        # Loads JSON config file

    @staticmethod
    def vip_main(agent_class, version="1.0"):
        # Parses command line and runs agent
```

### @PubSub.subscribe Decorator

The decorator marks methods for subscription:

```python
@PubSub.subscribe('pubsub', 'sensors/temperature')
def on_temperature(self, peer, sender, bus, topic, headers, message):
    # This method gets subscribed automatically at startup
    pass
```

During agent startup, `start-legacy.py` scans for `_pubsub_subscriptions` attributes and registers them.

## Migrating VOLTTRON Agents

### Option 1: Use start-legacy.py (No Changes)

**Pros:**
- Zero code changes
- Works with existing VOLTTRON agents as-is
- Easy to test

**Cons:**
- Import hook overhead
- One extra script to manage

### Option 2: Update Imports (Minimal Changes)

Change only the imports:

```python
# Before (VOLTTRON):
from volttron.platform.vip.agent import Agent, Core, RPC
from volttron.platform.agent import utils

# After (AEMS):
from derhost.client.agent import Agent, Core, RPC
from derhost.compat.shims.platform_agent import utils
```

Then run directly without start-legacy.py.

### Option 3: Full Migration (Recommended for New Development)

Use AEMS APIs directly for new agents. See [README.md](README.md) for examples.

## Configuration Files

### VOLTTRON Config Format (JSON)

```json
{
    "agentid": "listener",
    "message": "Hello from listener!",
    "heartbeat_period": 10,
    "runtime_limit": 3600,
    "log-level": "INFO"
}
```

The compatibility layer loads this via `utils.load_config()`.

### Accessing Config in Agent

```python
class MyAgent(Agent):
    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)
        self.config = utils.load_config(config_path)

        # Access config values
        self.heartbeat_period = self.config.get('heartbeat_period', 30)
```

## Troubleshooting

### Import Errors

**Problem:** `ModuleNotFoundError: No module named 'volttron'`

**Solution:** Make sure you're using `start-legacy.py`, not running the agent directly. The script installs import hooks before importing your agent.

### Agent Module Not Found

**Problem:** `ModuleNotFoundError: No module named 'listener'`

**Solution:** Ensure the agent's directory is in `PYTHONPATH` or use the full module path:

```bash
# Add to PYTHONPATH
export PYTHONPATH=/path/to/agent/directory:$PYTHONPATH
./start-legacy.py listener.agent:ListenerAgent

# Or use absolute module path
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent
```

### Connection Refused

**Problem:** `Connection refused` when agent tries to connect

**Solution:** Ensure AEMS server is running:

```bash
# Check if server is running
curl http://localhost:8000/health/

# Start server if not running
aems-server
```

### Config File Not Found

**Problem:** `Config file not found` warning

**Solution:** Provide full path to config or check file exists:

```bash
# Relative path
./start-legacy.py agent:Agent --config ./config.json

# Absolute path
./start-legacy.py agent:Agent --config /home/user/project/config.json
```

### Heartbeat or Health Not Working

**Problem:** `AttributeError: 'VIP' object has no attribute 'heartbeat'`

**Solution:** Ensure you're using the compatibility layer. The agent class must inherit from the compatibility-enhanced Agent:

```python
# This is done automatically by import hooks when using start-legacy.py
from volttron.platform.vip.agent import Agent  # Gets redirected to compat Agent
```

## Advanced Usage

### Using with ConfigStore

Agents can still use AEMS ConfigStore:

```python
@config.subscribe("device_config")
def on_config_update(self, config_name, action, contents):
    # Handle config updates from AEMS ConfigStore
    pass
```

Add config via REST API before starting agent:

```bash
curl -X POST http://localhost:8000/config-store/ \
    -H "Content-Type: application/json" \
    -d '{
        "agent_identity": "listener",
        "config_name": "device_config",
        "config_data": {"setting": "value"}
    }'
```

### Multiple Agents

Run multiple agents by starting multiple instances:

```bash
# Terminal 1
./start-legacy.py listener.agent:ListenerAgent --identity listener1

# Terminal 2
./start-legacy.py sensor.agent:SensorAgent --identity sensor1

# Terminal 3
./start-legacy.py controller.agent:ControllerAgent --identity controller1
```

### RPC Between Agents

Agents can call each other via RPC:

```python
# In SensorAgent
@RPC.export
def get_reading(self):
    return {"temperature": 72.5}

# In ControllerAgent
def update_control(self):
    # Call SensorAgent's RPC method
    result = self.vip.rpc.call("sensor1", "get_reading").get()
    print(f"Temperature: {result['temperature']}")
```

## Architecture Diagram

```
+--------------------------------------------------------------+
|                  VOLTTRON Agent Code                         |
|  (No modifications needed!)                                  |
|                                                              |
|  from volttron.platform.vip.agent import Agent, Core, RPC   |
|  from volttron.platform.agent import utils                  |
+------------------------+-------------------------------------+
                         |
                         +- Import Hook Intercepts
                         |
+------------------------v-------------------------------------+
|              AEMS Compatibility Layer                        |
|                                                              |
|  * vip_agent.py    -> Agent, Core, RPC, PubSub               |
|  * platform_agent.py -> utils (load_config, vip_main)        |
|  * health.py       -> STATUS_GOOD, STATUS_BAD                |
|  * heartbeat.py    -> Heartbeat subsystem                    |
|  * query.py        -> Query subsystem                        |
+------------------------+-------------------------------------+
                         |
                         +- Maps to AEMS APIs
                         |
+------------------------v-------------------------------------+
|                   AEMS Core Libraries                        |
|                                                              |
|  * derhost.client.agent.Agent                                |
|  * derhost.client.agent.RPC                                  |
|  * derhost.client.agent.PubSub                               |
|  * derhost.client.agent.Core                                 |
+------------------------+-------------------------------------+
                         |
                         +- WebSocket Connection
                         |
+------------------------v-------------------------------------+
|              AEMS FastAPI Message Bus                        |
|                                                              |
|  * WebSocket message routing                                |
|  * RPC request/response                                     |
|  * PubSub topic distribution                                |
|  * ConfigStore REST API                                     |
+--------------------------------------------------------------+
```

## Reference: Import Mapping

| VOLTTRON Import | AEMS Compatibility Shim |
|-----------------|------------------------|
| `volttron.platform.vip.agent` | `derhost.compat.shims.vip_agent` |
| `volttron.platform.agent` | `derhost.compat.shims.platform_agent` |
| `volttron.platform.messaging.health` | `derhost.compat.shims.health` |
| `volttron.platform.vip.agent.subsystems.query` | `derhost.compat.shims.query` |
| `volttron.platform.vip.agent.subsystems.heartbeat` | `derhost.compat.shims.heartbeat` |

## Next Steps

1. **Test with your agent:** Use `start-legacy.py` with your existing VOLTTRON agent
2. **Review compatibility:** Check which features your agent uses
3. **Plan migration:** Decide between compatibility layer vs. full migration
4. **Report issues:** If you find unsupported features, let us know!

## Support

For issues or questions:
- Check [README.md](README.md) for AEMS documentation
- Review [CLAUDE.md](CLAUDE.md) for development guidance
- See compatibility shims in `src/derhost/compat/shims/`
