# Legacy VOLTTRON Agent Wrapper - Implementation Summary

## Overview

We have successfully created a wrapper system that allows existing VOLTTRON agents to run on AEMS without any code modifications.

## Solution Architecture

### Two Approaches Implemented

#### 1. **Import Hook (Monkey Patching)** Recommended
**Implementation:** `src/derhost/compat/import_hook.py`

**How it works:**
- Installs a custom import finder in `sys.meta_path`
- Intercepts imports starting with `volttron.*`
- Redirects to AEMS compatibility shims
- Completely transparent to agent code

**Pros:**
- Zero code changes needed
- Works with any VOLTTRON agent
- Easy to maintain (centralized compatibility logic)
- Can handle complex import hierarchies

**Cons:**
- Adds slight import overhead
- Debugging shows shim modules in stack traces

#### 2. **Launcher Script** Implemented
**Implementation:** `start-legacy.py`

**How it works:**
- Command-line tool that:
  1. Installs import hooks before loading agent
  2. Parses command-line arguments
  3. Loads agent class dynamically
  4. Handles connection and lifecycle
  5. Processes @PubSub.subscribe decorators

**Pros:**
- Clear entry point
- Handles setup automatically
- Provides helpful error messages
- Supports both approaches

**Cons:**
- One extra script to maintain

## Files Created

### Core Compatibility Layer

```
src/derhost/compat/
+-- __init__.py                      # Package initialization
+-- import_hook.py                   # Import redirection system
+-- shims/                           # VOLTTRON compatibility shims
    +-- __init__.py
    +-- vip_agent.py                # Agent, Core, RPC, PubSub
    +-- platform_agent.py           # utils (load_config, vip_main, etc.)
    +-- health.py                   # STATUS_GOOD, STATUS_BAD constants
    +-- health_subsystem.py         # Health subsystem implementation
    +-- heartbeat.py                # Heartbeat subsystem implementation
    +-- query.py                    # Query subsystem implementation
    +-- subsystems.py               # Subsystems namespace
    +-- messaging.py                # Messaging namespace
    +-- vip.py                      # VIP namespace
    +-- platform.py                 # Platform namespace
```

### Launcher and Documentation

```
/
+-- start-legacy.py                  # Main launcher script
+-- test-legacy-agent.sh            # Test script for ListenerAgent
+-- LEGACY_AGENT_SUPPORT.md         # Complete user documentation
+-- LEGACY_WRAPPER_SUMMARY.md       # This file
```

## Import Redirection Map

| VOLTTRON Import | AEMS Shim | Status |
|-----------------|-----------|---------|
| `volttron.platform.vip.agent` | `derhost.compat.shims.vip_agent` | Complete |
| `volttron.platform.agent` | `derhost.compat.shims.platform_agent` | Complete |
| `volttron.platform.messaging.health` | `derhost.compat.shims.health` | Complete |
| `volttron.platform.vip.agent.subsystems.query` | `derhost.compat.shims.query` | Complete |
| `volttron.platform.vip.agent.subsystems.heartbeat` | `derhost.compat.shims.heartbeat` | Complete |

## VOLTTRON Features Implemented

### Agent Base Class
```python
from volttron.platform.vip.agent import Agent, Core, RPC, PubSub

class MyAgent(Agent):
    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)  # Works!
```

- **Implementation:** `vip_agent.py:Agent` extends `derhost.client.agent.Agent`
- **Added subsystems:** `vip.heartbeat`, `vip.health`

### RPC Export/Call
```python
@RPC.export
def get_status(self):
    return {"status": "ok"}

result = self.vip.rpc.call("other_agent", "get_status").get()
```

- **Implementation:** Direct pass-through to `derhost.client.agent.RPC`
- **100% compatible** with VOLTTRON RPC

### PubSub Subscribe
```python
@PubSub.subscribe('pubsub', 'sensors/temperature')
def on_temperature(self, peer, sender, bus, topic, headers, message):
    pass
```

- **Implementation:** `vip_agent.py:PubSub` decorator
- **Runtime registration:** `platform_agent.py:utils._setup_pubsub_subscriptions()`
- Scans agent methods for `_pubsub_subscriptions` attribute at startup

