"""
VOLTTRON VIP Agent errors compatibility shim.

This module provides VIP error classes for VOLTTRON compatibility.
It exists as a separate module to support imports like:
    from volttron.platform.vip.agent import errors
    except errors.Unreachable:
        ...
"""


class VIPError(Exception):
    """Base VIP error."""

    pass


class Again(VIPError):
    """Operation should be tried again."""

    pass


class Unreachable(VIPError):
    """Peer is unreachable."""

    pass


__all__ = ["VIPError", "Again", "Unreachable"]
