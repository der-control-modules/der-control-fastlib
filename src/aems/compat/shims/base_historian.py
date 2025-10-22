"""
Shim for volttron.platform.agent.base_historian module.

This shim imports BaseHistorian from the actual VOLTTRON source code
when available, allowing historian agents to work without modification.
"""

import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Try to import from actual VOLTTRON installation
BaseHistorian = None
BaseHistorianAgent = None
BaseQueryHistorianAgent = None

# Common VOLTTRON installation locations
VOLTTRON_SEARCH_PATHS = [
    "/home/volttron/volttron",  # Development location
    Path.home() / "volttron",  # User's home directory
    "/opt/volttron",  # System installation
]

# Find and import from VOLTTRON source
for volttron_path in VOLTTRON_SEARCH_PATHS:
    volttron_path = Path(volttron_path)
    base_historian_file = volttron_path / "volttron" / "platform" / "agent" / "base_historian.py"

    if base_historian_file.exists():
        _log.debug(f"Found VOLTTRON base_historian at: {base_historian_file}")

        # Add VOLTTRON source to path so regular imports work
        volttron_src = str(volttron_path)
        if volttron_src not in sys.path:
            sys.path.insert(0, volttron_src)
            _log.debug(f"Added VOLTTRON source to path: {volttron_src}")

        try:
            # Import using importlib to bypass the import hook
            # We can't use regular imports because our import hook will intercept them
            import importlib.machinery
            import importlib.util

            loader = importlib.machinery.SourceFileLoader("volttron_real_base_historian", str(base_historian_file))
            spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(base_historian_file))
            _base_historian_module = importlib.util.module_from_spec(spec)

            # Store module in sys.modules BEFORE executing so ply can find it
            sys.modules["volttron_real_base_historian"] = _base_historian_module

            # Execute the module
            loader.exec_module(_base_historian_module)

            # Extract the classes we need
            BaseHistorian = getattr(_base_historian_module, "BaseHistorian", None)
            BaseHistorianAgent = getattr(_base_historian_module, "BaseHistorianAgent", None)
            BaseQueryHistorianAgent = getattr(_base_historian_module, "BaseQueryHistorianAgent", None)

            if BaseHistorian:
                _log.info("Successfully loaded BaseHistorian from VOLTTRON source")
                break
            else:
                _log.warning("BaseHistorian class not found in module")
        except Exception as e:
            _log.warning(f"Failed to import BaseHistorian from {volttron_path}: {e}")
            import traceback

            _log.debug(f"Traceback: {traceback.format_exc()}")
            # Continue trying other paths
            continue

# If we couldn't import from VOLTTRON source, provide a helpful error
if BaseHistorian is None:
    _log.error(
        f"Could not find VOLTTRON BaseHistorian class. Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
    )

    # Create a placeholder that gives a clear error message
    class BaseHistorian:
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "BaseHistorian requires the VOLTTRON source code. "
                "Please ensure VOLTTRON is installed or available in one of: "
                f"{[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
            )

    class BaseHistorianAgent:
        def __init__(self, *args, **kwargs):
            raise ImportError("BaseHistorianAgent requires VOLTTRON source code")

    class BaseQueryHistorianAgent:
        def __init__(self, *args, **kwargs):
            raise ImportError("BaseQueryHistorianAgent requires VOLTTRON source code")


# Export the classes
__all__ = ["BaseHistorian", "BaseHistorianAgent", "BaseQueryHistorianAgent"]
