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
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Sensitive-value redaction
# ---------------------------------------------------------------------------
_SENSITIVE_KEYS = {"password", "secret", "token"}


def _is_sensitive(key: str) -> bool:
    """Return True if *key* (case-insensitive) contains a sensitive word."""
    lower = key.lower()
    return any(word in lower for word in _SENSITIVE_KEYS)


def _redact_flat_line(line: str) -> str:
    """Redact the value portion of a 'key = value' line if key is sensitive."""
    if "=" in line:
        k, _, v = line.partition("=")
        if _is_sensitive(k.strip()):
            return f"{k}= [REDACTED]"
    return line


def _redact_args(args: list[str]) -> list[str]:
    """Return a copy of *args* with values following sensitive keys redacted."""
    out: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            out.append("[REDACTED]")
            redact_next = False
            continue
        if "=" in arg and arg.startswith("--"):
            k, _, v = arg.partition("=")
            if _is_sensitive(k):
                out.append(f"{k}=[REDACTED]")
            else:
                out.append(arg)
        elif arg.startswith("--") and _is_sensitive(arg):
            out.append(arg)
            redact_next = True
        else:
            out.append(arg)
    return out

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

# ---------------------------------------------------------------------------
# Mapping from our sectioned config.ini to the flat keys that
# configargparse in generate_configs.py expects.
#
# Format: (ini_section, ini_key, configargparse_key)
# Only keys present in the ini are written; missing keys let
# generate_configs.py use its own defaults.
# ---------------------------------------------------------------------------
_INI_TO_FLAT = [
    ("site",         "campus",          "campus"),
    ("site",         "building",        "building"),
    ("site",         "timezone",        "timezone"),
    ("site",         "gateway_address", "gateway-address"),
    ("site",         "stat_type",       "stat-type"),
    ("aems_manager", "prefix",          "prefix"),
    ("aems_manager", "num_devices",     "num-configs"),
    ("weather",      "station",         "weather-station"),
    ("database",     "db_name",         "db-name"),
    ("database",     "db_user",         "db-user"),
    ("database",     "db_password",     "db-password"),
    ("database",     "db_address",      "db-address"),
    ("database",     "db_port",         "db-port"),
]


