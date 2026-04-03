# AEMS Platform Setup Guide

Three scenarios for connecting the AEMS platform to RTU devices:

1. **[Development](#scenario-1-development-sim-rtu)** — simulated devices via sim-rtu
2. **[Production](#scenario-2-production-real-hardware)** — physical Schneider/OpenStat thermostats + DENT meters via BACnet/IP
3. **[Mixed](#scenario-3-mixed-simulated--real)** — sim-rtu alongside real hardware

---

## Prerequisites

| Requirement | Version | Check |
|-------------|---------|-------|
| Python | 3.10+ | `python3 --version` |
| Git | any | `git --version` |
| Docker | 20.10+ (optional) | `docker --version` |

| Scenario | Additional Requirements |
|----------|------------------------|
| Development | Go 1.24+ (to build sim-rtu) |
| Production | BACnet/IP network, device IPs, BACnet device IDs |
| Mixed | Both of the above |

### Required Repositories

```
repos/
  aems-lib-fastapi/              # This repo
  volttron-pnnl-aems/            # AEMS Edge agents (for orchestration pipeline)
  sim-rtu/                       # Only for development/testing scenario
```

```bash
mkdir -p ~/repos && cd ~/repos
git clone <your-aems-lib-fastapi-repo> aems-lib-fastapi
git clone https://github.com/VOLTTRON/volttron-pnnl-aems.git
```

---

## Install aems-lib-fastapi

```bash
cd ~/repos/aems-lib-fastapi
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[pipeline,drivers]"
```

For Python 3.12+ with BACnet legacy driver:

```bash
pip install bacpypes==0.16.7 pyasyncore pyasynchat
```

See [GETTING_STARTED.md](GETTING_STARTED.md) for full dependency group reference.

---

## Scenario 1: Development (sim-rtu)

Use [sim-rtu](https://github.com/VOLTTRON/sim-rtu) to simulate Schneider, OpenStat, and DENT devices without physical hardware.

### 1.1 Build and Start sim-rtu

```bash
cd ~/repos
git clone https://github.com/VOLTTRON/sim-rtu.git
cd sim-rtu
make build
./bin/sim-rtu --config configs/default.yml
```

Verify sim-rtu is running:

```bash
curl http://127.0.0.1:8080/api/v1/status
curl http://127.0.0.1:8080/api/v1/devices
```

See the [sim-rtu setup guide](https://github.com/VOLTTRON/sim-rtu/blob/main/docs/setup-guide.md) for detailed sim-rtu configuration.

### 1.2 Configure AEMS

```bash
cd ~/repos/aems-lib-fastapi
cp config.ini.example config.ini
```

#### Choose a Driver

**Option A: NF Driver (recommended)**

```ini
[agents]
platform_driver = false
nf_driver = true
```

Install NF driver configs from sim-rtu:

```bash
~/repos/sim-rtu/scripts/switch-to-nf.sh ~/repos/aems-lib-fastapi
```

**Option B: Legacy BACnet Driver**

```ini
[agents]
platform_driver = true
nf_driver = false
```

Install BACnet configs from sim-rtu:

```bash
~/repos/sim-rtu/scripts/switch-to-bacnet.sh ~/repos/aems-lib-fastapi
```

### 1.3 Start the Platform

See [Starting the Platform](#starting-the-platform) below.

### 1.4 Verify

```bash
# Read a point through AEMS
curl http://127.0.0.1:8000/devices/SIM/RTU/Schneider/ZoneTemperature

# Write a setpoint
curl -X PUT http://127.0.0.1:8000/devices/SIM/RTU/Schneider/OccupiedCoolingSetPoint \
     -H "Content-Type: application/json" \
     -d '{"value": 73.0, "priority": 16}'

# All three simulated devices
curl http://127.0.0.1:8000/devices/SIM/RTU/Schneider
curl http://127.0.0.1:8000/devices/SIM/RTU/OpenStat
curl http://127.0.0.1:8000/devices/SIM/RTU/DENT
```

---

## Scenario 2: Production (Real Hardware)

Connect to physical Schneider SE8650 / OpenStat thermostats and DENT meters via BACnet/IP.

### 2.1 Network Requirements

- AEMS host on the same subnet as BACnet devices (or via BACnet router)
- UDP port **47808** open between AEMS host and devices
- If using NF gateway: HTTP access on port 8081

### 2.2 Discover BACnet Devices

```bash
pip install bacpypes==0.16.7

python3 -c "
from bacpypes.app import BIPSimpleApplication
from bacpypes.local.device import LocalDeviceObject
import time

device = LocalDeviceObject(objectIdentifier=('device', 999), objectName='discovery')
app = BIPSimpleApplication(device, '0.0.0.0')
app.who_is()
time.sleep(3)
for k, v in app.i_am_devices.items():
    print(f'Device {k}: address={v}')
"
```

Or verify specific device IPs:

```bash
ping -c 1 192.168.1.101   # thermostat 1
ping -c 1 192.168.1.102   # thermostat 2
ping -c 1 192.168.1.100   # DENT meter
```

### 2.3 Configure config.ini

```bash
cd ~/repos/aems-lib-fastapi
cp config.ini.example config.ini
```

Edit the key sections:

```ini
[site]
campus = PNNL
building = SEB
timezone = America/Los_Angeles
gateway_address = 192.168.1.1
stat_type = schneider          # or openstat

[device:1]
name = rtu01
address = 192.168.1.101        # actual device IP
device_id = 1001               # BACnet device instance
stat_type = schneider

[device:2]
name = rtu02
address = 192.168.1.102
device_id = 1002
stat_type = schneider

[meter]
name = meter
address = 192.168.1.100
device_id = 100
registry = dent.csv
```

### 2.4 Driver Configuration

#### Option A: NF Driver with Real Gateway

```ini
[agents]
platform_driver = false
nf_driver = true
```

NF driver config (`configs/nf-driver/config`):

```yaml
polling_interval: 60
driver_config:
  url: http://aems-gateway.local:8081
  client_id: my-client
  client_secret: my-secret
device_list:
- device_id: 1001
  registry_file: schneider.csv
  points_per_request: 25
  topic: PNNL/SEB/RTU01
- device_id: 1002
  registry_file: schneider.csv
  points_per_request: 25
  topic: PNNL/SEB/RTU02
```

#### Option B: BACnet Driver (Direct)

No gateway needed — direct BACnet/IP:

```ini
[agents]
platform_driver = true
nf_driver = false
```

Device config (`configs/platform-driver/devices/rtu01.config`):

```json
{
    "driver_config": {
        "device_address": "192.168.1.101",
        "device_id": 1001
    },
    "driver_type": "bacnet",
    "registry_config": "config://registry_configs/schneider.csv",
    "interval": 60,
    "timezone": "US/Pacific",
    "heart_beat_point": "HeartBeat"
}
```

DENT meter config (`configs/platform-driver/devices/meter.config`):

```json
{
    "driver_config": {
        "device_address": "192.168.1.100",
        "device_id": 100
    },
    "driver_type": "bacnet",
    "registry_config": "config://registry_configs/dent.csv",
    "interval": 60,
    "timezone": "US/Pacific"
}
```

### 2.5 Registry CSVs

The same registry files work for both simulated and real devices:

| File | Device | Points |
|------|--------|--------|
| `schneider.csv` | Schneider SE8650 | ~60 points (temps, setpoints, modes, stages) |
| `dent.csv` | DENT power meter | ~40 points (voltage, current, power, PF, THD) |

### 2.6 Thermostat Configuration

Site-specific thermostat config (e.g., `configs/aems-manager/schneider.config`):

```json
{
    "campus": "PNNL",
    "building": "SEB",
    "system": "SCHNEIDER",
    "system_status_point": "OccupancyCommand",
    "setpoint_control": 1,
    "local_tz": "US/Pacific",
    "default_setpoints": {
        "UnoccupiedHeatingSetPoint": 65,
        "UnoccupiedCoolingSetPoint": 78,
        "DeadBand": 3,
        "OccupiedSetPoint": 71
    },
    "schedule": {
        "Monday":    {"start": "6:00", "end": "18:00"},
        "Tuesday":   {"start": "6:00", "end": "18:00"},
        "Wednesday": {"start": "6:00", "end": "18:00"},
        "Thursday":  {"start": "6:00", "end": "18:00"},
        "Friday":    {"start": "6:00", "end": "18:00"},
        "Saturday":  "always_off",
        "Sunday":    "always_off"
    },
    "occupancy_values": {
        "occupied": 2,
        "unoccupied": 3
    }
}
```

Key fields to customize per site:

| Field | Description |
|-------|-------------|
| `campus` / `building` / `system` | Must match your topic hierarchy |
| `setpoint_control` | `1` = Schneider dual-setpoint, `0` = OpenStat single-setpoint |
| `default_setpoints` | Unoccupied fallback temperatures |
| `schedule` | Occupied hours per day of week |
| `occupancy_values` | Schneider: `2`/`3`, OpenStat: `1`/`0` |

### 2.7 Verify

```bash
# Check BACnet connectivity
nc -zu 192.168.1.101 47808

# Read a point through AEMS
curl http://127.0.0.1:8000/devices/PNNL/SEB/RTU01/ZoneTemperature

# Write a setpoint
curl -X PUT http://127.0.0.1:8000/devices/PNNL/SEB/RTU01/OccupiedCoolingSetPoint \
     -H "Content-Type: application/json" \
     -d '{"value": 74.0, "priority": 16}'

# Check AEMS logs for polling activity
tail -f /var/log/aems/platform.driver.log
```

---

## Scenario 3: Mixed (Simulated + Real)

Run sim-rtu for development devices alongside real hardware. Develop against simulated devices, then verify on physical RTUs.

### 3.1 Setup

1. Complete [Scenario 1](#scenario-1-development-sim-rtu) (sim-rtu running)
2. Add real devices to `config.ini` alongside simulated ones

### 3.2 config.ini

```ini
[site]
campus = PNNL
building = SEB
timezone = America/Los_Angeles
gateway_address = 192.168.1.1
stat_type = schneider

# --- Simulated devices (from sim-rtu) ---
[device:1]
name = sim-schneider
address = 127.0.0.1             # sim-rtu address
device_id = 86254               # sim-rtu default device ID
stat_type = schneider

# --- Real devices ---
[device:2]
name = rtu01
address = 192.168.1.101
device_id = 1001
stat_type = schneider

[device:3]
name = rtu02
address = 192.168.1.102
device_id = 1002
stat_type = schneider

[meter]
name = meter
address = 192.168.1.100
device_id = 100
registry = dent.csv
```

### 3.3 Topic Separation

Use different topic prefixes to distinguish simulated vs real:

| Device | Topic | Source |
|--------|-------|--------|
| sim-schneider | `SIM/RTU/Schneider` | sim-rtu on localhost |
| rtu01 | `PNNL/SEB/RTU01` | Real Schneider SE8650 |
| rtu02 | `PNNL/SEB/RTU02` | Real Schneider SE8650 |
| meter | `PNNL/SEB/METER` | Real DENT meter |

### 3.4 Verify

```bash
# Simulated device
curl http://127.0.0.1:8000/devices/SIM/RTU/Schneider/ZoneTemperature

# Real device
curl http://127.0.0.1:8000/devices/PNNL/SEB/RTU01/ZoneTemperature
```

---

## Starting the Platform

Two paths: orchestration pipeline (Docker) or manual (development).

### Orchestration Pipeline

Generates agent configs and Docker Compose from `config.ini`. See [PIPELINE.md](PIPELINE.md) for full details.

```bash
cd ~/repos/aems-lib-fastapi
source .venv/bin/activate
python orchestrate.py

# Then start via Docker:
cd ~/repos/volttron-pnnl-aems/aems-app/docker
docker compose --profile fastapi --profile fastapi-agents up -d
```

Pipeline flags:

| Flag | Purpose |
|------|---------|
| `--config my-site.ini` | Use a different config file |
| `--dry-run` | Preview without writing files |
| `--skip-configs` | Skip if `configs/` already exists |
| `--aems-edge-path /path` | Override default aems-edge location |

### Manual (start-legacy.py)

Run each agent individually. See [QUICK_START_LEGACY_AGENTS.md](QUICK_START_LEGACY_AGENTS.md) for full details.

```bash
cd ~/repos/aems-lib-fastapi
source .venv/bin/activate

# Terminal 1: Start server
aems-server --host 127.0.0.1 --port 8000

# Terminal 2: Start agents
python orchestrate.py
```

Or start agents individually:

```bash
./start-legacy.py --agent-dir /path/to/agent --config config --identity agent.name
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Address already in use` on 47808 | Another process on that UDP port. `lsof -i UDP:47808` |
| `No module named asyncore` | Python 3.12+ removed asyncore. `pip install pyasyncore pyasynchat` |
| `No response from device` (BACnet) | Check `device_address` in config. Use `172.17.0.1` for Docker. |
| Connection refused on 8080 | sim-rtu not running or bound to `127.0.0.1`. Use `0.0.0.0` for Docker. |
| AEMS not polling | Check `polling_interval` in NF config. Default is 60s. |
| `bacpypes` import errors | Must be `bacpypes==0.16.7` exactly (not bacpypes3). |
| Config changes not taking effect | AEMS reads configs at startup. Restart after changes. |
| `orchestrate.py` exits code 2 | `volttron-pnnl-aems/aems-edge` not found. Use `--aems-edge-path`. |
| Docker: can't reach sim-rtu | Use `172.17.0.1` (docker0 bridge) or container name. |
| Firewall blocking BACnet | Open UDP 47808: `sudo ufw allow 47808/udp` |
| Wrong BACnet device ID | Run WhoIs discovery (see [2.2](#22-discover-bacnet-devices)). |
| NF gateway unreachable | Verify URL, check OAuth credentials, `curl http://<gateway>:8081/health`. |
| Wrong occupancy behavior | Schneider: `2`=occupied/`3`=unoccupied. OpenStat: `1`/`0`. |
| Intermittent BACnet reads | Reduce `points_per_request` to avoid timeouts. |
