#!/usr/bin/env python3
r"""
VOLTTRON Legacy Agent Launcher for AEMS.

This script allows running existing VOLTTRON agents without code modifications.
It changes to the agent's directory and runs it from there, just like vctl start.

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
    --config PATH       Path to agent configuration file (relative to agent-dir)
    --identity ID       Agent identity/name
    --address URL       AEMS message bus address (default: ws://localhost:8000)
    --volttron-home PATH  VOLTTRON_HOME directory
    --debug             Enable debug logging
    --help              Show this help message

Features:
    - Auto-detects module and class from directory structure
    - Installs VOLTTRON compatibility shims via import hooks
    - Intelligently passes config parameters based on agent __init__ signature
    - Supports agents with custom required parameters (e.g., PlatformDriverAgent)
"""

import argparse
import contextlib
import importlib
import importlib.util
import logging
import os
import signal
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Global agent reference for signal handler
_agent_instance = None


def setup_logging(debug=False):
    """Configure logging for the launcher."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    if debug:
        # Enable debug logging for key AEMS modules
        logging.getLogger("aems.client.agent").setLevel(logging.DEBUG)
        logging.getLogger("aems.compat").setLevel(logging.DEBUG)
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

    Looks for the standard VOLTTRON agent structure:
        AgentName/
            agent_package/
                agent.py  (contains AgentClass)

    Args:
        agent_dir: Path to agent directory

    Returns:
        tuple: (module_name, class_name) e.g., ("listener.agent", "ListenerAgent")

    Raises:
        FileNotFoundError: If agent module not found
    """
    agent_dir = Path(agent_dir).resolve()

    _log.debug(f"Searching for agent in: {agent_dir}")

    # Look for subdirectories (agent packages)
    for item in agent_dir.iterdir():
        if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
            # Check if this directory has an agent.py file
            agent_file = item / "agent.py"
            if agent_file.exists():
                _log.debug(f"Found agent.py in {item.name}/")

                # Read the file to find the agent class
                with open(agent_file) as f:
                    content = f.read()

                # Look for class definitions that inherit from Agent
                import re

                class_matches = re.findall(r"class\s+(\w+)\s*\([^)]*Agent[^)]*\)", content)

                if class_matches:
                    class_name = class_matches[0]  # Use first match
                    module_name = f"{item.name}.agent"

                    _log.info(f"Auto-detected agent: {module_name}:{class_name}")
                    return module_name, class_name

    # Fallback: look for setup.py to get package name
    setup_file = agent_dir / "setup.py"
    if setup_file.exists():
        _log.debug("Found setup.py, parsing for agent info")
        # Could parse setup.py for entry points, but for now return None

    raise FileNotFoundError(
        f"Could not find agent.py in any subdirectory of {agent_dir}. "
        f"Expected structure: {agent_dir.name}/package_name/agent.py"
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

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.debug)

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

    # Auto-detect agent module and class
    try:
        module_name, class_name = find_agent_module_and_class(agent_dir)
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

    # Determine identity
    identity = args.identity if args.identity else class_name.lower()

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
    _log.info(f"Agent Class:   {class_name}")
    _log.info(f"Identity:      {identity}")
    _log.info(f"Address:       {args.address}")
    _log.info(f"Config:        {config_path_abs or '(none)'}")
    _log.info("-" * 60)

    # IMPORTANT: Change to agent directory (like vctl start does)
    original_dir = Path.cwd()
    os.chdir(agent_dir)
    _log.info(f"Changed working directory to: {agent_dir}")

    # Add agent directory to Python path so imports work
    sys.path.insert(0, str(agent_dir))
    _log.debug(f"Added to sys.path: {agent_dir}")

    # Now install compatibility layer AFTER changing directory
    # This ensures any relative imports work correctly
    try:
        # Add src to path for AEMS modules
        repo_root = original_dir  # Where we started
        src_path = repo_root / "src"
        if src_path.exists():
            sys.path.insert(0, str(src_path))
            _log.debug(f"Added to sys.path: {src_path}")

        from aems.compat import install_volttron_compatibility

        install_volttron_compatibility()
        _log.debug("VOLTTRON compatibility layer installed")

        from aems.compat.shims.platform_agent import utils

    except ImportError as e:
        _log.error(f"Failed to import AEMS compatibility layer: {e}")
        _log.error("Make sure you're running from the AEMS repository root")
        sys.exit(1)

    try:
        # Import the agent module
        _log.info(f"Importing module: {module_name}")
        agent_module = importlib.import_module(module_name)
        _log.debug(f"Module imported successfully: {agent_module}")

        # Get agent version if available
        agent_version = getattr(agent_module, "__version__", "unknown")
        if agent_version != "unknown":
            _log.info(f"Agent version: {agent_version}")

        # Get the agent class
        try:
            agent_class = getattr(agent_module, class_name)
            _log.info(f"Loaded agent class: {class_name}")
        except AttributeError:
            _log.error(f"Class {class_name} not found in module {module_name}")
            _log.error(f"Available classes: {[name for name in dir(agent_module) if not name.startswith('_')]}")
            sys.exit(1)

        # Prepare agent initialization parameters
        agent_kwargs = {"identity": identity, "address": args.address}

        # Load and parse config if provided
        config_dict = {}
        if config_path_abs:
            _log.info(f"Loading config from: {config_path_abs}")
            try:
                # Try to parse as JSON (with comments)
                config_text = config_path_abs.read_text()
                # Remove JSON comments (VOLTTRON configs often have comments)
                import re

                config_text = re.sub(r"#.*$", "", config_text, flags=re.MULTILINE)
                import json

                config_dict = json.loads(config_text)
                _log.debug(f"Config loaded: {list(config_dict.keys())}")
            except Exception as e:
                _log.warning(f"Failed to parse config file: {e}")
                _log.warning("Will try instantiating with just config_path parameter")
                agent_kwargs["config_path"] = str(config_path_abs)

        # Inspect agent class __init__ to see what parameters it accepts
        import inspect

        sig = inspect.signature(agent_class.__init__)
        params = sig.parameters
        param_names = list(params.keys())
        _log.debug(f"Agent __init__ parameters: {param_names}")

        # Handle config parameters
        if config_dict:
            # Add config_path if it's in the signature
            if "config_path" in param_names and config_path_abs:
                agent_kwargs["config_path"] = str(config_path_abs)

            # Add all config values that match __init__ parameters
            for key, value in config_dict.items():
                if key in param_names:
                    agent_kwargs[key] = value
                    _log.debug(f"  Using config parameter: {key} = {value}")

        # Check for required parameters (no default value) that are still missing
        for param_name, param in params.items():
            if param_name in ("self", "kwargs"):
                continue
            # Check if parameter is required (no default value)
            if param.default is inspect.Parameter.empty:
                if param_name not in agent_kwargs:
                    # Provide sensible defaults for common required parameters
                    if param_name == "driver_config_list":
                        _log.warning(f"Required parameter '{param_name}' not in config, using empty list")
                        _log.info("  Note: PlatformDriverAgent typically gets driver configs from config store")
                        agent_kwargs[param_name] = []
                    elif param_name == "config_path":
                        if config_path_abs:
                            agent_kwargs[param_name] = str(config_path_abs)
                        else:
                            _log.warning(f"Required parameter '{param_name}' not provided")
                    else:
                        _log.warning(f"Required parameter '{param_name}' is missing and has no default!")

        # Instantiate the agent
        _log.info(f"Instantiating agent with parameters: {list(agent_kwargs.keys())}")
        agent = agent_class(**agent_kwargs)

        _log.info("Agent instantiated successfully")

        # Set global agent instance for signal handler
        _agent_instance = agent

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # kill command

        # Connect to message bus
        _log.info("Connecting to AEMS message bus...")
        agent.connect()
        _log.info(f"✓ Connected to {args.address}")

        # Process @PubSub.subscribe decorators
        utils._setup_pubsub_subscriptions(agent)

        _log.info("=" * 60)
        _log.info(f"✓ {class_name} is running from {agent_dir.name}/")
        _log.info("  Press Ctrl+C to stop")
        _log.info("=" * 60)

        # Run until interrupted
        try:
            import time

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            # This may not be reached if signal handler exits first
            _log.info("\nReceived keyboard interrupt, stopping agent...")

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
