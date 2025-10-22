# AEMS FastAPI Documentation

Welcome to the AEMS FastAPI documentation! This directory contains comprehensive guides for using AEMS as a VOLTTRON-compatible agent communication library.

## 📖 Documentation Index

### Getting Started

1. **[Quick Start Guide](QUICK_START_LEGACY_AGENTS.md)**
   - 30-second quick start for running legacy VOLTTRON agents
   - Installation instructions with optional dependencies
   - Common usage patterns and examples
   - **Start here if you want to run existing VOLTTRON agents!**

### Legacy Agent Support

2. **[Legacy Agent Support](LEGACY_AGENT_SUPPORT.md)**
   - Comprehensive guide to running VOLTTRON agents on AEMS
   - Supported features and compatibility matrix
   - Detailed usage examples
   - Troubleshooting guide
   - Advanced usage patterns

3. **[vctl-Style Launcher](VCTL_STYLE_LAUNCHER.md)**
   - How `start-legacy.py` works like VOLTTRON's `vctl`
   - Auto-detection and working directory handling
   - Command-line reference
   - Directory structure requirements

### Technical Documentation

4. **[Legacy Wrapper Implementation](LEGACY_WRAPPER_SUMMARY.md)**
   - Technical implementation details
   - Import hook system architecture
   - Compatibility shim structure
   - VOLTTRON features implemented
   - Success metrics and test results

5. **[VOLTTRON Compatibility Status](VOLTTRON_COMPATIBILITY_STATUS.md)**
   - Feature-by-feature compatibility report
   - Test results and status
   - Known limitations
   - Next steps for full compatibility

### Background & Development

6. **[AI Transformation Journey](AI_TRANSFORMATION_JOURNEY.md)**
   - How this library was built with AI assistance
   - Development phases and patterns
   - Lessons learned from AI-assisted development
   - Tips for working with AI on complex projects

## 🎯 Quick Navigation

### By Use Case

**I want to run an existing VOLTTRON agent:**
→ [Quick Start Guide](QUICK_START_LEGACY_AGENTS.md)

**I want to understand what VOLTTRON features are supported:**
→ [Compatibility Status](VOLTTRON_COMPATIBILITY_STATUS.md)

**I want to understand how the compatibility layer works:**
→ [Legacy Wrapper Implementation](LEGACY_WRAPPER_SUMMARY.md)

**I'm having issues running my agent:**
→ [Legacy Agent Support - Troubleshooting](LEGACY_AGENT_SUPPORT.md#troubleshooting)

**I want to develop new features:**
→ [Development Guide](../CLAUDE.md)

### By Agent Type

**Historian Agents (SQLHistorian, MQTTHistorian):**
```bash
pip install -e ".[historians]"
./start-legacy.py --agent-dir /path/to/HistorianAgent --config config
```
See: [Quick Start - SQLHistorian](QUICK_START_LEGACY_AGENTS.md#sqlhistorian)

**PlatformDriverAgent:**
```bash
pip install -e ".[drivers]"
./start-legacy.py --agent-dir /path/to/PlatformDriverAgent --config config
```
See: [Quick Start - PlatformDriverAgent](QUICK_START_LEGACY_AGENTS.md#platformdriveragent)

**ILCAgent (Intelligent Load Control):**
```bash
pip install -e ".[ilc]"
./start-legacy.py --agent-dir /path/to/ILCAgent --config config
```
See: [Quick Start - ILCAgent](QUICK_START_LEGACY_AGENTS.md#ilcagent)

**Basic Agents (ListenerAgent, etc.):**
```bash
./start-legacy.py --agent-dir /path/to/ListenerAgent --config config
```
See: [Quick Start - ListenerAgent](QUICK_START_LEGACY_AGENTS.md#listeneragent-basic-agent)

## 📚 Additional Resources

- **[Main README](../README.md)** - Project overview and architecture
- **[Development Guide](../CLAUDE.md)** - Development commands and workflow
- **[API Documentation](http://localhost:8000/docs)** - Interactive API docs (when server is running)

## 🔧 Development Resources

### Code Quality Commands
```bash
# Format code
make format

# Run linting
make lint

# Run all tests
make test
```

See [Development Guide](../CLAUDE.md) for complete command reference.

### Test Infrastructure
- `tests/test_volttron_compatibility.py` - VOLTTRON compatibility tests
- `tests/test_config_store_*.py` - Config store behavior tests
- `tests/test_agent_*.py` - Agent communication tests

## 🤝 Contributing

When contributing documentation:
1. Keep examples practical and testable
2. Update the index when adding new docs
3. Cross-reference related documentation
4. Include troubleshooting sections
5. Test all code examples before committing

## 📝 Documentation Standards

- Use clear, descriptive headings
- Include code examples with expected output
- Provide both quick start and detailed explanations
- Add troubleshooting sections for common issues
- Cross-link related documentation
- Keep examples up-to-date with codebase changes

---

**Need help?** Start with the [Quick Start Guide](QUICK_START_LEGACY_AGENTS.md) or see the [Main README](../README.md) for overview.
