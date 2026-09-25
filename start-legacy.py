#!/usr/bin/env python3
r"""
VOLTTRON Legacy Agent Launcher for AEMS.

This script allows running existing VOLTTRON agents without code modifications.
It runs the agent module from its directory by calling its main() function,
just like running `python -m agent.module` or how vctl start works.

Usage:
    ./start-legacy.py --agent-dir <path> [options]

Examples:
    # Run ListenerAgent
    ./start-legacy.py --agent-dir example-from-volttron/ListenerAgent \\
        --config config \\
        --identity listener

    # With custom server address
    ./start-legacy.py --agent-dir /path/to/MyAgent \\
        --config config.json \\
        --address ws://localhost:8000 \\
        --identity my_agent

Arguments:
    --agent-dir PATH    Path to agent directory (required)
    --module MODULE     Agent module path (e.g., 'listener.agent' or 'sqlhistorian.historian')
                        If not provided, will auto-detect by searching for vip_main call
    --config PATH       Path to agent configuration file (relative to agent-dir)
    --identity ID       Agent identity/name
    --address URL       AEMS message bus address (default: ws://localhost:8000)
    --volttron-home PATH  VOLTTRON_HOME directory
    --debug             Enable debug logging
    --help              Show this help message

How it works:
    1. Changes to agent directory (like vctl start)
    2. Installs VOLTTRON compatibility shims via import hooks
    3. Imports the agent module
    4. Calls the module's main() function, which calls utils.vip_main()
    5. vip_main() handles all agent initialization (config loading, factory functions, etc.)

This approach works with:
    - Agents with direct class instantiation (e.g., ListenerAgent)
    - Agents with factory functions (e.g., BACnetProxyAgent)
    - Agents with custom config parameter names
    - All standard VOLTTRON agent patterns
"""

import argparse
import contextlib
import logging
import os
import runpy
import signal
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Global agent reference for signal handler
_agent_instance = None


def setup_logging(debug=False, log_file=None, keep=False):
    """Configure logging for the launcher."""
    level = logging.DEBUG if debug else logging.INFO

    # Create formatters
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Get root logger and clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = []

    # Always add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Add file handler if log_file is specified
    if log_file:
        mode = "a" if keep else "w"
        file_handler = logging.FileHandler(log_file, mode=mode)
        # Always set file handler to DEBUG to capture all legacy agent protocol messages
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Always enable DEBUG for legacy agent modules to capture protocol messages
    # (like BACnet I-Am responses, driver scrapes, etc.)
    logging.getLogger("bacnet_proxy").setLevel(logging.DEBUG)
    logging.getLogger("platform_driver").setLevel(logging.DEBUG)
    # Also enable for __main__ since agents run as main module
    logging.getLogger("__main__").setLevel(logging.DEBUG)
    # Enable DEBUG for platform shims to see AsyncCall activity
    logging.getLogger("derhost.compat.shims.platform").setLevel(logging.DEBUG)

    if debug:
        # Enable debug logging for key AEMS modules
        logging.getLogger("derhost.client.agent").setLevel(logging.DEBUG)
        logging.getLogger("derhost.compat").setLevel(logging.DEBUG)
        logging.getLogger("websocket").setLevel(logging.DEBUG)
    else:
        # Keep websocket quiet unless in debug mode
        logging.getLogger("websocket").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global _agent_instance

    signal_name = signal.Signals(signum).name
    _log.info(f"\n{'=' * 60}")
    _log.info(f"Received {signal_name}, shutting down agent gracefully...")
    _log.info(f"{'=' * 60}")

    if _agent_instance:
        try:
            # Stop the agent's scheduler if it has one
            if hasattr(_agent_instance, "core") and hasattr(_agent_instance.core, "stop"):
                _log.info("Stopping agent core...")
                _agent_instance.core.stop()

            # Disconnect from message bus
            _log.info("Disconnecting from message bus...")
            _agent_instance.disconnect()
            _log.info("✓ Agent stopped cleanly")
        except Exception as e:
            _log.error(f"Error during shutdown: {e}")

    sys.exit(0)


