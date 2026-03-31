# AEMS Docker Integration Pipeline

End-to-end guide for generating agent configurations and Docker Compose files from a single `config.ini`.

## Overview

The pipeline transforms a human-readable `config.ini` into a fully runnable Docker Compose stack. Three scripts execute in sequence, each producing the input for the next:

```
config.ini
    |
    v
[Step 1] generate_configs.py        (from aems-edge repo)
    |        Produces: configs/ directory with agent config files
    |        (BACnet device configs, historian, weather, manager, ILC, etc.)
    v
[Step 2] generate-agents-config.py   (this repo)
    |        Produces: agents-config.json
    |        (Structured JSON describing every agent, validated against schema)
    v
[Step 3] generate-docker-compose.py  (this repo)
    |        Produces: docker-compose.yml
    |        (One service per enabled agent + the AEMS FastAPI server)
    v
docker compose up -d
```

`orchestrate.py` wraps all three steps into a single command.

## Prerequisites

### Required Repositories

```
parent-directory/
  aems-lib-fastapi/          # This repo (Steps 2, 3, orchestrator)
  volttron-pnnl-aems/        # Contains aems-edge (Step 1)
    aems-edge/
      configurations/
        docker/
          generate_configs.py
```

The orchestrator expects `volttron-pnnl-aems/aems-edge` to be a sibling of `aems-lib-fastapi` by default:

```
../volttron-pnnl-aems/aems-edge
```

Override with `--aems-edge-path` if your layout differs.

### Python Dependencies

Step 1 (`generate_configs.py`) requires:
- `configargparse`
- `netifaces`
- `pyyaml`

Steps 2 and 3 use only the Python standard library (no extra packages).

### Docker

Docker and Docker Compose must be installed to run the generated stack.

## Configuration: config.ini

The `config.ini` file is the single source of truth. All pipeline scripts read from it.

### Section Reference

| Section | Purpose | Used By |
|---------|---------|---------|
| `[site]` | Campus, building, timezone, gateway IP, thermostat type | Steps 1, 2 |
| `[global]` | Server address, VOLTTRON_HOME, restart policy, logging | Step 2 |
| `[agents]` | Enable/disable toggles for each agent (true/false) | Step 2 |
| `[aems_manager]` | Manager agent_dir, identity, prefix, num_devices | Step 2 |
| `[nf_driver]` | Normal Framework BACnet driver settings | Step 2 |
| `[platform_driver]` | Legacy VOLTTRON platform driver settings | Step 2 |
| `[device:N]` | Per-device BACnet config (address, device_id, stat_type) | Step 1 |
| `[meter]` | Power meter device config | Step 1 |
| `[database]` | DB connection for historian (name, user, password, host, port) | Step 1 |
| `[historian]` | SQL historian agent_dir, identity, config_file, volumes | Step 2 |
| `[mqtt_historian]` | MQTT historian settings | Step 2 |
| `[weather]` | Weather.gov agent + station ID | Steps 1, 2 |
| `[ilc]` | Intelligent Load Control agent settings, depends_on | Step 2 |
| `[emailer]` | Email notification agent settings | Steps 1, 2 |
| `[listener]` | Debug/example listener agent | Step 2 |
| `[scheduler_example]` | Scheduler example agent (disabled by default) | Step 2 |
| `[web_agent_example]` | Web agent example with port mappings (disabled by default) | Step 2 |

### Key Settings

**`[site]`** -- Physical deployment identity:
```ini
[site]
campus = PNNL
building = SEB
timezone = America/Los_Angeles
gateway_address = 192.168.1.1
stat_type = schneider          # schneider or openstat
```

**`[global]`** -- Container runtime defaults:
```ini
[global]
server_address = ws://aems-fastlib-server:8000
volttron_home = /var/volttron
config_base_dir = /app
restart_policy = unless-stopped
log_dir = /var/log/aems
enable_agent_logs = true
```

**`[agents]`** -- Master enable/disable switches:
```ini
[agents]
listener = true
platform_driver = false
nf_driver = true
sql_historian = true
aems_manager = true
weather_dot_gov = true
ilc = true
```

**`[aems_manager]`** -- Multi-device expansion:
```ini
[aems_manager]
prefix = rtu
num_devices = 1       # Set >1 to generate aems_manager_rtu01, aems_manager_rtu02, etc.
identity = manager.rtu02
```

When `num_devices > 1`, the pipeline generates one manager container per device with identities `manager.<prefix>01`, `manager.<prefix>02`, etc.

## Pipeline Steps

### Step 1: generate_configs.py (aems-edge)

