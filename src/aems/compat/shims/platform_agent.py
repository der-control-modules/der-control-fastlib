"""
VOLTTRON platform.agent compatibility shim.

Provides: utils module with load_config, vip_main, setup_logging
"""

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path

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
        # Parse command line arguments (VOLTTRON style)
        parser = argparse.ArgumentParser(description=f"Run {agent_class.__name__}")

        parser.add_argument("--config", dest="config_path", help="Path to agent configuration file", default=None)

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

        _log.info(f"Starting {agent_class.__name__} version {version}")
        _log.info(f"  Identity: {args.identity}")
        _log.info(f"  Address: {args.address}")
        _log.info(f"  Config: {args.config_path}")

        try:
            # Instantiate the agent
            # VOLTTRON agents expect config_path as first argument
            if args.config_path:
                agent = agent_class(config_path=args.config_path, identity=args.identity, address=args.address)
            else:
                agent = agent_class(identity=args.identity, address=args.address)

            # Store version
            if hasattr(agent, "core"):
                agent.core._version = version

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
            # Cleanup
            if "agent" in locals():
                with contextlib.suppress(Exception):
                    agent.disconnect()

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


# Export utils and other modules at module level
__all__ = ["utils", "known_identities", "math_utils"]