def find_agent_module_and_class(agent_dir):
    """
    Find the agent module and class in the agent directory.

    Uses multiple strategies to locate the agent:
    1. Search for files containing vip_main() call (most reliable)
    2. Look for standard agent.py files
    3. Parse setup.py for entry points

    Args:
        agent_dir: Path to agent directory

    Returns:
        tuple: (module_name, class_name) e.g., ("listener.agent", "ListenerAgent")

    Raises:
        FileNotFoundError: If agent module not found
    """
    import re

    agent_dir = Path(agent_dir).resolve()
    _log.debug(f"Searching for agent in: {agent_dir}")

    # Strategy 1: Search for files containing vip_main call
    # This is the most reliable method as all VOLTTRON agents call vip_main
    _log.debug("Strategy 1: Searching for vip_main() calls...")
    for item in agent_dir.iterdir():
        if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
            # Search all Python files in this package directory
            for py_file in item.glob("*.py"):
                if py_file.name.startswith("_") and py_file.name != "__init__.py":
                    continue

                try:
                    content = py_file.read_text()

                    # Look for vip_main call - this indicates the entry point
                    if "vip_main" in content and re.search(r"utils\.vip_main\s*\(", content):
                        _log.info(f"Found vip_main call in: {py_file.relative_to(agent_dir)}")

                        # Try to find the actual agent class (not factory function)
                        # Look for class definitions that inherit from Agent, Historian, etc.
                        class_matches = re.findall(r"class\s+(\w+)\s*\([^)]*(?:Agent|Historian)[^)]*\)", content)
                        if class_matches:
                            # Prefer classes with Agent/Historian suffix
                            agent_classes = [c for c in class_matches if c.endswith(("Agent", "Historian"))]
                            class_name = agent_classes[0] if agent_classes else class_matches[0]
                            _log.debug(f"Found agent class from class definition: {class_name}")
                        else:
                            # Fallback: Extract from vip_main call (may be factory function)
                            # Pattern: utils.vip_main(agent_class_or_factory, ...)
                            vip_main_match = re.search(r"utils\.vip_main\s*\(\s*(\w+)", content)
                            if vip_main_match:
                                class_name = vip_main_match.group(1)
                                _log.warning(
                                    f"Using vip_main parameter '{class_name}' - this may be a factory function"
                                )
                            else:
                                _log.warning(f"Could not extract class name from {py_file}")
                                continue

                        # Build module name from directory structure
                        # e.g., sqlhistorian/historian.py -> sqlhistorian.historian
                        module_name = f"{item.name}.{py_file.stem}"

                        _log.info(f"Auto-detected agent: {module_name}:{class_name}")
                        return module_name, class_name

                except Exception as e:
                    _log.debug(f"Error reading {py_file}: {e}")
                    continue

    # Strategy 2: Look for standard agent.py files
    _log.debug("Strategy 2: Searching for agent.py files...")
    for item in agent_dir.iterdir():
        if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
            agent_file = item / "agent.py"
            if agent_file.exists():
                _log.debug(f"Found agent.py in {item.name}/")

                try:
                    content = agent_file.read_text()

                    # Look for class definitions that inherit from Agent
                    class_matches = re.findall(r"class\s+(\w+)\s*\([^)]*Agent[^)]*\)", content)

                    if class_matches:
                        class_name = class_matches[0]  # Use first match
                        module_name = f"{item.name}.agent"

                        _log.info(f"Auto-detected agent: {module_name}:{class_name}")
                        return module_name, class_name
                except Exception as e:
                    _log.debug(f"Error reading {agent_file}: {e}")
                    continue

    # Strategy 3: Parse setup.py for entry points
    setup_file = agent_dir / "setup.py"
    if setup_file.exists():
        _log.debug("Strategy 3: Parsing setup.py for entry points...")
        try:
            content = setup_file.read_text()
            # Look for entry_points with console_scripts
            entry_point_match = re.search(
                r"entry_points\s*=\s*{[^}]*console_scripts[^}]*:\s*\[([^\]]+)\]", content, re.DOTALL
            )
            if entry_point_match:
                _log.debug(f"Found entry_points in setup.py: {entry_point_match.group(1)}")
                # Could parse this further, but it's complex
        except Exception as e:
            _log.debug(f"Error parsing setup.py: {e}")

    raise FileNotFoundError(
        f"Could not find agent entrypoint in {agent_dir}.\n"
        f"  Tried:\n"
        f"    1. Searching for vip_main() call in Python files\n"
        f"    2. Looking for agent.py files\n"
        f"    3. Parsing setup.py\n"
        f"  Expected structure: {agent_dir.name}/package_name/module.py (with vip_main call)\n"
        f"  Hint: Use --module and --class to specify manually"
    )