**Location:** `volttron-pnnl-aems/aems-edge/configurations/docker/generate_configs.py`

**What it does:** Reads site parameters and generates all the agent configuration files that will be mounted into containers. This includes BACnet device configs, registry CSVs, historian config, weather config, manager configs, ILC topic watcher config, and platform config YAML.

**Inputs:**
- `config.ini` values (translated to flat key=value format by the orchestrator)
- `--output-dir` specifying where to write files

**Outputs:** A `configs/` directory tree:
```
configs/
  configuration_store/
    platform.driver/
      devices/<campus>/<building>/<device>.json
      registry_configs/
        schneider.csv
        dent.csv
    manager.<prefix>XX/
      devices/<campus>/<building>/<device>.json
  bacnet_proxy.config
  bacnet.config
  driver.config
  historian.config
  weather.config
  emailer.config
  topic_watcher.config
  manager.<prefix>XX.config
  platform_config.yml
```

**Key CLI args** (all handled automatically by the orchestrator):
- `--campus`, `--building`, `--gateway-address` (required)
- `--num-configs` (number of devices)
- `--stat-type` (schneider or openstat)
- `--output-dir` (required)
- `--timezone`, `--db-name`, `--db-user`, `--db-password`, `--weather-station`, etc.

**Note:** This script uses `configargparse`, which auto-reads a `config.ini` in the current working directory. The orchestrator writes a temporary flat config file and runs the script from that temp directory.

### Step 2: generate-agents-config.py

**Location:** `aems-lib-fastapi/generate-agents-config.py`

**What it does:** Reads `config.ini` and produces a structured `agents-config.json` that describes every agent -- its type, directory, identity, config file path, volumes, ports, dependencies, and enabled/disabled state.

**Inputs:**
- `--config config.ini`

**Outputs:**
- `agents-config.json` (validated against `agents-config.schema.json`)

**Agent types:**
- `volttron` -- standard VOLTTRON agents (listener, platform_driver, sql_historian, etc.)
- `aems-edge` -- PNNL AEMS agents (nf_driver, aems_manager)

**Standalone usage:**
```bash
python generate-agents-config.py --config config.ini --output agents-config.json

# Preview without writing:
python generate-agents-config.py --config config.ini --dry-run

# Validate only:
python generate-agents-config.py --config config.ini --validate-only
```

### Step 3: generate-docker-compose.py

**Location:** `aems-lib-fastapi/generate-docker-compose.py`

**What it does:** Reads `agents-config.json` and generates a `docker-compose.yml` with:
1. An `aems-fastlib-server` service (the FastAPI WebSocket server)
2. One service per **enabled** agent, each running `start-legacy.py`

**Inputs:**
- `--config agents-config.json`

**Outputs:**
- `docker-compose.yml`

Every agent container:
- Uses the same `aems-fastapi:latest` image
- Shares a `volttron-home` named volume
- Depends on the server being healthy
- Runs `python /app/start-legacy.py --agent-dir <dir> --config <path> --identity <id> --address ws://aems-fastlib-server:8000`

**Config file mounting logic:**
- Relative path (e.g., `configs/historian/config`) -- mounted from the host into the container at `<config_base_dir>/<path>`
- Absolute path (e.g., `/volttron/examples/.../config`) -- used directly inside the container (file already exists in the image)
- `null` -- agent uses default `config` in its agent_dir

**Standalone usage:**
```bash
python generate-docker-compose.py --config agents-config.json --output docker-compose.yml

# Preview:
python generate-docker-compose.py --config agents-config.json --dry-run

# Summary only:
python generate-docker-compose.py --config agents-config.json --summary
```

## Using the Orchestrator

`orchestrate.py` chains all three steps with a single command.

### Basic Usage

```bash
# Run the full pipeline with defaults:
python orchestrate.py

# Use a custom config file:
python orchestrate.py --config my-site.ini

# Preview what would happen without writing anything:
python orchestrate.py --dry-run
```

### Skipping Steps

```bash
# Skip Step 1 if configs/ already exists:
python orchestrate.py --skip-configs

# Skip Steps 1 and 2 if agents-config.json already exists:
python orchestrate.py --skip-configs --skip-agents-config

# Skip only Step 2 (re-run Step 1 and Step 3):
python orchestrate.py --skip-agents-config
```

### Custom Paths

```bash
# Point to aems-edge in a non-default location:
python orchestrate.py --aems-edge-path /opt/volttron-pnnl-aems/aems-edge

# Custom output locations:
python orchestrate.py \
  --configs-output-dir ./my-configs \
  --agents-config ./my-agents-config.json \
  --compose-output ./my-docker-compose.yml
```

