"""
Shim for volttron.platform.dbutils.basedb module.

Loads the DbDriver base class from the actual VOLTTRON source code.
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

DbDriver = None

for volttron_path in VOLTTRON_SEARCH_PATHS:
    volttron_path = Path(volttron_path)
    basedb_file = volttron_path / "volttron" / "platform" / "dbutils" / "basedb.py"

    if basedb_file.exists():
        _log.debug(f"Found basedb.py at: {basedb_file}")

        volttron_src = str(volttron_path)
        if volttron_src not in sys.path:
            sys.path.insert(0, volttron_src)

        try:
            full_module_name = "volttron.platform.dbutils.basedb"
            if full_module_name not in sys.modules:
                # Ensure the parent dbutils package exists
                if "volttron.platform.dbutils" not in sys.modules:
                    import aems.compat.shims.dbutils  # noqa: F401

                loader = importlib.machinery.SourceFileLoader(full_module_name, str(basedb_file))
                spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(basedb_file))
                basedb_mod = importlib.util.module_from_spec(spec)
                sys.modules[full_module_name] = basedb_mod
                loader.exec_module(basedb_mod)
            else:
                basedb_mod = sys.modules[full_module_name]

            DbDriver = basedb_mod.DbDriver
            _log.info("Successfully loaded basedb from VOLTTRON source")
            break

        except Exception as e:
            _log.warning(f"Failed to import basedb from {volttron_path}: {e}")
            import traceback
            _log.debug(f"Traceback: {traceback.format_exc()}")
            continue

if DbDriver is None:
    _log.error(
        f"Could not find basedb module. Searched paths: "
        f"{[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
    )

    class DbDriver:
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "DbDriver requires VOLTTRON source code. "
                f"Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
            )


__all__ = ["DbDriver"]
