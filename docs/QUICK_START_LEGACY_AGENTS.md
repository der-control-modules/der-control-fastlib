# Quick Start: Running Legacy VOLTTRON Agents on AEMS

## ⚠️ Important Prerequisites

### You MUST Have a VOLTTRON Clone

**The legacy agent launcher requires a local clone of VOLTTRON** to access the agent source code and dependencies. AEMS provides compatibility shims for the VOLTTRON platform, but the actual agent code must come from a VOLTTRON installation or clone.

```bash
# Clone VOLTTRON if you don't have it
git clone https://github.com/VOLTTRON/volttron.git
cd volttron

# Or for PNNL applications (ILCAgent, etc.)
git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git
```

**Why this is needed:**
- Agent source code lives in the VOLTTRON repository
- Some agents have dependencies on VOLTTRON helper modules (e.g., `BaseHistorian`)
- Configuration files and setup scripts are part of VOLTTRON agents
- AEMS provides the **runtime platform**, but agents come from **VOLTTRON source**

## 30-Second Quick Start

```bash
# Terminal 1: Start AEMS server
cd /path/to/aems-lib-fastapi
source .venv/bin/activate
aems-server

# Terminal 2: Run your VOLTTRON agent (NO CODE CHANGES NEEDED!)
cd /path/to/aems-lib-fastapi
source .venv/bin/activate
./start-legacy.py --agent-dir /path/to/volttron/examples/ListenerAgent \
    --config config \
    --identity listener
```

That's it! Your VOLTTRON agent now runs on AEMS.

## Prerequisites

### 1. Clone VOLTTRON Repository

```bash
# For standard VOLTTRON agents (ListenerAgent, PlatformDriverAgent, SQLHistorian, etc.)
git clone https://github.com/VOLTTRON/volttron.git ~/volttron
cd ~/volttron

# For PNNL-specific agents (ILCAgent, etc.)
git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git ~/volttron-pnnl-applications
```

### 2. Install Optional Dependencies

Depending on which VOLTTRON agents you want to run, install the appropriate optional dependencies:

```bash
# For Historian agents (SQLHistorian, MQTTHistorian, etc.)
pip install -e ".[historians]"

# For PlatformDriverAgent with device interfaces
pip install -e ".[drivers]"

# For ILCAgent (Intelligent Load Control)
pip install -e ".[ilc]"

# Or install multiple at once
pip install -e ".[historians,drivers,ilc]"
```

## Examples by Agent Type

### ListenerAgent (Basic Agent)
```bash
# Clone VOLTTRON if you haven't already
git clone https://github.com/VOLTTRON/volttron.git ~/volttron

# Terminal 1: Start AEMS server
cd ~/aems-lib-fastapi
source .venv/bin/activate
aems-server

# Terminal 2: Run ListenerAgent from VOLTTRON clone
cd ~/aems-lib-fastapi
source .venv/bin/activate
./start-legacy.py --agent-dir ~/volttron/examples/ListenerAgent \
    --config config \
    --identity listener
```

### SQLHistorian
```bash
# Install historian dependencies first
cd ~/aems-lib-fastapi
pip install -e ".[historians]"

# Run SQLHistorian from VOLTTRON clone
./start-legacy.py --agent-dir ~/volttron/services/core/SQLHistorian \
    --config config.sqlite \
    --identity platform.historian
```

### PlatformDriverAgent
```bash
# Install driver dependencies first
cd ~/aems-lib-fastapi
pip install -e ".[drivers]"

# Run PlatformDriverAgent from VOLTTRON clone
./start-legacy.py --agent-dir ~/volttron/services/core/PlatformDriverAgent \
    --config config \
    --identity platform.driver
```

### ILCAgent
```bash
# Clone PNNL applications if you haven't already
git clone https://github.com/VOLTTRON/volttron-pnnl-applications.git ~/volttron-pnnl-applications

# Install ILC dependencies
cd ~/aems-lib-fastapi
pip install -e ".[ilc]"

# Run ILCAgent from PNNL applications clone
./start-legacy.py --agent-dir ~/volttron-pnnl-applications/GridServices/Control/ILCAgent \
    --config config \
    --identity ilc.platform
```