### All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config.ini` | Path to the INI config file |
| `--agents-config` | `agents-config.json` | Output path for agents-config.json |
| `--compose-output` | `docker-compose.yml` | Output path for docker-compose.yml |
| `--configs-output-dir` | `./configs` | Directory for Step 1 output |
| `--aems-edge-path` | `../volttron-pnnl-aems/aems-edge` | Path to aems-edge checkout |
| `--dry-run` | off | Show commands without executing |
| `--skip-configs` | off | Skip Step 1 |
| `--skip-agents-config` | off | Skip Step 2 |

## Overriding Values

Use `--passthrough` to forward arguments directly to Step 1's `generate_configs.py` as CLI overrides. Everything after `--passthrough` is passed through verbatim:

```bash
# Override campus and number of devices for Step 1:
python orchestrate.py --passthrough --campus DIFFERENT_CAMPUS --num-configs 5

# Override gateway address:
python orchestrate.py --passthrough --gateway-address 10.0.0.1
```

The orchestrator translates `config.ini` sections into the flat key=value format that `configargparse` expects. The mapping is:

| config.ini Section | config.ini Key | Flat Key (Step 1) |
|-------------------|---------------|-------------------|
| `[site]` | `campus` | `campus` |
| `[site]` | `building` | `building` |
| `[site]` | `timezone` | `timezone` |
| `[site]` | `gateway_address` | `gateway-address` |
| `[site]` | `stat_type` | `stat-type` |
| `[aems_manager]` | `prefix` | `prefix` |
| `[aems_manager]` | `num_devices` | `num-configs` |
| `[weather]` | `station` | `weather-station` |
| `[database]` | `db_name` | `db-name` |
| `[database]` | `db_user` | `db-user` |
| `[database]` | `db_password` | `db-password` |
| `[database]` | `db_address` | `db-address` |
| `[database]` | `db_port` | `db-port` |

CLI overrides take precedence over config.ini values.

## Docker Profiles (volttron-pnnl-aems Integration)

When running as part of the full `volttron-pnnl-aems` stack (in `aems-app/docker/`), the FastAPI services are gated behind Docker Compose profiles:

```bash
# Start only the FastAPI server and agent containers:
docker compose --profile fastapi up -d

# Start only agent containers (assumes server is already running):
docker compose --profile fastapi-agents up -d
```

**Profile assignments in volttron-pnnl-aems:**

| Service | Profiles |
|---------|----------|
| `aems-fastapi-server` | `fastapi` |
| `aems-listener` | `fastapi`, `fastapi-agents` |
| `aems-sql-historian` | `fastapi`, `fastapi-agents` |
| `aems-manager` | `fastapi`, `fastapi-agents` |
| `aems-weather-dot-gov` | `fastapi`, `fastapi-agents` |
| `aems-nf-driver` | `fastapi`, `fastapi-agents` |
| `aems-ilc` | `fastapi`, `fastapi-agents` |

In the integrated stack, the compose file uses:
- `${COMPOSE_CONTAINER_REGISTRY}/${COMPOSE_PROJECT_NAME}/aems-fastapi:${TAG}` for images
- `.env.aems-fastapi` for environment variables
- `aems-volttron-home` and `aems-agent-logs` for shared volumes
- The Docker socket mounted into the server for container management

## File Layout

### Generated Files (Standalone)

After running the full pipeline from `aems-lib-fastapi/`:

```
aems-lib-fastapi/
  config.ini.example            # Template: copy to config.ini and edit
  config.ini                    # Input: your site configuration (git-ignored)
  agents-config.json            # Generated: agent definitions (Step 2)
  agents-config.schema.json     # Validation schema
  docker-compose.yml            # Generated: Docker Compose file (Step 3)
  configs/                      # Generated: agent config files (Step 1)
    configuration_store/
      platform.driver/
        devices/PNNL/SEB/*.json
        registry_configs/*.csv
      manager.rtu01/
        devices/PNNL/SEB/*.json
    bacnet_proxy.config
    driver.config
    historian.config
    weather.config
    manager.rtu01.config
    topic_watcher.config
    platform_config.yml
```

### Container Mount Points

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `volttron-home` (named volume) | `/var/volttron` | Shared VOLTTRON state |
| `agent-logs` (named volume) | `/var/log/aems` | Agent log files |
| `./configs/<agent>/` | `/app/configs/<agent>/:ro` | Agent config files (read-only) |
| `/var/run/docker.sock` | `/var/run/docker.sock:ro` | Server only: container management |

### What Lives in the Docker Image