def main():
    """Main entry point for the legacy agent launcher."""
    global _agent_instance

    parser = argparse.ArgumentParser(
        description="Run VOLTTRON agents on AEMS without code modifications (vctl-style)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--agent-dir",
        dest="agent_dir",
        required=True,
        help="Path to agent directory (e.g., example-from-volttron/ListenerAgent)",
    )

    parser.add_argument(
        "--module",
        dest="module_name",
        help="Agent module path (e.g., 'listener.agent' or 'sqlhistorian.historian'). "
        "If not provided, will auto-detect by searching for vip_main call",
        default=None,
    )

    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to agent configuration file (relative to agent-dir or absolute)",
        default=None,
    )

    parser.add_argument(
        "--identity",
        dest="identity",
        help="Agent identity (VIP identity)",
        default=None,
    )

    parser.add_argument(
        "--address",
        dest="address",
        help="AEMS message bus address (e.g., ws://localhost:8000)",
        default=os.environ.get("AEMS_MESSAGE_BUS_ADDRESS", "ws://localhost:8000"),
    )

    parser.add_argument(
        "--volttron-home",
        dest="volttron_home",
        help="VOLTTRON_HOME directory",
        default=os.environ.get("VOLTTRON_HOME", os.path.expanduser("~/.volttron")),
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--log-file", type=str, help="Log file path (logs to both console and file)")
    parser.add_argument("--keep", action="store_true", help="Append to log file instead of overwriting")

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.debug, args.log_file, args.keep)

    _log.info("=" * 60)
    _log.info("AEMS Legacy VOLTTRON Agent Launcher (vctl-style)")
    _log.info("=" * 60)

    # Resolve agent directory
    agent_dir = Path(args.agent_dir).resolve()
    if not agent_dir.exists():
        _log.error(f"Agent directory not found: {agent_dir}")
        sys.exit(1)

    if not agent_dir.is_dir():
        _log.error(f"Agent path is not a directory: {agent_dir}")
        sys.exit(1)

    _log.info(f"Agent Directory: {agent_dir}")

    # Determine module - use manual specification if provided, otherwise auto-detect
    if args.module_name:
        module_name = args.module_name
        _log.info(f"Using manually specified module: {module_name}")
    else:
        # Auto-detect agent module
        try:
            module_name, _ = find_agent_module_and_class(agent_dir)
        except FileNotFoundError as e:
            _log.error(str(e))
            sys.exit(1)

    # Resolve config file path
    config_path_abs = None
    if args.config_path:
        config_path = Path(args.config_path)

        # Try relative to agent directory first
        config_relative = agent_dir / config_path
        if config_relative.exists():
            config_path_abs = config_relative
            _log.debug(f"Config found relative to agent dir: {config_path_abs}")
        # Try absolute path
        elif config_path.is_absolute() and config_path.exists():
            config_path_abs = config_path
            _log.debug(f"Config found at absolute path: {config_path_abs}")
        # Try relative to current directory
        elif config_path.exists():
            config_path_abs = config_path.resolve()
            _log.debug(f"Config found relative to current dir: {config_path_abs}")
        else:
            _log.warning(f"Config file not found: {args.config_path}")
            _log.warning(f"  Tried: {config_relative}")
            _log.warning(f"  Tried: {config_path}")

    # Determine identity (use module name as fallback if not provided)
    identity = args.identity if args.identity else module_name.split(".")[-1].lower()

    # Set VOLTTRON_HOME
    os.environ["VOLTTRON_HOME"] = args.volttron_home

    # Determine config store location (matches server logic)
    config_store_dir = os.path.join(args.volttron_home, "aems_config_store", identity)

    _log.info(f"VOLTTRON_HOME: {args.volttron_home}")
    _log.info(f"Config Store:  {config_store_dir}")

    # Check if config store directory exists and show what's in it
    if os.path.exists(config_store_dir):
        configs = []
        for root, _dirs, files in os.walk(config_store_dir):
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), config_store_dir)
                configs.append(rel_path)
        if configs:
            _log.info(f"  Existing configs: {', '.join(sorted(configs))}")
        else:
            _log.info("  (empty - no configs found)")
    else:
        _log.info("  (does not exist yet)")

    _log.info("-" * 60)
    _log.info(f"Agent Module:  {module_name}")
    _log.info(f"Identity:      {identity}")
    _log.info(f"Address:       {args.address}")
    _log.info(f"Config:        {config_path_abs or '(none)'}")
    _log.info("-" * 60)

    # Save original directory for cleanup and AEMS module imports
    original_dir = Path.cwd()

    # First, install compatibility layer BEFORE changing directory
    # This ensures AEMS modules are loaded from the repo, not the agent directory
    try:
        # Add src to path for AEMS modules
        repo_root = original_dir  # Where we started (AEMS repo root)
        src_path = repo_root / "src"
        if src_path.exists():
            sys.path.insert(0, str(src_path))
            _log.debug(f"Added to sys.path for AEMS modules: {src_path}")

        from derhost.compat import install_volttron_compatibility

        install_volttron_compatibility()
        _log.debug("VOLTTRON compatibility layer installed")

        from derhost.compat.shims.platform_agent import utils  # noqa: F401

    except ImportError as e:
        _log.error(f"Failed to import AEMS compatibility layer: {e}")
        _log.error("Make sure you're running from the AEMS repository root")
        sys.exit(1)

    # IMPORTANT: Change to agent directory (like vctl start does)
    # This must happen AFTER AEMS imports but BEFORE agent module import
    # The agent module will be imported with its directory as the current working directory
    os.chdir(agent_dir)
    _log.info(f"Changed working directory to: {agent_dir}")
    _log.info(f"  Agent module will be imported from this directory (like 'python -m {module_name}')")

    # Add agent directory to Python path at the BEGINNING so imports work
    # This ensures the agent's package is found first
    sys.path.insert(0, str(agent_dir))
    _log.debug(f"Added to sys.path for agent imports: {agent_dir}")

    # Set up environment variables for the agent
    os.environ["AGENT_VIP_IDENTITY"] = identity
    os.environ["AGENT_CONFIG"] = str(config_path_abs) if config_path_abs else ""

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill command

    try:
        _log.info(f"Executing agent module (like 'python -m {module_name}')...")
        _log.info("  This will run the module's if __name__ == '__main__': block")
        _log.info("  which typically calls utils.vip_main() to handle:")
        _log.info(f"  - Loading config from: {config_path_abs or '(none)'}")
        _log.info("  - Instantiating agent (factory or class)")
        _log.info(f"  - Connecting to message bus: {args.address}")
        _log.info("  - Setting up subscriptions and starting agent")
        _log.info("=" * 60)

        # Prepare sys.argv for the agent module execution
        # AEMS vip_main uses ArgumentParser which reads sys.argv
        original_argv = sys.argv.copy()
        # Use a dummy script name for sys.argv[0]
        sys.argv = [module_name]

        # Add config if provided
        if config_path_abs:
            sys.argv.extend(["--config", str(config_path_abs)])

        # Add identity
        sys.argv.extend(["--identity", identity])

        # Add message bus address
        sys.argv.extend(["--address", args.address])

        _log.debug(f"sys.argv for agent: {sys.argv}")

        # Execute the module (runs if __name__ == '__main__': block)
        # This is equivalent to: python -m module_name
        # Note: vip_main will automatically create temp config if needed
        try:
            runpy.run_module(module_name, run_name="__main__", alter_sys=False)
        finally:
            # Restore original sys.argv
            sys.argv = original_argv

    except Exception as e:
        _log.exception(f"Error running agent: {e}")
        sys.exit(1)

    finally:
        # Cleanup (in case signal handler didn't run)
        if _agent_instance:
            try:
                _log.info("Performing final cleanup...")
                if hasattr(_agent_instance, "core") and hasattr(_agent_instance.core, "stop"):
                    _agent_instance.core.stop()
                _agent_instance.disconnect()
                _log.info("✓ Agent stopped cleanly")
            except Exception as e:
                _log.debug(f"Cleanup already performed or error: {e}")
            finally:
                _agent_instance = None

        # Change back to original directory
        with contextlib.suppress(Exception):
            os.chdir(original_dir)


if __name__ == "__main__":
    main()
