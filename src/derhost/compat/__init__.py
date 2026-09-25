"""
VOLTTRON Compatibility Layer for AEMS

This module provides import hooks and compatibility shims to run existing
VOLTTRON agents without code modifications.
"""

from derhost.compat.import_hook import install_volttron_compatibility

__all__ = ["install_volttron_compatibility"]