The Dockerfile clones these repos into the image at build time:
- `/volttron` -- VOLTTRON 9.0.4 (legacy agent source code)
- `/volttron-pnnl-aems` -- AEMS Edge agents (Manager, Normal Framework driver)
- `/volttron-pnnl-applications` -- ILC agent
- `/app` -- The aems-lib-fastapi application code and virtualenv

Agent containers reference these paths via `agent_dir` in their config.

## Standalone vs Integrated

### Standalone (aems-lib-fastapi only)

Use when developing or testing the FastAPI layer independently.

```bash
cd aems-lib-fastapi

# Copy the template and edit for your site:
cp config.ini.example config.ini
# Run the pipeline:
python orchestrate.py

# Start the stack:
docker compose up -d

# Check health:
curl http://localhost:5410/health/
```

The generated `docker-compose.yml` builds the image locally from the Dockerfile and creates its own network and volumes.

### Integrated (volttron-pnnl-aems)

Use for full AEMS deployment alongside the VOLTTRON platform, database, and web application.

```bash
cd volttron-pnnl-aems/aems-app/docker

# The compose file references ../../aems-lib-fastapi as build context.
# Config files are pre-generated and mounted.

# Start the FastAPI profile:
docker compose --profile fastapi up -d

# Or start everything:
docker compose --profile fastapi --profile volttron up -d
```

Key differences in integrated mode:
- Image names use registry/project prefix variables
- Environment loaded from `.env.aems-fastapi`
- Services depend on an `init` service that runs first
- Volumes are prefixed with `aems-`
- Agent containers use both `fastapi` and `fastapi-agents` profiles

## Troubleshooting

### "Could not find aems-edge with configurations/docker/generate_configs.py"

The orchestrator cannot locate the `generate_configs.py` script in the aems-edge repo.

**Fix:** Use `--aems-edge-path` to point to the correct location:
```bash
python orchestrate.py --aems-edge-path /path/to/volttron-pnnl-aems/aems-edge
```

Or ensure the repo is cloned as a sibling directory: `../volttron-pnnl-aems/aems-edge`.

### "Config file not found: config.ini"

No `config.ini` in the current directory or specified path.

**Fix:** Copy the sample and edit it:
```bash
cp config.ini.example config.ini   # copy the template and edit for your site
python orchestrate.py --config /full/path/to/config.ini
```

### Step 1 fails with missing required arguments

`generate_configs.py` requires `--campus`, `--building`, and `--gateway-address`. These come from `[site]` in config.ini.

**Fix:** Ensure your `[site]` section has all three:
```ini
[site]
campus = PNNL
building = SEB
gateway_address = 192.168.1.1
```

### Agent container exits immediately

The agent's `agent_dir` path does not exist inside the container.

**Fix:** Check that the `agent_dir` in config.ini points to a directory that exists in the Docker image. Standard paths:
- `/volttron/examples/ListenerAgent`
- `/volttron/services/core/SQLHistorian`
- `/volttron-pnnl-aems/aems-edge/Manager`
- `/volttron-pnnl-aems/aems-edge/Normal`
- `/volttron-pnnl-applications/GridServices/Control/ILCAgent`

### Agent cannot connect to server

The agent's `--address` does not resolve to the running server.

**Fix:** Ensure the server container name matches the address in `[global]`:
```ini
[global]
server_address = ws://aems-fastlib-server:8000
```

And both containers are on the same Docker network (`aems-network`).

### Config files not found inside container

The config_file path in agents-config.json does not match what is mounted.

**Fix:** Relative paths (e.g., `configs/historian/config`) are mounted at `<config_base_dir>/<path>`. Check that:
1. The file exists on the host at `./configs/historian/config`
2. `config_base_dir` in `[global]` matches the mount target (default: `/app`)
3. The compose file mounts `./configs/historian:/app/configs/historian:ro`

### Validation errors from generate-agents-config.py

The generated JSON does not match the schema.

**Fix:** Run validation independently to see details:
```bash
python generate-agents-config.py --config config.ini --validate-only
```

Common causes:
- Enabled agent with empty `agent_dir`
- Invalid `restart_policy` (must be: `no`, `always`, `unless-stopped`, `on-failure`)
- `depends_on` referencing an agent name that does not exist in `[agents]`
- Invalid port format (must be `host:container`, e.g., `8080:8080`)

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Config file not found |
| 2 | aems-edge path not found |
| 10 | Step 1 (generate_configs.py) failed |
| 20 | Step 2 (generate-agents-config.py) failed |
| 30 | Step 3 (generate-docker-compose.py) failed |