## What Gets Redirected Automatically

| Your VOLTTRON Code | Automatically Becomes |
|--------------------|----------------------|
| `from volttron.platform.vip.agent import Agent` | `from aems.compat.shims.vip_agent import Agent` |
| `from volttron.platform.agent import utils` | `from aems.compat.shims.platform_agent import utils` |
| `from volttron.platform.messaging.health import STATUS_GOOD` | `from aems.compat.shims.health import STATUS_GOOD` |

**You don't change anything - it happens automatically!**

## Supported Features

✅ `@RPC.export` - Export RPC methods
✅ `@PubSub.subscribe()` - Subscribe to topics
✅ `@Core.receiver()` - Handle lifecycle events
✅ `@Core.periodic()` - Periodic tasks
✅ `utils.load_config()` - Load configuration
✅ `utils.vip_main()` - Main entry point
✅ `vip.heartbeat` - Heartbeat publishing
✅ `vip.health` - Health status
✅ `vip.rpc.call()` - RPC calls
✅ `vip.pubsub.publish()` - Publish messages
✅ Config Store integration - Agent configs auto-loaded

## Command-Line Options

```bash
./start-legacy.py --agent-dir DIRECTORY [OPTIONS]

Required:
  --agent-dir DIR       Path to agent directory

Optional:
  --config FILE         Configuration file path (relative to agent-dir)
  --identity NAME       Agent identity name
  --address URL         Server address (default: ws://localhost:8000)
  --debug               Enable debug logging
  --help                Show help
```

## Common Patterns

### Pattern 1: Agent with Config
```bash
./start-legacy.py --agent-dir /opt/agents/MyAgent \
    --config config.json \
    --identity my_agent
```

### Pattern 2: Multiple Agents
```bash
# Start each in a separate terminal
./start-legacy.py --agent-dir /opt/agents/Agent1 --identity agent1
./start-legacy.py --agent-dir /opt/agents/Agent2 --identity agent2
./start-legacy.py --agent-dir /opt/agents/Agent3 --identity agent3
```

### Pattern 3: Custom Server
```bash
./start-legacy.py --agent-dir /opt/agents/MyAgent \
    --address ws://192.168.1.100:8000 \
    --identity my_agent
```

### Pattern 4: Using VOLTTRON_HOME
```bash
export VOLTTRON_HOME=/home/user/.volttron
./start-legacy.py --agent-dir /opt/agents/MyAgent
```

## Troubleshooting

### "Cannot connect"
```bash
# Check server is running
curl http://localhost:8000/health/
```

### "Module not found"
```bash
# Ensure agent directory has proper structure:
# AgentDir/
#   ├── package_name/
#   │   ├── __init__.py
#   │   └── agent.py
```

### "Config file not found"
```bash
# Config path is relative to agent directory
./start-legacy.py --agent-dir /opt/MyAgent --config config

# Or use absolute path
./start-legacy.py --agent-dir /opt/MyAgent --config /etc/myagent.json
```

### Missing Dependencies
```bash
# Install the specific optional dependencies needed for your agent
pip install -e ".[historians]"  # For historian agents
pip install -e ".[drivers]"     # For PlatformDriverAgent
pip install -e ".[ilc]"          # For ILCAgent
```

## Testing

Quick test with included example:

```bash
# Run the test script
./test-legacy-agent.sh
```

## More Information

- **Full Documentation:** [LEGACY_AGENT_SUPPORT.md](LEGACY_AGENT_SUPPORT.md)
- **Implementation Details:** [LEGACY_WRAPPER_SUMMARY.md](LEGACY_WRAPPER_SUMMARY.md)
- **AEMS Documentation:** [../README.md](../README.md)

## Questions?

The wrapper handles VOLTTRON imports automatically via import hooks. Your code doesn't change at all - just run it with `start-legacy.py`!
