"""
Shim for volttron.platform.dbutils.sqlutils module.

Loads the get_dbfuncts_class function from the actual VOLTTRON source code.
This is the entry point used by the SQL Historian to resolve database drivers.
"""

import importlib.machinery
import importlib.util
import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

VOLTTRON_SEARCH_PATHS = [
    "/volttron",
    "/home/volttron/volttron",
    Path.home() / "volttron",
    "/opt/volttron",
]

get_dbfuncts_class = None

for volttron_path in VOLTTRON_SEARCH_PATHS:
    volttron_path = Path(volttron_path)
    sqlutils_file = volttron_path / "volttron" / "platform" / "dbutils" / "sqlutils.py"

    if sqlutils_file.exists():
        _log.debug(f"Found sqlutils.py at: {sqlutils_file}")

        volttron_src = str(volttron_path)
        if volttron_src not in sys.path:
            sys.path.insert(0, volttron_src)

        try:
            full_module_name = "volttron.platform.dbutils.sqlutils"
            if full_module_name not in sys.modules:
                # Ensure parent package and basedb are loaded first
                if "volttron.platform.dbutils" not in sys.modules:
                    import aems.compat.shims.dbutils  # noqa: F401

                if "volttron.platform.dbutils.basedb" not in sys.modules:
                    import aems.compat.shims.dbutils_basedb  # noqa: F401

                loader = importlib.machinery.SourceFileLoader(full_module_name, str(sqlutils_file))
                spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(sqlutils_file))
                sqlutils_mod = importlib.util.module_from_spec(spec)
                sys.modules[full_module_name] = sqlutils_mod
                loader.exec_module(sqlutils_mod)
            else:
                sqlutils_mod = sys.modules[full_module_name]

            get_dbfuncts_class = sqlutils_mod.get_dbfuncts_class
            _log.info("Successfully loaded sqlutils from VOLTTRON source")
            break

        except Exception as e:
            _log.warning(f"Failed to import sqlutils from {volttron_path}: {e}")
            import traceback
            _log.debug(f"Traceback: {traceback.format_exc()}")
            continue

if get_dbfuncts_class is None:
    _log.error(
        f"Could not find sqlutils module. Searched paths: "
        f"{[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
    )

    def get_dbfuncts_class(database_type):
        raise ImportError(
            f"get_dbfuncts_class() requires VOLTTRON source code. "
            f"Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
        )


__all__ = ["get_dbfuncts_class"]
