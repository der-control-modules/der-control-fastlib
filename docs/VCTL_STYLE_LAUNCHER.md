# vctl-Style Agent Launcher

## Overview

The enhanced `start-legacy.py` script now works **exactly like `vctl start`** in VOLTTRON:

1. **Changes to agent directory** before running
2. **Auto-detects** module and class from directory structure
3. **Config paths** relative to agent directory
4. **Working directory** is the agent's directory during execution

## Key Changes

### Before (Old Approach)
```bash
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config \
    --identity listener
```

**Problems:**
- Had to specify full module path
- Config path from script location
- Working directory was script location
- Import errors with `volttron.platform.agent`

### After (vctl-Style)
```bash
./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \
    --config config \
    --identity listener
```

**Benefits:**
- Auto-detects module (`listener.agent`) and class (`ListenerAgent`)
- Config path relative to agent directory
- Working directory **IS** the agent directory (like vctl!)
- Proper import resolution

## How It Works

### 1. Auto-Detection

The script scans the agent directory for the standard VOLTTRON structure:

```
ListenerAgent/                    <- --agent-dir points here
+-- listener/                     <- Auto-detected package
|   +-- __init__.py
|   +-- agent.py                 <- Auto-detected module
|   |   +-- class ListenerAgent  <- Auto-detected class
|   +-- settings.py
+-- config                        <- Config relative to agent dir
+-- setup.py
```

**Detection logic:**
1. Finds subdirectory with `agent.py` file
2. Reads `agent.py` to find class inheriting from `Agent`
3. Constructs module path: `{package}.agent`

### 2. Working Directory Change

```python
# Before importing, change to agent directory
os.chdir(agent_dir)  # e.g., /path/to/ListenerAgent
sys.path.insert(0, str(agent_dir))

# Now import works like: python -m listener.agent
agent_module = importlib.import_module("listener.agent")
```

This means:
- Any relative file paths in agent code work correctly
- Imports like `from listener.settings import X` work
- Config file resolution is relative to agent directory

### 3. Config Path Resolution

Config path priority:
1. **Agent directory** - `{agent_dir}/{config_path}` (preferred)
2. **Absolute path** - If config path is absolute
3. **Current directory** - Fallback

Example:
```bash
--agent-dir example-from-volttron/ListenerAgent --config config
```
Resolves to: `example-from-volttron/ListenerAgent/config`

## Usage Examples

### Example 1: ListenerAgent (Minimal)
```bash
./start-legacy.py --agent-dir example-from-volttron/ListenerAgent
```

Auto-detects:
- Module: `listener.agent`
- Class: `ListenerAgent`
- Identity: `listeneragent` (from class name)

### Example 2: With Config
```bash
./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \
    --config config \
    --identity my_listener
```

Config resolves to: `example-from-volttron/ListenerAgent/config`

### Example 3: Absolute Config Path
```bash
./start-legacy.py --agent-dir /opt/agents/MyAgent \
    --config /etc/volttron/agents/myagent.json \
    --identity my_agent
```

### Example 4: Custom Server
```bash
./start-legacy.py --agent-dir ~/agents/SensorAgent \
    --config config.json \
    --address wss://aems-prod.example.com:8443 \
    --identity sensor1
```

## Command-Line Reference

```
./start-legacy.py --agent-dir <path> [options]

Required:
  --agent-dir PATH        Path to agent directory

Optional:
  --config PATH          Config file (relative to agent-dir or absolute)
  --identity NAME        Agent identity (default: lowercase class name)
  --address URL          Message bus URL (default: ws://localhost:8000)
  --volttron-home PATH   VOLTTRON_HOME directory
  --debug                Enable debug logging
  --help                 Show help message
```

## Directory Structure Requirements

Your agent directory must follow VOLTTRON convention:

```
AgentName/                     <- --agent-dir points here
+-- package_name/              <- Agent package (any name)
|   +-- __init__.py           <- Required
|   +-- agent.py              <- Required (contains Agent class)
|   +-- ... (other modules)
+-- config                     <- Optional config file
+-- setup.py                   <- Optional
+-- ... (other files)
```

**What gets auto-detected:**
- Package name from subdirectory containing `agent.py`
- Class name by scanning `agent.py` for `class X(Agent):`

## Comparison with vctl

| Feature | vctl start | start-legacy.py |
|---------|-----------|-----------------|
| Change to agent dir | Yes | Yes |
| Auto-detect module | Yes | Yes |
| Config relative to agent dir | Yes | Yes |
| Working directory | Agent dir | Agent dir |
| Import hooks | VOLTTRON platform | AEMS compat layer |

## Troubleshooting

### "Could not find agent.py"

**Error:**
```
FileNotFoundError: Could not find agent.py in any subdirectory
```

**Solution:** Ensure your agent has the structure:
```
AgentDir/
  +-- some_package/
      +-- agent.py
```

### "Config file not found"

**Warning:**
```
Config file not found: config
  Tried: /path/to/AgentDir/config
```

**Solution:**
- Check config file exists in agent directory
- Or provide absolute path: `--config /full/path/to/config.json`

### Module import errors

**Error:**
```
ModuleNotFoundError: No module named 'listener'
```

**Solution:**
- Ensure you're using `--agent-dir` (not old module path syntax)
- Check agent directory has proper package structure with `__init__.py`

## Migration from Old Syntax

### Old Syntax (Deprecated)
```bash
./start-legacy.py example-from-volttron.ListenerAgent.listener.agent:ListenerAgent \
    --config example-from-volttron/ListenerAgent/config
```

### New Syntax (vctl-style)
```bash
./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \
    --config config
```

**Benefits of new syntax:**
- Simpler command line
- Matches VOLTTRON vctl behavior
- Auto-detects module and class
- Proper working directory handling

## Testing

Run the test script:
```bash
./test-legacy-agent.sh
```

This will:
1. Check AEMS server is running
2. Launch ListenerAgent with new syntax
3. Show debug output

Expected output:
```
============================================================
AEMS Legacy VOLTTRON Agent Launcher (vctl-style)
============================================================
Agent Directory: /path/to/der-control-fastlib/example-from-volttron/ListenerAgent
Auto-detected agent: listener.agent:ListenerAgent
------------------------------------------------------------
Agent Module:  listener.agent
Agent Class:   ListenerAgent
Identity:      test_listener
Address:       ws://localhost:8000
Config:        /home/.../ListenerAgent/config
------------------------------------------------------------
Changed working directory to: /home/.../ListenerAgent
Connected to ws://localhost:8000
============================================================
ListenerAgent is running from ListenerAgent/
  Press Ctrl+C to stop
============================================================
```

## Summary

The enhanced `start-legacy.py` now provides a **vctl-compatible experience**:

- **Auto-detection** - No need to specify module:class
- **Agent directory** - Working directory is agent's directory
- **Relative configs** - Config paths relative to agent dir
- **Clean syntax** - Simple, intuitive command line

This makes running VOLTTRON agents on AEMS feel natural and familiar!
