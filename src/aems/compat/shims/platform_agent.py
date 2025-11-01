"""
VOLTTRON platform.agent compatibility shim.

Provides: utils module with load_config, vip_main, setup_logging
"""

import argparse
import contextlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytz
from dateutil.parser import parse as parse_date

_log = logging.getLogger(__name__)


class utils:
    """
    VOLTTRON agent utilities compatibility layer.

    Provides common utility functions used by VOLTTRON agents.
    """

    @staticmethod
    def setup_logging(level=logging.INFO):
        """
        Setup logging for the agent (VOLTTRON style).

        In VOLTTRON, this sets up the platform logging.
        In AEMS, we just configure basic logging.
        """
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _log.debug("Logging configured (VOLTTRON compatibility mode)")

    @staticmethod
    def load_config(config_path):
        """
        Load configuration file (VOLTTRON style).

        Args:
            config_path: Path to configuration file (JSON or Python dict)

        Returns:
            dict: Configuration dictionary
        """
        if not config_path:
            _log.warning("No config path provided, returning empty config")
            return {}

        config_path = Path(config_path)

        if not config_path.exists():
            _log.warning(f"Config file not found: {config_path}, returning empty config")
            return {}

        try:
            with open(config_path) as f:
                config = json.load(f)
            _log.info(f"Loaded configuration from {config_path}")
            return config
        except json.JSONDecodeError as e:
            _log.error(f"Failed to parse JSON config {config_path}: {e}")
            return {}
        except Exception as e:
            _log.error(f"Failed to load config {config_path}: {e}")
            return {}

    @staticmethod
    def vip_main(agent_class, version="1.0", **kwargs):
        """
        Main entry point for VOLTTRON agents (compatibility mode).

        This mimics volttron.platform.agent.utils.vip_main() behavior.

        Args:
            agent_class: Agent class to instantiate
            version: Agent version string
            **kwargs: Additional arguments passed to agent

        The agent is expected to be run via command line with these args:
            --config <path>      Configuration file path
            --identity <name>    Agent identity
            --address <url>      Message bus address (ws://host:port)
        """
        # Apply platform driver patches (for older VOLTTRON versions)
        try:
            from aems.compat.platform_driver_patches import apply_all_patches

            apply_all_patches()
        except Exception as e:
            _log.debug(f"Platform driver patches not applied (may not be needed): {e}")

        # Parse command line arguments (VOLTTRON style)
        parser = argparse.ArgumentParser(description=f"Run {agent_class.__name__}")

        parser.add_argument(
            "--config",
            dest="config_path",
            help="Path to agent configuration file",
            default=None,
        )

        parser.add_argument(
            "--identity",
            dest="identity",
            help="Agent identity (VIP identity)",
            default=agent_class.__name__.lower(),
        )

        parser.add_argument(
            "--address",
            dest="address",
            help="Message bus address (e.g., ws://localhost:8000)",
            default=os.environ.get("AEMS_MESSAGE_BUS_ADDRESS", "ws://localhost:8000"),
        )

        parser.add_argument(
            "--volttron-home",
            dest="volttron_home",
            help="VOLTTRON_HOME directory",
            default=os.environ.get("VOLTTRON_HOME", os.path.expanduser("~/.volttron")),
        )

        args = parser.parse_args()

        # Set VOLTTRON_HOME if provided
        if args.volttron_home:
            os.environ["VOLTTRON_HOME"] = args.volttron_home

        # Track temporary config file for cleanup
        temp_config_path = None

        # All legacy VOLTTRON agents require config_path
        # Create temporary empty config if not provided
        if not args.config_path:
            _log.info("No config file provided, creating temporary empty config...")

            # Create temporary config file
            import json
            import tempfile

            temp_fd, temp_config_path = tempfile.mkstemp(suffix=".json", prefix=f"{args.identity}_config_", text=True)

            # Write empty JSON config
            with os.fdopen(temp_fd, "w") as f:
                json.dump({}, f)

            args.config_path = temp_config_path
            _log.info(f"  Created temporary config: {temp_config_path}")
            _log.info("  (contains empty JSON: {})")

        _log.info(f"Starting {agent_class.__name__} version {version}")
        _log.info(f"  Identity: {args.identity}")
        _log.info(f"  Address: {args.address}")
        _log.info(f"  Config: {args.config_path}")

        try:
            # Instantiate the agent with config_path
            # All VOLTTRON agents expect config_path as first argument
            agent = agent_class(
                config_path=args.config_path,
                identity=args.identity,
                address=args.address,
            )

            # Store version
            if hasattr(agent, "core"):
                agent.core._version = version

            # WORKAROUND: Give BACpypes time to finish initialization
            # BACpypes binds to network interface in __init__, which can interfere
            # with WebSocket connection if we connect too quickly
            import time

            time.sleep(2)
            _log.info("Waiting 2s after agent init before connecting...")

            # Connect to message bus
            agent.connect()

            # Process @PubSub.subscribe decorators
            utils._setup_pubsub_subscriptions(agent)

            _log.info(f"{agent_class.__name__} started successfully")

            # Run until interrupted
            try:
                import time

                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                _log.info("Received keyboard interrupt, stopping agent")

        except Exception as e:
            _log.exception(f"Error running agent: {e}")
            raise

        finally:
            # Remove temporary config file if we created one
            if temp_config_path and os.path.exists(temp_config_path):
                try:
                    os.unlink(temp_config_path)
                    _log.debug(f"Removed temporary config file: {temp_config_path}")
                except Exception as e:
                    _log.debug(f"Failed to remove temporary config file: {e}")

            # Cleanup
            if "agent" in locals():
                with contextlib.suppress(Exception):
                    agent.disconnect()

    @staticmethod
    def format_timestamp(time_stamp):
        """Create a consistent datetime string representation based on ISO 8601 format."""
        time_str = time_stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")

        if time_stamp.tzinfo is not None:
            sign = "+"
            td = time_stamp.tzinfo.utcoffset(time_stamp)
            if td.days < 0:
                sign = "-"
                td = -td

            seconds = td.seconds
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            time_str += f"{sign}{hours:02}:{minutes:02}"

        return time_str

    @staticmethod
    def parse_timestamp_string(time_stamp_str):
        """Parse timestamp string to datetime object."""
        return parse_date(time_stamp_str)

    @staticmethod
    def get_aware_utc_now():
        """Get current UTC time as timezone-aware datetime."""
        return datetime.now(pytz.UTC)

    @staticmethod
    def process_timestamp(timestamp_string, topic=""):
        """Process timestamp string from VOLTTRON message.

        Returns:
            tuple: (timestamp, original_tz) - UTC datetime and original timezone
        """
        if timestamp_string is None:
            _log.error(f"message for {topic} missing timestamp")
            return None, None

        try:
            if isinstance(timestamp_string, str):
                timestamp = utils.parse_timestamp_string(timestamp_string)
            else:
                timestamp = timestamp_string
        except (ValueError, TypeError):
            _log.error(f"message for {topic} bad timestamp string: {timestamp_string}")
            return None, None

        # Handle timezone
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=pytz.UTC)
            original_tz = None
        else:
            original_tz = timestamp.tzinfo
            timestamp = timestamp.astimezone(pytz.UTC)

        return timestamp, original_tz

    @staticmethod
    def fix_sqlite3_datetime(sql=None):
        """Register datetime converters for SQLite compatibility."""
        if sql is None:
            import sqlite3 as sql

        def parse(time_stamp_bytes):
            return utils.parse_timestamp_string(time_stamp_bytes.decode("utf-8"))

        sql.register_adapter(datetime, utils.format_timestamp)
        sql.register_converter("timestamp", parse)

    @staticmethod
    def update_kwargs_with_config(kwargs, config_dict):
        """Update kwargs dictionary with config values (VOLTTRON pattern)."""
        kwargs.update(config_dict)

    @staticmethod
    def is_secure_mode():
        """Check if VOLTTRON is running in secure mode (always False in AEMS)."""
        return False

    @staticmethod
    def _setup_pubsub_subscriptions(agent):
        """
        Set up PubSub subscriptions from @PubSub.subscribe decorators.

        Scans agent methods for _pubsub_subscriptions attribute and
        registers them with the agent's pubsub subsystem.
        """
        for attr_name in dir(agent):
            try:
                attr = getattr(agent, attr_name)
                if hasattr(attr, "_pubsub_subscriptions"):
                    for sub in attr._pubsub_subscriptions:
                        topic = sub["topic"]
                        # Subscribe using AEMS pubsub
                        # The callback signature matches VOLTTRON
                        agent.vip.pubsub.subscribe("", topic, attr)
                        _log.info(f"Subscribed {attr_name} to topic: {topic}")
            except Exception as e:
                _log.warning(f"Failed to setup subscription for {attr_name}: {e}")


