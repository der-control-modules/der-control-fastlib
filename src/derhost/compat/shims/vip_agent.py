"""
VOLTTRON VIP Agent compatibility shim.

Provides: Agent, Core, PubSub, RPC
Maps to: derhost.client.agent equivalents
"""

import logging

from derhost.client.agent import RPC as AEMSRPC, Agent as AEMSAgent, Core as AEMSCore
from derhost.compat.shims.health_subsystem import Health
from derhost.compat.shims.heartbeat import Heartbeat

_log = logging.getLogger(__name__)


# Extend Core to add version() method BEFORE defining Agent
class Core(AEMSCore):
    """
    VOLTTRON-compatible Core subsystem.

    Extends AEMS Core to add version() method.
    """

    def __init__(self, agent):
        super().__init__(agent)
        # Try to get version from agent's module
        self._version = self._get_agent_version(agent)

    def _get_agent_version(self, agent):
        """
        Get version from agent's module.

        Looks for __version__ attribute in the agent's module.
        """
        import inspect

        # Try to get version from agent's class module
        try:
            agent_module = inspect.getmodule(agent.__class__)
            if agent_module and hasattr(agent_module, "__version__"):
                return agent_module.__version__
        except Exception:
            # Ignore any errors reading version - use fallback
            pass

        # Fallback to default
        return "1.0.0"

    def version(self):
        """Return agent version (VOLTTRON compatibility)."""
        return self._version


# Export Agent directly from AEMS with VOLTTRON subsystems added
class Agent(AEMSAgent):
    """
    VOLTTRON-compatible Agent class.

    This extends AEMS Agent to add VOLTTRON-specific subsystems:
    - vip.heartbeat
    - vip.health
    - Enhanced Core with version()
    """

    def __init__(self, **kwargs):
        """
        Initialize VOLTTRON-compatible agent.

        VOLTTRON agents typically don't pass 'identity' in __init__,
        it gets set later via vip_main or command line.
        """
        # VOLTTRON agents may not provide identity in __init__
        # It's typically set via environment or vip_main
        identity = kwargs.pop("identity", None)
        address = kwargs.pop("address", None)

        # Store for later connection
        self._pending_identity = identity
        self._pending_address = address
        self._pending_kwargs = kwargs

        # Parse address into host and port for AEMS Agent
        # VOLTTRON uses "ws://host:port" format
        # AEMS Agent expects separate host and port parameters
        host = kwargs.pop("host", "127.0.0.1")
        port = kwargs.pop("port", 8000)

        if address:
            # Parse ws://host:port format
            import re

            match = re.match(r"ws://([^:]+):(\d+)", address)
            if match:
                host = match.group(1)
                port = int(match.group(2))
                _log.debug(f"Parsed address '{address}' to host='{host}', port={port}")
            else:
                _log.warning(f"Could not parse address '{address}', using defaults")

        # Initialize AEMS agent with parsed host and port
        super().__init__(identity=identity or "volttron_agent", host=host, port=port, **kwargs)

        # Replace core with VOLTTRON-compatible Core
        self.core = Core(self)

        # Add VOLTTRON-specific subsystems
        self.vip.heartbeat = Heartbeat(self)
        self.vip.health = Health(self)


# Export RPC directly - it's compatible
RPC = AEMSRPC

# Export BasicAgent as an alias for Agent (VOLTTRON compatibility)
BasicAgent = Agent


# VOLTTRON VIP errors
class errors:
    """VOLTTRON VIP error classes."""

    class VIPError(Exception):
        """Base VIP error."""

        pass

    class Again(VIPError):
        """Operation should be tried again."""

        pass

    class Unreachable(VIPError):
        """Peer is unreachable."""

        pass


# PubSub is accessed via agent.vip.pubsub, but we also export the decorator
class PubSub:
    """
    VOLTTRON PubSub compatibility layer.

    In VOLTTRON, @PubSub.subscribe() is used as a decorator.
    In AEMS, we use agent.vip.pubsub.subscribe() directly.

    This provides the decorator interface for compatibility.
    """

    @staticmethod
    def subscribe(bus, topic, **kwargs):
        """
        Decorator for subscribing to PubSub topics (VOLTTRON style).

        Usage:
            @PubSub.subscribe('pubsub', 'topic/name')
            def on_message(self, peer, sender, bus, topic, headers, message):
                pass

        Args:
            bus: Message bus (usually 'pubsub')
            topic: Topic pattern to subscribe to
            **kwargs: Additional subscription options (e.g., all_platforms=True)
        """

        def decorator(func):
            # Mark the function as needing subscription
            # The actual subscription will happen during agent startup
            if not hasattr(func, "_pubsub_subscriptions"):
                func._pubsub_subscriptions = []

            func._pubsub_subscriptions.append({"bus": bus, "topic": topic, "kwargs": kwargs})

            return func

        return decorator


# For module-level imports
__all__ = ["Agent", "Core", "RPC", "PubSub", "BasicAgent", "errors"]
