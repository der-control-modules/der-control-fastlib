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
    "/volttron",  # Docker container location (cloned in Dockerfile)
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

            # Explicitly set __file__ — spec.has_location may be False in Python 3.11
            # even when origin is set, causing inspect.getsourcelines() to fail inside ply.lex
            _base_historian_module.__file__ = str(base_historian_file)

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

# If we couldn't import from VOLTTRON source, provide a functional shim
if BaseHistorian is None:
    searched = [str(p) for p in VOLTTRON_SEARCH_PATHS]
    _log.warning(f"Could not load VOLTTRON BaseHistorian from source. Using AEMS shim. Searched: {searched}")

    from derhost.compat.shims.vip_agent import Agent as _Agent

    class BaseHistorian(_Agent):
        """AEMS shim for VOLTTRON BaseHistorian — inherits from Agent shim."""

        def __init__(self, *args, **kwargs):
            # Strip historian-specific kwargs that Agent shim doesn't understand
            kwargs.pop("historian_setup", None)
            kwargs.pop("publish_to_historian", None)
            kwargs.pop("query_historian", None)
            super().__init__(**kwargs)

        def historian_setup(self):
            """Override in subclass to set up historian-specific resources."""
            pass

        def publish_to_historian(self, to_publish_list):
            """Override in subclass to persist data."""
            pass

        def query_historian(
            self, topic, start=None, end=None, agg_type=None, agg_period=None, skip=0, count=None, order="FIRST_TO_LAST"
        ):
            """Override in subclass to query historical data."""
            return {"values": [], "metadata": {}}

        def query_topic_list(self):
            """Override in subclass to list available topics."""
            return []

        def query_topics_metadata(self, topics):
            """Override in subclass to get topic metadata."""
            return {}

        def query_topics_by_pattern(self, topic_pattern):
            """Override in subclass to query topics matching a pattern."""
            return {}

        def version(self):
            """Return the version of the historian."""
            return "1.0"

        def parse_table_def(self, tables_def):
            default_table_def = {
                "table_prefix": "",
                "data_table": "data",
                "topics_table": "topics",
                "meta_table": "meta",
            }
            if not tables_def:
                tables_def = default_table_def
            else:
                default_table_def.update(tables_def)
                tables_def = default_table_def
            table_names = dict(tables_def)
            table_prefix = tables_def.get("table_prefix", None)
            table_prefix = table_prefix + "_" if table_prefix else ""
            if table_prefix:
                for key, _value in list(table_names.items()):
                    table_names[key] = table_prefix + table_names[key]
            table_names["agg_topics_table"] = table_prefix + "aggregate_" + tables_def["topics_table"]
            table_names["agg_meta_table"] = table_prefix + "aggregate_" + tables_def["meta_table"]
            return tables_def, table_names

    class BaseHistorianAgent(_Agent):
        def __init__(self, *args, **kwargs):
            super().__init__(**kwargs)

    class BaseQueryHistorianAgent(_Agent):
        def __init__(self, *args, **kwargs):
            super().__init__(**kwargs)


# Export the classes
__all__ = ["BaseHistorian", "BaseHistorianAgent", "BaseQueryHistorianAgent"]
