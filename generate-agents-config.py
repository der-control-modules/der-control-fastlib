#!/usr/bin/env python3
"""
Generate agents-config.json from a config.ini file.

Reads a site-level config.ini and produces an agents-config.json that is
directly consumable by generate-docker-compose.py.

Usage:
    python generate-agents-config.py
    python generate-agents-config.py --config my-site.ini --output agents-config.json
    python generate-agents-config.py --config my-site.ini --dry-run
    python generate-agents-config.py --config my-site.ini --validate-only
"""

import argparse
import configparser
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema validation (uses only the stdlib; no jsonschema dependency)
# ---------------------------------------------------------------------------

VALID_AGENT_TYPES = {"volttron", "aems-edge"}
VALID_RESTART_POLICIES = {"no", "always", "unless-stopped", "on-failure"}
AGENT_NAME_PATTERN_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
PORT_PATTERN_CHARS = set("0123456789:")

# Agents that are always typed "aems-edge"
AEMS_EDGE_AGENTS = {"nf_driver", "aems_manager"}

# Agents that are always typed "volttron"
VOLTTRON_AGENTS = {
    "listener",
    "platform_driver",
    "sql_historian",
    "mqtt_historian",
    "weather_dot_gov",
    "ilc",
    "emailer",
    "scheduler_example",
    "web_agent_example",
}

# Map from config.ini agent key to its default type
AGENT_TYPE_MAP = dict.fromkeys(AEMS_EDGE_AGENTS, "aems-edge")
AGENT_TYPE_MAP.update(dict.fromkeys(VOLTTRON_AGENTS, "volttron"))


# ---------------------------------------------------------------------------
# INI parsing helpers
# ---------------------------------------------------------------------------

def _getbool(section: configparser.SectionProxy, key: str, fallback: bool = False) -> bool:
    """Parse a boolean from an ini value, accepting true/false/yes/no/1/0."""
    raw = section.get(key, str(fallback)).strip().lower()
    return raw in ("true", "yes", "1", "on")


def _getlist(section: configparser.SectionProxy, key: str, fallback: str = "") -> list[str]:
    """Parse a comma-separated list from an ini value."""
    raw = section.get(key, fallback).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _getint(section: configparser.SectionProxy, key: str, fallback: int = 0) -> int:
    """Parse an integer from an ini value."""
    raw = section.get(key, str(fallback)).strip()
    try:
        return int(raw)
    except ValueError:
        return fallback


# ---------------------------------------------------------------------------
# Config reading
# ---------------------------------------------------------------------------

def read_ini(config_path: Path) -> configparser.ConfigParser:
    """Read and return a ConfigParser instance.

    The ini file is read with a relaxed parser that allows keys without
    values (useful for empty ``config_file =`` lines).
    """
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(config_path)
    return parser


# ---------------------------------------------------------------------------
# Agent builders
# ---------------------------------------------------------------------------

def _build_agent_entry(
    *,
    enabled: bool,
    agent_type: str,
    agent_dir: str,
    identity: str,
    config_file: str | None = None,
    description: str = "",
    volumes: list[str] | None = None,
    ports: list[str] | None = None,
    depends_on: list[str] | None = None,
    environment: dict[str, str] | None = None,
    network_mode: str | None = None,
) -> dict[str, Any]:
    """Build a single agent entry dict conforming to the schema."""
    entry: dict[str, Any] = {
        "enabled": enabled,
        "type": agent_type,
        "agent_dir": agent_dir,
        "identity": identity,
        "config_file": config_file if config_file else None,
    }
    if description:
        entry["description"] = description
    if volumes:
        entry["volumes"] = volumes
    if ports:
        entry["ports"] = ports
    if depends_on:
        entry["depends_on"] = depends_on
    if environment:
        entry["environment"] = environment
    if network_mode:
        entry["network_mode"] = network_mode
    return entry