# VOLTTRON known_identities constants
class known_identities:
    """VOLTTRON known agent identities (constants)."""

    PLATFORM_DRIVER = "platform.driver"
    CONTROL = "control"
    CONFIGURATION_STORE = "config.store"
    PLATFORM_HEALTH = "platform.health"
    PLATFORM_WEB = "platform.web"


# Math utilities placeholder
class math_utils:
    """Placeholder for VOLTTRON math utilities."""

    pass


# Export utils functions at module level for direct imports
# This allows: from volttron.platform.agent.utils import fix_sqlite3_datetime
fix_sqlite3_datetime = utils.fix_sqlite3_datetime
format_timestamp = utils.format_timestamp
parse_timestamp_string = utils.parse_timestamp_string
is_secure_mode = utils.is_secure_mode
get_aware_utc_now = utils.get_aware_utc_now
process_timestamp = utils.process_timestamp
update_kwargs_with_config = utils.update_kwargs_with_config
load_config = utils.load_config
setup_logging = utils.setup_logging
vip_main = utils.vip_main

# Export utils and other modules at module level
__all__ = [
    "utils",
    "known_identities",
    "math_utils",
    "fix_sqlite3_datetime",
    "format_timestamp",
    "parse_timestamp_string",
    "get_aware_utc_now",
    "process_timestamp",
    "update_kwargs_with_config",
    "load_config",
    "setup_logging",
    "vip_main",
    "is_secure_mode",
]
