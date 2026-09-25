"""
VOLTTRON VIP compatibility shim.

Provides: vip namespace
"""

# Re-export agent module
from derhost.compat.shims import vip_agent

__all__ = ["vip_agent"]
