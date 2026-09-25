# Agent Configuration Files

This directory contains configuration files for legacy VOLTTRON and AEMS Edge agents.

## Directory Structure

```
configs/
├── platform-driver/      # PlatformDriverAgent configs
│   ├── config            # Main agent config
│   ├── devices/          # Device configs
│   └── registry_configs/ # Registry CSV files
├── historian/            # SQLHistorian configs
│   └── config            # Historian config
├── mqtt-historian/       # MQTTHistorian configs
│   └── config            # MQTT historian config
├── aems-manager/         # AEMS Manager configs
│   └── config            # Manager agent config
└── README.md             # This file
```

## Adding New Agent Configurations

### 1. Create Configuration Directory

```bash
mkdir -p configs/my-agent
```

### 2. Add Configuration File

Create `configs/my-agent/config` with your agent's configuration:

```json
{
    "setting1": "value1",
    "setting2": "value2"
}
```

### 3. Update agents-config.json

Add your agent to `agents-config.json`:

```json
{
  "agents": {
    "my_agent": {
      "enabled": true,
      "type": "volttron",
      "agent_dir": "/volttron/path/to/agent",
      "identity": "my.agent",
      "config_file": "configs/my-agent/config",
      "description": "My custom agent"
    }
  }
}
```

### 4. Regenerate docker-compose.yml

```bash
python generate-docker-compose.py
```

### 5. Start the agents

```bash
docker-compose up -d
```

## Example Configurations

### Platform Driver Config

Example `configs/platform-driver/config`:

```json
{
    "driver_scrape_interval": 0.05,
    "publish_breadth_first_all": false,
    "publish_depth_first": false,
    "publish_breadth_first": false
}
```

Example device config `configs/platform-driver/devices/campus/building/device`:

```json
{
    "driver_config": {
        "device_address": "10.0.0.100",
        "device_id": 1000
    },
    "driver_type": "bacnet",
    "registry_config": "config://registry_configs/device.csv",
    "interval": 60,
    "timezone": "US/Pacific"
}
```

### Historian Config

Example `configs/historian/config`:

```json
{
    "connection": {
        "type": "sqlite",
        "params": {
            "database": "/var/volttron/data/platform.historian.sqlite"
        }
    }
}
```

### AEMS Manager Config

Example `configs/aems-manager/config`:

```json
{
    "campus": "PNNL",
    "building": "BUILDING1",
    "log-level": "INFO"
}
```

## Config File Formats

Agents may support different configuration formats:

- **JSON**: Most common format (`.json`)
- **YAML**: Some agents support YAML (`.yaml`, `.yml`)
- **CSV**: Registry files for Platform Driver (`.csv`)
- **Plain text**: Some simple configs

## Mounting Configs in Docker

When you specify a `config_file` in `agents-config.json`, the generator automatically:

1. Mounts the config directory as read-only
2. Passes the config path to `start-legacy.py`
3. Makes configs available to the agent

Example docker-compose volume mount:

```yaml
volumes:
  - ./configs/platform-driver:/configs/platform-driver:ro
```

## Config Store vs Mounted Configs

There are two ways to provide configs to agents:

### 1. Mounted Configs (Recommended for Docker)

- Configs in `configs/` directory
- Mounted as Docker volumes
- Easy to edit and version control
- Specified in `agents-config.json`

### 2. Config Store (Runtime configs)

- Configs in VOLTTRON_HOME config store
- Can be updated at runtime via REST API
- Useful for dynamic configurations
- Located at `/var/volttron/aems_config_store/{identity}/`

**Tip**: Use mounted configs for initial setup, config store for runtime updates.

## Validating Configurations

Before starting agents, validate your configurations:

```bash
# Check JSON syntax
python -m json.tool configs/my-agent/config

# Test with dry-run
python generate-docker-compose.py --dry-run

# View agent summary
python generate-docker-compose.py --summary
```

## Troubleshooting

### Config Not Found

**Error**: `Config file not found`

**Solution**: Ensure config file exists and path in `agents-config.json` is correct:

```bash
ls -la configs/my-agent/config
```

### Permission Denied

**Error**: `Permission denied reading config`

**Solution**: Configs are mounted read-only (`:ro`). Check file permissions:

```bash
chmod 644 configs/my-agent/config
```

### Config Format Error

**Error**: Agent fails to parse config

**Solution**: Validate JSON syntax:

```bash
python -m json.tool configs/my-agent/config
```

## Best Practices

1. **Use JSON for new configs** - Most portable and well-supported
2. **Add comments in description** - Document complex settings
3. **Version control configs** - Commit `configs/` to git
4. **Separate secrets** - Don't commit sensitive data (use env vars)
5. **Test configs locally** - Validate before deploying

## Examples

See the Docker documentation for complete examples:

- [DOCKER_LEGACY_AGENTS.md](../DOCKER_LEGACY_AGENTS.md) - Running legacy agents
- [DOCKER.md](../DOCKER.md) - Complete Docker guide

## Reference

- VOLTTRON Documentation: https://volttron.readthedocs.io/
- Agent Config Store: See [config store guide](../docs/LEGACY_AGENT_SUPPORT.md)
