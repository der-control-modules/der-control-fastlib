"""
VOLTTRON VIP compatibility shim.

Provides: vip namespace
"""

# Re-export agent module
from aems.compat.shims import vip_agent

__all__ = ["vip_agent"]