def _banner(step_num: int, title: str) -> None:
    """Print a visible step banner."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  Step {step_num}: {title}")
    print(sep)


def _resolve_aems_edge(cli_path: str | None, script_dir: Path) -> Path:
    """Resolve the aems-edge directory.

    If --aems-edge-path is explicitly provided, it MUST be valid — no silent
    fallback.  When omitted the default relative path is tried instead.
    """
    if cli_path:
        explicit = Path(cli_path).resolve()
        gen_script = explicit / GENERATE_CONFIGS_REL
        if gen_script.is_file():
            return explicit
        print(
            f"Error: --aems-edge-path was provided but "
            f"{GENERATE_CONFIGS_REL} was not found under:\n"
            f"  {explicit}\n",
            file=sys.stderr,
        )
        sys.exit(EXIT_AEMS_EDGE_NOT_FOUND)

    # No explicit path — try the default relative location.
    default = (script_dir / DEFAULT_AEMS_EDGE_REL).resolve()
    gen_script = default / GENERATE_CONFIGS_REL
    if gen_script.is_file():
        return default

    print(
        f"Error: Could not find aems-edge with {GENERATE_CONFIGS_REL}.\n"
        f"  Searched:\n  {default}\n\n"
        f"Use --aems-edge-path to specify the correct location.",
        file=sys.stderr,
    )
    sys.exit(EXIT_AEMS_EDGE_NOT_FOUND)


def _build_flat_config(config_path: Path) -> str:
    """Translate our sectioned config.ini into the flat key=value format
    that configargparse in generate_configs.py expects.

    Only keys that exist in the ini are emitted — everything else falls
    through to generate_configs.py's own defaults.

    If no gateway_address is found in [site], device sections are checked
    as a fallback.
    """
    ini = configparser.ConfigParser(allow_no_value=True)
    ini.read(config_path)

    lines: list[str] = []
    for section, key, flat_key in _INI_TO_FLAT:
        if ini.has_section(section) and ini.has_option(section, key):
            value = ini.get(section, key).strip()
            if value:
                lines.append(f"{flat_key} = {value}")

    # If gateway-address was not written, check device:N sections.
    if not any(line.startswith("gateway-address") for line in lines):
        for section in ini.sections():
            if section.startswith("device:"):
                addr = ini.get(section, "address", fallback="").strip()
                if addr:
                    lines.append(f"gateway-address = {addr}")
                    break

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Step 1: generate_configs.py
# ---------------------------------------------------------------------------

def step1_generate_configs(
    *,
    config_path: Path,
    aems_edge_path: Path,
    output_dir: Path,
    overrides: list[str],
    dry_run: bool,
) -> None:
    """Run generate_configs.py from the aems-edge repo.

    Instead of re-extracting every value from config.ini and passing them
    as CLI args, we write a temporary flat config file that configargparse
    reads natively.  Only --output-dir and any explicit user overrides are
    passed on the command line.
    """
    _banner(1, "Generate agent config files (generate_configs.py)")

    gen_script = aems_edge_path / GENERATE_CONFIGS_REL

    # Build the flat config that configargparse expects.
    flat_config_text = _build_flat_config(config_path)

    # Assemble the command — only the output dir is mandatory on the CLI.
    cmd = [
        sys.executable, str(gen_script),
        "--output-dir", str(output_dir.resolve()),
    ]
    if overrides:
        cmd.extend(overrides)

    print(f"  Script:  {gen_script}")
    print(f"  Output:  {output_dir}")
    print(f"  Config:  {config_path}  (translated to flat configargparse format)")
    if overrides:
        print(f"  Overrides: {' '.join(_redact_args(overrides))}")
    print(f"  Command: {' '.join(_redact_args(cmd))}")

    if dry_run:
        print("  [DRY RUN] Flat config that would be written:")
        for line in flat_config_text.splitlines():
            print(f"    {_redact_flat_line(line)}")
        print("  [DRY RUN] Skipping execution.")
        return

    # Write the flat config to a temp directory and run from there so
    # configargparse discovers it as 'config.ini' in the CWD.
    # Use os.open with 0o600 to ensure the file is not world-readable
    # (it may contain database passwords).
    with tempfile.TemporaryDirectory(prefix="aems-orchestrate-") as tmpdir:
        flat_config_path = Path(tmpdir) / "config.ini"
        fd = os.open(str(flat_config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, flat_config_text.encode())
        finally:
            os.close(fd)

        result = subprocess.run(cmd, cwd=tmpdir)

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

    result = subprocess.run(cmd)
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

    result = subprocess.run(cmd)
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
        help=f"Path to config.ini (default: {DEFAULT_CONFIG}). "
             "Copy config.ini.example to config.ini and edit for your site.",
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

    parser.add_argument(
        "--passthrough",
        nargs=argparse.REMAINDER,
        default=[],
        help="Additional arguments passed directly to generate_configs.py "
             "(e.g. --passthrough --campus DIFFERENT --num-configs 5). "
             "Everything after --passthrough is forwarded verbatim.",
    )

    args = parser.parse_args()
    overrides = args.passthrough
    # Strip a leading '--' that argparse.REMAINDER may capture.
    if overrides and overrides[0] == "--":
        overrides = overrides[1:]

    # Resolve paths relative to this script's directory (= working dir for the project)
    script_dir = Path(__file__).resolve().parent
    config_path = args.config if args.config.is_absolute() else (script_dir / args.config)
    agents_config_path = args.agents_config if args.agents_config.is_absolute() else (script_dir / args.agents_config)
    compose_output = args.compose_output if args.compose_output.is_absolute() else (script_dir / args.compose_output)

    if args.configs_output_dir:
        configs_output_dir = (
            args.configs_output_dir if args.configs_output_dir.is_absolute() else (script_dir / args.configs_output_dir)
        )
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
    if overrides:
        print(f"  Overrides:         {' '.join(_redact_args(overrides))}")

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
            overrides=overrides,
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
    # Guard: if both prior steps were skipped, agents-config.json must exist.
    if not agents_config_path.is_file() and not args.dry_run:
        print(
            f"\nError: Cannot run Step 3 — {agents_config_path} does not exist.\n"
            f"  Run without --skip-configs / --skip-agents-config first, or "
            f"provide an existing agents-config.json via --agents-config.",
            file=sys.stderr,
        )
        sys.exit(EXIT_STEP3_FAILED)

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
