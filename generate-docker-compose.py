#!/usr/bin/env python3
"""
Generate docker-compose.yml from agents-config.json

This script reads agents-config.json and generates a docker-compose.yml file
with the AEMS server and all enabled legacy agents.

Usage:
    python generate-docker-compose.py
    python generate-docker-compose.py --config agents-config.json --output docker-compose.yml
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_agents_config(config_file: Path) -> dict[str, Any]:
    """Load and validate agents configuration."""
    if not config_file.exists():
        print(f"Error: Config file not found: {config_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(config_file) as f:
            config = json.load(f)

        # Validate required keys
        if "agents" not in config or "global" not in config:
            print("Error: Config must have 'agents' and 'global' sections", file=sys.stderr)
            sys.exit(1)

        return config
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file: {e}", file=sys.stderr)
        sys.exit(1)


def generate_compose_header() -> str:
    """Generate docker-compose.yml header."""
    return """# Generated docker-compose.yml
# DO NOT EDIT MANUALLY - Generated from agents-config.json
# Run: python generate-docker-compose.py to regenerate

services:
"""


def generate_server_service() -> str:
    """Generate AEMS server service definition."""
    return """  aems-fastlib-server:
    build:
      context: .
      dockerfile: Dockerfile
    image: aems-fastapi:latest
    container_name: aems-fastlib-server
    ports:
      - "5410:8000"
    volumes:
      - volttron-home:/var/volttron
    environment:
      - VOLTTRON_HOME=/var/volttron
      - AEMS_PORT=8000
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    networks:
      - aems-network

"""


def generate_agent_service(agent_name: str, agent_config: dict[str, Any], global_config: dict[str, Any]) -> str:
    """Generate docker-compose service definition for an agent."""
    if not agent_config.get("enabled", False):
        return ""

    agent_dir = agent_config["agent_dir"]
    identity = agent_config["identity"]
    config_file = agent_config.get("config_file")
    server_address = global_config["server_address"]
    volttron_home = global_config["volttron_home"]
    restart_policy = global_config.get("restart_policy", "unless-stopped")
    config_base_dir = global_config.get("config_base_dir", "/configs")
    log_dir = global_config.get("log_dir", "/var/log/aems")
    enable_agent_logs = global_config.get("enable_agent_logs", True)

    # Build container name
    container_name = f"aems-{agent_name.replace('_', '-')}"

    # Start service definition
    service = f"  {agent_name}:\n"
    service += "    image: aems-fastapi:latest\n"
    service += f"    container_name: {container_name}\n"

    # Ports
    ports = agent_config.get("ports", [])
    if ports:
        service += "    ports:\n"
        for port_mapping in ports:
            service += f'      - "{port_mapping}"\n'

    # Volumes
    service += "    volumes:\n"
    service += f"      - volttron-home:{volttron_home}\n"

    # Add config volume if config file specified
    # Check if config_file is an absolute path (starts with /)
    is_absolute_path = config_file and config_file.startswith("/")

    if config_file and not is_absolute_path:
        # Relative path - mount from host
        config_dir = str(Path(config_file).parent)
        service += f"      - ./{config_dir}:{config_base_dir}/{config_dir}:ro\n"

    # Add log directory volume if enabled
    if enable_agent_logs:
        service += f"      - agent-logs:{log_dir}\n"

    # Add additional volumes
    for volume in agent_config.get("volumes", []):
        service += f"      - {volume}\n"

    # Environment variables
    service += "    environment:\n"
    service += f"      - VOLTTRON_HOME={volttron_home}\n"

    # Add log file path for agent
    if enable_agent_logs:
        log_file = f"{log_dir}/{identity}.log"
        service += f"      - AGENT_LOG_FILE={log_file}\n"

    # Add default environment
    for key, value in global_config.get("default_environment", {}).items():
        service += f"      - {key}={value}\n"

    # Add agent-specific environment
    for key, value in agent_config.get("environment", {}).items():
        service += f"      - {key}={value}\n"

    # Build command
    service += "    command: [\n"
    service += '      "python", "/app/start-legacy.py",\n'
    service += f'      "--agent-dir", "{agent_dir}",\n'

    if config_file:
        if is_absolute_path:
            # Use absolute path directly (file already in container)
            config_path = config_file
        else:
            # Build path from config_base_dir
            config_path = f"{config_base_dir}/{config_file}"
        service += f'      "--config", "{config_path}",\n'
    else:
        service += '      "--config", "config",\n'

    service += f'      "--identity", "{identity}",\n'
    service += f'      "--address", "{server_address}"\n'
    service += "    ]\n"

    # Healthcheck - verify the agent process is running
    service += "    healthcheck:\n"
    service += '      test: ["CMD-SHELL", "grep -q start-legacy /proc/1/cmdline"]\n'
    service += "      interval: 30s\n"
    service += "      timeout: 5s\n"
    service += "      retries: 3\n"
    service += "      start_period: 15s\n"

    # Dependencies
    service += "    depends_on:\n"
    service += "      aems-fastlib-server:\n"
    service += "        condition: service_healthy\n"

    # Add custom dependencies
    for dep in agent_config.get("depends_on", []):
        service += f"      {dep}:\n"
        service += "        condition: service_started\n"

    # Restart policy
    service += f"    restart: {restart_policy}\n"

    # Network
    if "network_mode" in agent_config:
        service += f"    network_mode: {agent_config['network_mode']}\n"
    else:
        service += "    networks:\n"
        service += "      - aems-network\n"

    service += "\n"
    return service


def generate_compose_footer() -> str:
    """Generate docker-compose.yml footer with volumes and networks."""
    return """volumes:
  volttron-home:
    driver: local
  agent-logs:
    driver: local

