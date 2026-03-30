#!/usr/bin/env python3
"""
Top-level orchestrator for the AEMS agent configuration pipeline.

Chains three scripts in sequence:
  1. generate_configs.py  (from aems-edge) → agent config files in configs/
  2. generate-agents-config.py             → agents-config.json
  3. generate-docker-compose.py            → docker-compose.yml

Usage:
    python orchestrate.py
    python orchestrate.py --config my-site.ini
    python orchestrate.py --dry-run
    python orchestrate.py --skip-configs --skip-agents-config
    python orchestrate.py --aems-edge-path /path/to/volttron-pnnl-aems/aems-edge
"""

import argparse
import configparser
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_CONFIG_NOT_FOUND = 1
EXIT_AEMS_EDGE_NOT_FOUND = 2
EXIT_STEP1_FAILED = 10
EXIT_STEP2_FAILED = 20
EXIT_STEP3_FAILED = 30

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = "config.ini"
DEFAULT_AGENTS_CONFIG = "agents-config.json"
DEFAULT_COMPOSE_OUTPUT = "docker-compose.yml"
DEFAULT_AEMS_EDGE_REL = "../volttron-pnnl-aems/aems-edge"
GENERATE_CONFIGS_REL = "configurations/docker/generate_configs.py"


def _banner(step_num: int, title: str) -> None:
    """Print a visible step banner."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  Step {step_num}: {title}")
    print(sep)


def _resolve_aems_edge(cli_path: str | None, script_dir: Path) -> Path:
    """Resolve the aems-edge directory, trying CLI arg then default relative path."""
    candidates = []
    if cli_path:
        candidates.append(Path(cli_path).resolve())
    candidates.append((script_dir / DEFAULT_AEMS_EDGE_REL).resolve())

    for candidate in candidates:
        gen_script = candidate / GENERATE_CONFIGS_REL
        if gen_script.is_file():
            return candidate

    tried = "\n  ".join(str(c) for c in candidates)
    print(
        f"Error: Could not find aems-edge with {GENERATE_CONFIGS_REL}.\n"
        f"  Searched:\n  {tried}\n\n"
        f"Use --aems-edge-path to specify the correct location.",
        file=sys.stderr,
    )
    sys.exit(EXIT_AEMS_EDGE_NOT_FOUND)


def _read_ini(config_path: Path) -> configparser.ConfigParser:
    """Read the config.ini and return a ConfigParser."""
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(config_path)
    return parser


def _get_ini_value(ini: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    """Safely get a value from the ini."""
    if ini.has_section(section):
        return ini.get(section, key, fallback=fallback).strip()
    return fallback


# ---------------------------------------------------------------------------
# Step 1: generate_configs.py
# ---------------------------------------------------------------------------

def step1_generate_configs(
    *,
    config_path: Path,
    aems_edge_path: Path,
    output_dir: Path,
    dry_run: bool,
) -> None:
    """Run generate_configs.py from the aems-edge repo."""
    _banner(1, "Generate agent config files (generate_configs.py)")

    gen_script = aems_edge_path / GENERATE_CONFIGS_REL
    gen_script_dir = gen_script.parent

    # Read the ini to extract required args for generate_configs.py
    ini = _read_ini(config_path)
    campus = _get_ini_value(ini, "site", "campus", "PNNL")
    building = _get_ini_value(ini, "site", "building", "SEB")
    timezone = _get_ini_value(ini, "site", "timezone", "America/Los_Angeles")
    gateway_address = _get_ini_value(ini, "site", "gateway_address", "")
    prefix = _get_ini_value(ini, "aems_manager", "prefix", "rtu")
    num_devices = _get_ini_value(ini, "aems_manager", "num_devices", "1")
    weather_station = _get_ini_value(ini, "weather", "station", "")
    stat_type = _get_ini_value(ini, "site", "stat_type", "schneider")

    # If no gateway_address in [site], check device sections
    if not gateway_address:
        for section in ini.sections():
            if section.startswith("device:"):
                gateway_address = _get_ini_value(ini, section, "address", "")
                if gateway_address:
                    break

    if not gateway_address:
        gateway_address = "192.168.1.1"
        print(f"  Warning: No gateway_address found in config; using default {gateway_address}")

    # Build the config.ini that generate_configs.py expects (configargparse format)
    # We pass all values as CLI args to avoid config file conflicts
    cmd = [
        sys.executable, str(gen_script),
        "--output-dir", str(output_dir),
        "--campus", campus,
        "--building", building,
        "--gateway-address", gateway_address,
        "--timezone", timezone,
        "--prefix", prefix,
        "--num-configs", num_devices,
        "--stat-type", stat_type,
    ]

    if weather_station:
        cmd.extend(["--weather-station", weather_station])

    # Database settings from [historian] or [database] sections
    db_name = _get_ini_value(ini, "database", "db_name", "")
    db_user = _get_ini_value(ini, "database", "db_user", "")
    db_password = _get_ini_value(ini, "database", "db_password", "")
    db_address = _get_ini_value(ini, "database", "db_address", "")
    db_port = _get_ini_value(ini, "database", "db_port", "")
    if db_name:
        cmd.extend(["--db-name", db_name])
    if db_user:
        cmd.extend(["--db-user", db_user])
    if db_password:
        cmd.extend(["--db-password", db_password])
    if db_address:
        cmd.extend(["--db-address", db_address])
    if db_port:
        cmd.extend(["--db-port", db_port])

    print(f"  Script:  {gen_script}")
    print(f"  Output:  {output_dir}")
    print(f"  Config:  campus={campus}, building={building}, prefix={prefix}, "
          f"num_devices={num_devices}, timezone={timezone}")
    print(f"  Command: {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] Skipping execution.")
        return

    # Run from the generate_configs.py directory so its relative config.ini
    # doesn't interfere — we pass everything via CLI args
    result = subprocess.run(cmd, cwd=str(gen_script_dir))
    if result.returncode != 0:
        print(f"\nError: generate_configs.py exited with code {result.returncode}", file=sys.stderr)
        sys.exit(EXIT_STEP1_FAILED)

    print(f"  Done. Config files written to {output_dir}")


# ---------------------------------------------------------------------------
# Step 2: generate-agents-config.py
# ---------------------------------------------------------------------------

def step2_generate_agents_config(
    *,
    config_path: Path,
    output_path: Path,
    script_dir: Path,
    dry_run: bool,
) -> None:
    """Run generate-agents-config.py."""
    _banner(2, "Generate agents-config.json (generate-agents-config.py)")

    gen_script = script_dir / "generate-agents-config.py"
    if not gen_script.is_file():
        print(f"Error: {gen_script} not found", file=sys.stderr)
        sys.exit(EXIT_STEP2_FAILED)

    cmd = [
        sys.executable, str(gen_script),
        "--config", str(config_path),
        "--output", str(output_path),
    ]

    print(f"  Script:  {gen_script}")
    print(f"  Input:   {config_path}")
    print(f"  Output:  {output_path}")
    print(f"  Command: {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] Skipping execution.")
        return

    result = subprocess.run(cmd, cwd=str(script_dir))
    if result.returncode != 0:
        print(f"\nError: generate-agents-config.py exited with code {result.returncode}", file=sys.stderr)
        sys.exit(EXIT_STEP2_FAILED)

    print(f"  Done. Wrote {output_path}")


# ---------------------------------------------------------------------------
# Step 3: generate-docker-compose.py
# ---------------------------------------------------------------------------

def step3_generate_docker_compose(
    *,
    agents_config_path: Path,
    output_path: Path,
    script_dir: Path,
    dry_run: bool,
) -> None:
    """Run generate-docker-compose.py."""
    _banner(3, "Generate docker-compose.yml (generate-docker-compose.py)")

    gen_script = script_dir / "generate-docker-compose.py"
    if not gen_script.is_file():
        print(f"Error: {gen_script} not found", file=sys.stderr)
        sys.exit(EXIT_STEP3_FAILED)

    cmd = [
        sys.executable, str(gen_script),
        "--config", str(agents_config_path),
        "--output", str(output_path),
    ]

    print(f"  Script:  {gen_script}")
    print(f"  Input:   {agents_config_path}")
    print(f"  Output:  {output_path}")
    print(f"  Command: {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] Skipping execution.")
        return

    result = subprocess.run(cmd, cwd=str(script_dir))
    if result.returncode != 0:
        print(f"\nError: generate-docker-compose.py exited with code {result.returncode}", file=sys.stderr)
        sys.exit(EXIT_STEP3_FAILED)

    print(f"  Done. Wrote {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrate the full AEMS config-to-compose pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Pipeline:
  config.ini
    → generate_configs.py        → agent config files (configs/)
    → generate-agents-config.py  → agents-config.json
    → generate-docker-compose.py → docker-compose.yml
""",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path(DEFAULT_CONFIG),
        help=f"Path to config.ini (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--agents-config",
        type=Path,
        default=Path(DEFAULT_AGENTS_CONFIG),
        help=f"Path for agents-config.json output (default: {DEFAULT_AGENTS_CONFIG})",
    )
    parser.add_argument(
        "--compose-output",
        type=Path,
        default=Path(DEFAULT_COMPOSE_OUTPUT),
        help=f"Path for docker-compose.yml output (default: {DEFAULT_COMPOSE_OUTPUT})",
    )
    parser.add_argument(
        "--configs-output-dir",
        type=Path,
        default=None,
        help="Directory for generated agent config files (default: ./configs in the working directory)",
    )
    parser.add_argument(
        "--aems-edge-path",
        type=str,
        default=None,
        help=f"Path to volttron-pnnl-aems/aems-edge (default: {DEFAULT_AEMS_EDGE_REL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be executed without running anything",
    )
    parser.add_argument(
        "--skip-configs",
        action="store_true",
        help="Skip Step 1 (generate_configs.py) if agent config files already exist",
    )
    parser.add_argument(
        "--skip-agents-config",
        action="store_true",
        help="Skip Step 2 (generate-agents-config.py) if agents-config.json already exists",
    )

    args = parser.parse_args()

    # Resolve paths relative to this script's directory (= working dir for the project)
    script_dir = Path(__file__).resolve().parent
    config_path = args.config if args.config.is_absolute() else (script_dir / args.config)
    agents_config_path = args.agents_config if args.agents_config.is_absolute() else (script_dir / args.agents_config)
    compose_output = args.compose_output if args.compose_output.is_absolute() else (script_dir / args.compose_output)

    if args.configs_output_dir:
        configs_output_dir = args.configs_output_dir if args.configs_output_dir.is_absolute() else (script_dir / args.configs_output_dir)
    else:
        configs_output_dir = script_dir / "configs"

    # Validate config.ini exists
    if not config_path.is_file():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_NOT_FOUND)

    # Resolve aems-edge path (only needed for Step 1)
    aems_edge_path = None
    if not args.skip_configs:
        aems_edge_path = _resolve_aems_edge(args.aems_edge_path, script_dir)

    # Print pipeline summary
    print("AEMS Configuration Pipeline")
    print("=" * 70)
    print(f"  Config file:       {config_path}")
    print(f"  Configs output:    {configs_output_dir}")
    print(f"  Agents config:     {agents_config_path}")
    print(f"  Compose output:    {compose_output}")
    if aems_edge_path:
        print(f"  aems-edge path:    {aems_edge_path}")
    print(f"  Dry run:           {args.dry_run}")
    print(f"  Skip configs:      {args.skip_configs}")
    print(f"  Skip agents-config:{args.skip_agents_config}")

    # --- Step 1 ---
    if args.skip_configs:
        _banner(1, "Generate agent config files — SKIPPED (--skip-configs)")
        if not configs_output_dir.is_dir():
            print(f"  Warning: configs directory does not exist: {configs_output_dir}")
    else:
        step1_generate_configs(
            config_path=config_path,
            aems_edge_path=aems_edge_path,
            output_dir=configs_output_dir,
            dry_run=args.dry_run,
        )

    # --- Step 2 ---
    if args.skip_agents_config:
        _banner(2, "Generate agents-config.json — SKIPPED (--skip-agents-config)")
        if not agents_config_path.is_file():
            print(f"  Warning: agents-config.json does not exist: {agents_config_path}")
    else:
        step2_generate_agents_config(
            config_path=config_path,
            output_path=agents_config_path,
            script_dir=script_dir,
            dry_run=args.dry_run,
        )

    # --- Step 3 ---
    step3_generate_docker_compose(
        agents_config_path=agents_config_path,
        output_path=compose_output,
        script_dir=script_dir,
        dry_run=args.dry_run,
    )

    # --- Done ---
    print("\n" + "=" * 70)
    print("  Pipeline complete.")
    if args.dry_run:
        print("  (Dry run — no files were written.)")
    else:
        print(f"  Config files:      {configs_output_dir}/")
        print(f"  Agents config:     {agents_config_path}")
        print(f"  Docker compose:    {compose_output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