def build_simple_agent(
    agent_name: str,
    ini: configparser.ConfigParser,
    enabled: bool,
    section_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build an agent entry from its own [agent_name] ini section.

    Falls back to sensible defaults when the section is missing.

    When *section_name* is provided, the INI section is looked up under
    that name instead of *agent_name* (useful for aliases like
    ``[historian]`` -> ``sql_historian``).
    """
    lookup = section_name if section_name else agent_name
    section = ini[lookup] if ini.has_section(lookup) else {}
    agent_type = AGENT_TYPE_MAP.get(agent_name, "volttron")

    agent_dir = section.get("agent_dir", "")
    identity = section.get("identity", agent_name)
    config_file = section.get("config_file", None)
    # configparser returns empty string for ``config_file =``; treat as None
    if config_file is not None and config_file.strip() == "":
        config_file = None
    description = section.get("description", "")
    volumes = _getlist(section, "volumes") if hasattr(section, "get") else []
    ports = _getlist(section, "ports") if hasattr(section, "get") else []
    depends_on = _getlist(section, "depends_on") if hasattr(section, "get") else []
    network_mode = section.get("network_mode", None) if hasattr(section, "get") else None

    return {
        agent_name: _build_agent_entry(
            enabled=enabled,
            agent_type=agent_type,
            agent_dir=agent_dir,
            identity=identity,
            config_file=config_file,
            description=description,
            volumes=volumes if volumes else None,
            ports=ports if ports else None,
            depends_on=depends_on if depends_on else None,
            network_mode=network_mode,
        )
    }


def build_aems_manager_agents(
    ini: configparser.ConfigParser,
    enabled: bool,
) -> dict[str, dict[str, Any]]:
    """Build one or more AEMS Manager agent entries.

    When ``num_devices`` > 1 in [aems_manager], each device gets its own
    agent entry keyed ``aems_manager`` (single) or ``aems_manager_01`` etc.
    """
    section = ini["aems_manager"] if ini.has_section("aems_manager") else {}
    agent_dir = section.get("agent_dir", "/volttron-pnnl-aems/aems-edge/Manager")
    config_file = section.get("config_file", "configs/aems-manager/config")
    description = section.get("description", "AEMS Edge Manager agent")
    prefix = section.get("prefix", "rtu")
    num_devices = _getint(section, "num_devices", 1)

    agents: dict[str, dict[str, Any]] = {}

    if num_devices <= 1:
        # Single manager — use explicit identity or default
        identity = section.get("identity", f"manager.{prefix}01")
        agents["aems_manager"] = _build_agent_entry(
            enabled=enabled,
            agent_type="aems-edge",
            agent_dir=agent_dir,
            identity=identity,
            config_file=config_file,
            description=description,
        )
    else:
        for i in range(1, num_devices + 1):
            device_name = f"{prefix}{str(i).zfill(2)}"
            agent_key = f"aems_manager_{device_name}"
            agents[agent_key] = _build_agent_entry(
                enabled=enabled,
                agent_type="aems-edge",
                agent_dir=agent_dir,
                identity=f"manager.{device_name}",
                config_file=config_file,
                description=f"{description} ({device_name})",
            )

    return agents


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

# The canonical order agents appear in the output.  Agents not in this list
# are appended at the end in the order they appear in [agents].
AGENT_ORDER = [
    "listener",
    "platform_driver",
    "sql_historian",
    "mqtt_historian",
    "aems_manager",
    "scheduler_example",
    "web_agent_example",
    "weather_dot_gov",
    "nf_driver",
    "ilc",
    "emailer",
]


def generate_agents_config(ini: configparser.ConfigParser) -> dict[str, Any]:
    """Build the full agents-config.json structure from the parsed ini."""

    # --- [site] ---
    _site = ini["site"] if ini.has_section("site") else {}  # noqa: F841
    # site values are informational; they don't directly appear in the output
    # but downstream generators (generate_configs.py) use them.

    # --- [global] ---
    global_section = ini["global"] if ini.has_section("global") else {}
    global_config: dict[str, Any] = {
        "server_address": global_section.get("server_address", "ws://aems-fastlib-server:8000"),
        "volttron_home": global_section.get("volttron_home", "/var/volttron"),
    }
    # Optional global keys
    for key in ("config_base_dir", "restart_policy", "log_dir"):
        val = global_section.get(key, None)
        if val:
            global_config[key] = val
    enable_logs = global_section.get("enable_agent_logs", None)
    if enable_logs is not None:
        global_config["enable_agent_logs"] = enable_logs.strip().lower() in (
            "true", "yes", "1", "on"
        )

    # --- [agents] toggles ---
    agents_toggles: dict[str, bool] = {}
    if ini.has_section("agents"):
        for key in ini["agents"]:
            agents_toggles[key] = _getbool(ini["agents"], key)

    # --- Build agent entries ---
    agents: dict[str, dict[str, Any]] = {}

    # Walk the canonical order first, then any extras from [agents]
    all_agent_keys = list(AGENT_ORDER)
    for key in agents_toggles:
        if key not in all_agent_keys:
            all_agent_keys.append(key)

    for agent_name in all_agent_keys:
        enabled = agents_toggles.get(agent_name, False)

        if agent_name == "aems_manager":
            agents.update(build_aems_manager_agents(ini, enabled))
        elif agent_name == "sql_historian":
            # sql_historian reads from [historian] section
            agents.update(build_simple_agent(agent_name, ini, enabled, section_name="historian"))
        elif agent_name == "mqtt_historian":
            agents.update(build_simple_agent(agent_name, ini, enabled))
        elif agent_name == "weather_dot_gov":
            # weather_dot_gov reads from [weather] section
            agents.update(build_simple_agent(agent_name, ini, enabled, section_name="weather"))
        else:
            agents.update(build_simple_agent(agent_name, ini, enabled))

    # --- Assemble ---
    config: dict[str, Any] = {
        "$schema": "./agents-config.schema.json",
        "description": "Configuration for legacy VOLTTRON and AEMS Edge agents",
        "agents": agents,
        "global": global_config,
    }

    return config


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(config: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """Validate the generated config against the JSON schema.

    Performs structural validation using only the stdlib.  If a schema file
    is provided and readable, it is used for additional checks.  Returns a
    list of error strings (empty == valid).
    """
    errors: list[str] = []

    # --- Required top-level keys ---
    for key in ("agents", "global"):
        if key not in config:
            errors.append(f"Missing required top-level key: '{key}'")

    if "agents" not in config:
        return errors  # can't continue without agents

    # --- Global section ---
    global_cfg = config.get("global", {})
    for required in ("server_address", "volttron_home"):
        if required not in global_cfg:
            errors.append(f"Missing required global key: '{required}'")

    restart = global_cfg.get("restart_policy")
    if restart and restart not in VALID_RESTART_POLICIES:
        errors.append(
            f"Invalid restart_policy '{restart}'; "
            f"must be one of {sorted(VALID_RESTART_POLICIES)}"
        )

    # --- Agent entries ---
    agents = config["agents"]
    if not isinstance(agents, dict):
        errors.append("'agents' must be an object")
        return errors

    for name, agent in agents.items():
        # Name pattern
        if not all(c in AGENT_NAME_PATTERN_CHARS for c in name):
            errors.append(f"Agent name '{name}' contains invalid characters")

        # Required fields
        for req in ("enabled", "type", "agent_dir", "identity"):
            if req not in agent:
                errors.append(f"Agent '{name}' missing required field: '{req}'")

        # agent_dir must be non-empty for enabled agents
        if agent.get("enabled") and not agent.get("agent_dir", "").strip():
            errors.append(
                f"Agent '{name}' is enabled but has an empty 'agent_dir'"
            )

        # Type enum
        atype = agent.get("type")
        if atype and atype not in VALID_AGENT_TYPES:
            errors.append(
                f"Agent '{name}' has invalid type '{atype}'; "
                f"must be one of {sorted(VALID_AGENT_TYPES)}"
            )

        # Ports format
        for port in agent.get("ports", []):
            parts = port.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                errors.append(f"Agent '{name}' has invalid port mapping: '{port}'")

        # depends_on references
        for dep in agent.get("depends_on", []):
            if dep not in agents:
                errors.append(
                    f"Agent '{name}' depends_on '{dep}' which is not defined"
                )

    # --- Optional: validate against JSON Schema file ---
    if schema_path and schema_path.exists():
        try:
            import jsonschema  # type: ignore[import-untyped]

            with open(schema_path) as f:
                schema = json.load(f)
            jsonschema.validate(config, schema)
        except ImportError:
            pass  # jsonschema not installed; structural validation above suffices
        except Exception as exc:
            errors.append(f"JSON Schema validation error: {exc}")

    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate agents-config.json from a config.ini file"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.ini"),
        help="Path to the config.ini file (default: config.ini)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("agents-config.json"),
        help="Path to the output JSON file (default: agents-config.json)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Path to agents-config.schema.json for validation (auto-detected if next to --output)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated JSON to stdout without writing to disk",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the output and exit (does not write)",
    )

    args = parser.parse_args()

    # --- Read INI ---
    ini = read_ini(args.config)

    # --- Generate ---
    config = generate_agents_config(ini)

    # --- Resolve schema path ---
    schema_path = args.schema
    if schema_path is None:
        candidate = args.output.parent / "agents-config.schema.json"
        if candidate.exists():
            schema_path = candidate

    # --- Validate ---
    errors = validate_config(config, schema_path)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # --- Output ---
    json_str = json.dumps(config, indent=2) + "\n"

    if args.validate_only:
        print("Validation passed.")
        _print_summary(config)
        return

    if args.dry_run:
        print(json_str)
        return

    with open(args.output, "w") as f:
        f.write(json_str)

    print(f"Wrote {args.output}")
    _print_summary(config)
    print()
    print("Next steps:")
    print(f"  1. Review {args.output}")
    print(f"  2. python generate-docker-compose.py --config {args.output}")


def _print_summary(config: dict[str, Any]) -> None:
    """Print a human-readable summary of the generated config."""
    agents = config.get("agents", {})
    enabled = [n for n, a in agents.items() if a.get("enabled")]
    disabled = [n for n, a in agents.items() if not a.get("enabled")]

    print()
    print("Summary:")
    print(f"  Total agents: {len(agents)}")
    print(f"  Enabled:      {len(enabled)}")
    print(f"  Disabled:     {len(disabled)}")

    if enabled:
        print()
        print("Enabled agents:")
        for name in enabled:
            agent = agents[name]
            desc = agent.get("description", "")
            atype = agent.get("type", "?")
            print(f"  + {name} ({atype}){': ' + desc if desc else ''}")

    if disabled:
        print()
        print("Disabled agents:")
        for name in disabled:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