networks:
  aems-network:
    driver: bridge
"""


def generate_docker_compose(config: dict[str, Any]) -> str:
    """Generate complete docker-compose.yml content."""
    compose = generate_compose_header()
    compose += generate_server_service()

    # Generate agent services
    agents = config["agents"]
    global_config = config["global"]

    for agent_name, agent_config in agents.items():
        agent_service = generate_agent_service(agent_name, agent_config, global_config)
        compose += agent_service

    compose += generate_compose_footer()
    return compose


def print_summary(config: dict[str, Any]) -> None:
    """Print summary of enabled agents."""
    agents = config["agents"]
    enabled_agents = [name for name, cfg in agents.items() if cfg.get("enabled", False)]
    disabled_agents = [name for name, cfg in agents.items() if not cfg.get("enabled", False)]

    print("Configuration Summary:")
    print(f"  Total agents: {len(agents)}")
    print(f"  Enabled: {len(enabled_agents)}")
    print(f"  Disabled: {len(disabled_agents)}")
    print()

    if enabled_agents:
        print("Enabled agents:")
        for agent_name in enabled_agents:
            agent_config = agents[agent_name]
            agent_type = agent_config.get("type", "unknown")
            description = agent_config.get("description", "No description")
            print(f"  - {agent_name} ({agent_type}): {description}")

    if disabled_agents:
        print()
        print("Disabled agents:")
        for agent_name in disabled_agents:
            print(f"  - {agent_name}")


def main():
    parser = argparse.ArgumentParser(description="Generate docker-compose.yml from agents-config.json")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("agents-config.json"),
        help="Path to agents configuration JSON file (default: agents-config.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docker-compose.yml"),
        help="Path to output docker-compose.yml file (default: docker-compose.yml)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print generated compose file without writing to disk")
    parser.add_argument("--summary", action="store_true", help="Print summary of agents configuration")

    args = parser.parse_args()

    # Load configuration
    config = load_agents_config(args.config)

    # Print summary if requested
    if args.summary:
        print_summary(config)
        return

    # Generate docker-compose.yml
    compose_content = generate_docker_compose(config)

    if args.dry_run:
        print("Generated docker-compose.yml:")
        print("=" * 80)
        print(compose_content)
        print("=" * 80)
    else:
        # Write to file
        with open(args.output, "w") as f:
            f.write(compose_content)

        print(f"✓ Generated {args.output}")
        print()
        print_summary(config)
        print()
        print("Next steps:")
        print(f"  1. Review {args.output}")
        print("  2. Ensure config files exist in configs/ directory")
        print("  3. Run: docker-compose up -d")


if __name__ == "__main__":
    main()
