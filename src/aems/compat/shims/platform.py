"""
VOLTTRON platform compatibility shim.

Provides: platform namespace as a proper package
"""

# Import submodules to make them available as attributes
from aems.client.jsonrpc import RemoteError
from aems.compat.shims import messaging as messaging_module, platform_agent, vip as vip_module

# Make submodules available as package attributes
agent = platform_agent
vip = vip_module
messaging = messaging_module


# Create jsonrpc module namespace to support: from volttron.platform.jsonrpc import RemoteError
class jsonrpc:
    """VOLTTRON JSON-RPC compatibility namespace."""

    RemoteError = RemoteError


# Mark this as a package
__path__ = []
__package__ = "volttron.platform"

__all__ = ["agent", "vip", "messaging", "jsonrpc", "RemoteError"]


# AsyncCall - simplified version for AEMS
class AsyncCall:
    """Simplified AsyncCall for AEMS (no gevent hub needed)."""

    def __init__(self, hub=None):
        """Initialize AsyncCall."""
        self.calls = []

    def send(self, receiver, func, *args, **kwargs):
        """Execute function (simplified - no thread switching in AEMS).

        VOLTTRON AsyncCall signature: send(receiver, func, *args, **kwargs)
        - receiver: Target receiver (None for local calls)
        - func: The actual function to call
        - args/kwargs: Arguments to pass to func
        """
        import logging

        _log = logging.getLogger(__name__)
        try:
            _log.debug(f"AsyncCall.send() calling {func} with args={args}, kwargs={kwargs}")
            result = func(*args, **kwargs)
            _log.debug(f"AsyncCall.send() completed, result={result}")
            return result
        except Exception as e:
            _log.error(f"AsyncCall.send() failed calling {func}: {e}", exc_info=True)
            raise
