# Config Store Testing Documentation

## Overview
This document describes the comprehensive testing suite for the AEMS config store functionality, focusing on agent startup configuration loading and external configuration updates.

## Test Files

### test_config_store_agent_startup.py
Tests focusing on config loading at agent startup and external modifications:
- Agent loading existing configs at startup
- Loading multiple configs at startup
- External config updates via REST API
- File-based config updates
- Config deletion propagation
- Concurrent external updates
- Config persistence across reconnections
- Default and server config merging at startup

### test_config_store_comprehensive.py
Comprehensive tests covering complete workflows:
- Config loading via REST API before agent startup
- Agent-to-agent config updates
- Config persistence across agent restarts
- Default and server config merging
- Config deletion and callbacks
- Config listing functionality
- Concurrent config operations
- Config updates with defaults

### test_agent_config.py
Original test suite for basic config functionality:
- Config defaults only
- Server config only
- Config merging (server overrides defaults)
- Preserving defaults when not overridden
- Non-dict value handling
- Mixed dict and non-dict configs
- Config validation
- Agent-specific storage
- Config watch functionality
- Error handling

## Current Status

### ✅ Working Features
1. **Config Loading at Startup**
   - Agents successfully load configs stored via REST API before they start
   - Multiple configs are loaded correctly at startup
   - Configs persist across agent restarts

2. **Config Storage and Retrieval**
   - Configs can be stored via agent's `vip.config.set()` method
   - Configs can be retrieved via agent's `vip.config.get()` method
   - REST API PUT endpoint works for storing configs
   - REST API GET endpoint works for retrieving configs
   - REST API DELETE endpoint works for removing configs

3. **Config Merging**
   - Default configs merge properly with server configs
   - Server configs override matching keys in defaults
   - Non-matching default keys are preserved
   - Non-dict values completely override defaults

4. **Agent-Specific Storage**
   - Each agent has its own config namespace
   - Configs are isolated between agents
   - Same config name can have different values for different agents

5. **Concurrent Operations**
   - Multiple configs can be stored concurrently
   - System handles concurrent operations correctly

### ⚠️ Partial/Limited Features
1. **External Config Updates**
   - Updates via REST API are stored correctly
   - Notifications are sent to agents
   - However, agent cache invalidation needs manual intervention
   - Workaround: Clear cache and re-fetch

2. **Config Callbacks**
   - Subscribe mechanism exists
   - Callbacks are registered
   - However, callbacks are not always triggered on updates
   - Need to investigate event propagation

3. **File Watcher Updates**
   - File watcher is configured
   - Direct file modifications are detected
   - However, propagation to agents is inconsistent

### ❌ Not Working/Not Implemented
1. **Real-time External Update Propagation**
   - External updates don't immediately reflect in agent's cache
   - Agent needs to manually refresh or clear cache
   - WebSocket notifications arrive but don't trigger cache update

2. **Cross-Agent Config Updates**
   - Agent A cannot directly update Agent B's config
   - `set_for_agent()` method doesn't exist
   - Must use REST API as workaround

3. **Config Change Callbacks**
   - Callbacks registered via `subscribe()` don't fire consistently
   - DELETE callbacks particularly unreliable
   - Need to fix event propagation chain

## Test Execution

### Run All Config Tests
```bash
# Run all config-related tests
.venv/bin/pytest tests/test_*config*.py -v

# Run with coverage
.venv/bin/pytest tests/test_*config*.py --cov=aems.client.agent --cov=aems.server.config_store
```

### Run Specific Test Suites
```bash
# Startup and external update tests
.venv/bin/pytest tests/test_config_store_agent_startup.py -v

# Comprehensive workflow tests
.venv/bin/pytest tests/test_config_store_comprehensive.py -v

# Original config tests
.venv/bin/pytest tests/test_agent_config.py -v
```

### Run Individual Tests
```bash
# Test agent loading config at startup
.venv/bin/pytest tests/test_config_store_agent_startup.py::TestConfigStoreAgentStartup::test_agent_loads_existing_config_at_startup -v

# Test config merging
.venv/bin/pytest tests/test_agent_config.py::TestAgentConfig::test_config_merging_server_overrides_defaults -v
```

## Known Issues and Workarounds

### Issue 1: External Updates Not Reflected
**Problem**: When config is updated via REST API, agent's cached value doesn't update automatically.

**Workaround**:
```python
# Clear cache before getting updated value
if config_name in agent.vip.config._config_cache:
    del agent.vip.config._config_cache[config_name]
updated_value = agent.vip.config.get(config_name)
```

### Issue 2: Callbacks Not Firing
**Problem**: Subscribed callbacks don't fire on config changes.

**Workaround**: Poll for changes or use direct config retrieval instead of relying on callbacks.

### Issue 3: Cross-Agent Config Updates
**Problem**: Cannot update another agent's config directly from an agent.

**Workaround**: Use REST API directly:
```python
import requests
url = f"http://localhost:{port}/config-store/{target_agent_id}/{config_name}"
response = requests.put(url, json=config_data)
```

## Future Improvements

1. **Fix Cache Invalidation**
   - Implement proper cache invalidation when external updates occur
   - Ensure WebSocket notifications trigger cache refresh

2. **Implement Callback System**
   - Fix event propagation for config changes
   - Ensure callbacks fire for all change types (NEW, UPDATE, DELETE)

3. **Add set_for_agent Method**
   - Allow agents to update configs for other agents
   - Maintain proper access control

4. **Improve File Watcher**
   - Ensure file changes propagate to agents immediately
   - Add debouncing for rapid file changes

5. **Add Transaction Support**
   - Allow atomic updates of multiple configs
   - Support rollback on failure

## Test Coverage Summary

Current test coverage for config store functionality:
- Basic operations: 90%
- Startup loading: 85%
- External updates: 60%
- Callbacks: 30%
- File watching: 40%

Target coverage: >95% for all components
