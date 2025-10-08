"""
VOLTTRON platform compatibility shim.

Provides: platform namespace as a proper package
"""

# Import submodules to make them available as attributes
from aems.compat.shims import messaging as messaging_module, platform_agent, vip as vip_module

# Make submodules available as package attributes
agent = platform_agent
vip = vip_module
messaging = messaging_module

# Mark this as a package
__path__ = []
__package__ = "volttron.platform"

__all__ = ["agent", "vip", "messaging"]