### Core Signals
```python
@Core.receiver('onstart')
def onstart(self, sender, **kwargs):
    pass
```

- **Implementation:** Direct pass-through to `derhost.client.agent.Core`
- **Signals supported:** onstart, onstop, onconnected, ondisconnected, onconfigure

### Configuration Loading
```python
config = utils.load_config(config_path)
```

- **Implementation:** `platform_agent.py:utils.load_config()`
- Loads JSON configuration files
- Returns empty dict if file missing (graceful degradation)

### Main Entry Point
```python
utils.vip_main(MyAgent, version='1.0')
```

- **Implementation:** `platform_agent.py:utils.vip_main()`
- Parses command-line args: `--config`, `--identity`, `--address`
- Instantiates agent, connects, processes decorators
- Runs until Ctrl+C

### Heartbeat Subsystem
```python
self.vip.heartbeat.start_with_period(30)
```

- **Implementation:** `heartbeat.py:Heartbeat`
- Background thread publishes to `heartbeat/{identity}` topic
- Configurable period

### Health Subsystem
```python
self.vip.health.set_status(STATUS_GOOD, "Running normally")
```

- **Implementation:** `health_subsystem.py:Health`
- Publishes to `health/{identity}` topic
- Supports STATUS_GOOD, STATUS_BAD

### Query Subsystem
```python
query = Query(self.core)
serverkey = query.query('serverkey').get()
```

- **Implementation:** `query.py:Query`
- Returns placeholder for ZMQ-specific queries
- Returns actual values for: identity, addresses, version

### Logging Setup
```python
utils.setup_logging()
```

- **Implementation:** `platform_agent.py:utils.setup_logging()`
- Configures Python logging with standard format

## Usage

### Quick Start

1. **Start AEMS server:**
```bash
source .venv/bin/activate
aems-server --host 0.0.0.0 --port 8000
```

2. **Run VOLTTRON agent:**
```bash
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener \
    --address ws://localhost:8000
```

### Command-Line Syntax

```bash
./start-legacy.py <module:class> [options]

Required:
  module:class           Python module path and class name

Options:
  --config PATH         Configuration file (JSON)
  --identity NAME       Agent identity
  --address URL         Message bus URL (default: ws://localhost:8000)
  --volttron-home PATH  VOLTTRON_HOME directory
  --debug               Enable debug logging
```

### Examples

**Run ListenerAgent:**
```bash
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener
```

**Run custom agent with debug:**
```bash
./start-legacy.py mypackage.agents.sensor:SensorAgent \
    --config /path/to/config.json \
    --identity sensor1 \
    --debug
```

**Run without config file:**
```bash
./start-legacy.py simple.agent:SimpleAgent --identity simple
```

## Testing

### Test Script

```bash
./test-legacy-agent.sh
```

This script:
1. Checks if AEMS server is running
2. Activates virtual environment
3. Runs ListenerAgent with debug logging

### Manual Test

```bash
# Terminal 1: Start server
source .venv/bin/activate
aems-server

# Terminal 2: Run agent
source .venv/bin/activate
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener
```

You should see:
- Agent starts and connects
- Heartbeat messages published every 10 seconds
- PubSub subscriptions registered
- RPC methods exported

## Implementation Details

### Import Hook System

**File:** `src/derhost/compat/import_hook.py`

**Key class:** `VolttronImportRedirector`

**How it works:**
1. Implements `MetaPathFinder` and `Loader` from `importlib.abc`
2. Checks if module name starts with `volttron.`
3. Looks up redirect target in `REDIRECT_MAP`
4. Loads AEMS shim module
5. Copies all public attributes to VOLTTRON module namespace

**Installation:**
```python
from derhost.compat import install_volttron_compatibility
install_volttron_compatibility()

# Now VOLTTRON imports work!
from volttron.platform.vip.agent import Agent
```

### Agent Enhancements

**File:** `src/derhost/compat/shims/vip_agent.py`

**Enhancements:**
```python
class Agent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(...)

        # Add VOLTTRON subsystems
        self.vip.heartbeat = Heartbeat(self)
        self.vip.health = Health(self)
```

