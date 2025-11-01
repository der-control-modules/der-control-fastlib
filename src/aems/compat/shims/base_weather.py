"""
VOLTTRON base_weather compatibility shim.

Re-exports BaseWeatherAgent from the original VOLTTRON installation.
This allows weather agents to work with AEMS while using VOLTTRON's
base weather agent implementation.

We load the module directly from the filesystem to avoid circular imports
that would occur if we used the normal import system.
"""

import importlib.util

# Load the original VOLTTRON module directly from filesystem to avoid circular imports
# This bypasses the import hook system entirely
_module_path = "/home/volttron/volttron/volttron/platform/agent/base_weather.py"
_spec = importlib.util.spec_from_file_location("volttron.platform.agent.base_weather", _module_path)
_original_module = importlib.util.module_from_spec(_spec)

# Execute the module to populate it
_spec.loader.exec_module(_original_module)

# Re-export everything from the original module
BaseWeatherAgent = _original_module.BaseWeatherAgent

# Re-export module-level items
__all__ = []
for name in dir(_original_module):
    if not name.startswith("_"):
        globals()[name] = getattr(_original_module, name)
        __all__.append(name)