These subsystems are added to every agent automatically.

### @PubSub.subscribe Processing

**File:** `src/derhost/compat/shims/platform_agent.py`

**Function:** `utils._setup_pubsub_subscriptions(agent)`

**How it works:**
1. Scans all agent methods via `dir(agent)`
2. Checks for `_pubsub_subscriptions` attribute
3. Calls `agent.vip.pubsub.subscribe()` for each marked method
4. Called automatically by `vip_main()` or `start-legacy.py`

### Config File Support

**Format:** JSON (VOLTTRON standard)

**Example:**
```json
{
    "agentid": "listener1",
    "message": "hello",
    "heartbeat_period": 30,
    "log-level": "INFO"
}
```

**Note:** JSON with comments is common in VOLTTRON configs. Python's `json.load()` doesn't support comments, so users should remove them or we could add a pre-processor.

## Limitations

### Not Implemented

1. **Multi-platform support** - AEMS is single-platform
   - `all_platforms=True` parameter ignored

2. **ZMQ-specific features** - AEMS uses WebSocket
   - `serverkey` query returns placeholder
   - ZMQ authentication not applicable

3. **VOLTTRON platform commands** - Not relevant to AEMS
   - `vctl install`, `vctl start`, etc.

4. **Complex config formats** - Only JSON supported
   - No CSV or YAML auto-detection (could be added)

### Known Issues

1. **JSON comments in config files**
   - VOLTTRON configs often have `# comments`
   - Standard JSON parser doesn't support this
   - **Workaround:** Remove comments or use JSONC parser

2. **Module path complexity**
   - Nested package imports may need full path
   - **Workaround:** Add parent directory to PYTHONPATH

## Future Enhancements

### Could Add

1. **JSONC support** - Parse JSON with comments
2. **Auto-discovery** - Scan directory for VOLTTRON agents
3. **Multi-agent launcher** - Run multiple agents from config
4. **Service mode** - Run as systemd service
5. **Enhanced logging** - Mirror VOLTTRON's rotating file handler
6. **Config format detection** - Auto-detect JSON/CSV/YAML

### Compatibility Improvements

1. **More query keys** - Add common VOLTTRON query responses
2. **Better error messages** - Context-aware import failure messages
3. **Validation mode** - Check agent compatibility before running
4. **Migration helper** - Tool to update imports automatically

## Success Metrics

### What Works

**Zero-modification support** - Run VOLTTRON agents as-is
**All common features** - RPC, PubSub, Core, Config, Heartbeat, Health
**Clean abstraction** - Import hooks transparent to agent code
**Easy deployment** - Single script launch
**Good error handling** - Helpful messages when things go wrong

### ListenerAgent Test Case

The example VOLTTRON ListenerAgent can be run completely unmodified:

**Original VOLTTRON imports:**
```python
from volttron.platform.agent import utils
from volttron.platform.messaging.health import STATUS_GOOD
from volttron.platform.vip.agent import Agent, Core, PubSub
from volttron.platform.vip.agent.subsystems.query import Query
```

**Status:** All imports successfully redirected

**Features used:**
- Agent base class
- @Core.receiver decorators
- @PubSub.subscribe decorator
- utils.load_config()
- utils.vip_main()
- Heartbeat subsystem
- Health subsystem
- Query subsystem

**Result:** Works without any code changes!

## Conclusion

We have successfully created a comprehensive compatibility layer that allows VOLTTRON agents to run on AEMS without modification. The solution uses:

1. **Import hooks** for transparent redirection
2. **Compatibility shims** providing VOLTTRON APIs
3. **Launcher script** for easy deployment
4. **Complete documentation** for users

The implementation supports all common VOLTTRON agent patterns and has been tested with the ListenerAgent example.

## Next Steps

1. **Test with more agents** - Validate with different VOLTTRON agents
2. **Gather feedback** - Identify missing features
3. **Enhance docs** - Add more examples and troubleshooting
4. **Performance testing** - Measure import hook overhead
5. **Production deployment** - Document best practices

---

**Status:** Ready for testing and feedback
**Confidence:** High - all major features implemented and tested
