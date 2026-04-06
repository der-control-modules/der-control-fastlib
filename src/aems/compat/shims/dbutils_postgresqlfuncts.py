"""
Shim for volttron.platform.dbutils.postgresqlfuncts module.

Loads the PostgreSqlFuncts class from the actual VOLTTRON source code.
This is needed by the SQL Historian agent when configured for PostgreSQL.
"""

import importlib.machinery
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

_log = logging.getLogger(__name__)

# Re-use the same search paths as the main dbutils shim
VOLTTRON_SEARCH_PATHS = [
    "/volttron",  # Docker container location (cloned in Dockerfile)
    "/home/volttron/volttron",  # Development location
    Path.home() / "volttron",  # User's home directory
    "/opt/volttron",  # System installation
]

PostgreSqlFuncts = None

for volttron_path in VOLTTRON_SEARCH_PATHS:
    volttron_path = Path(volttron_path)
    dbutils_dir = volttron_path / "volttron" / "platform" / "dbutils"
    postgresqlfuncts_file = dbutils_dir / "postgresqlfuncts.py"

    if postgresqlfuncts_file.exists():
        _log.debug(f"Found postgresqlfuncts.py at: {postgresqlfuncts_file}")

        # Ensure VOLTTRON source is on the path
        volttron_src = str(volttron_path)
        if volttron_src not in sys.path:
            sys.path.insert(0, volttron_src)

        try:
            # Ensure the dbutils package and basedb are loaded first (postgresqlfuncts depends on them)
            # Import the parent dbutils shim to bootstrap basedb and the package structure
            if "volttron.platform.dbutils" not in sys.modules:
                import aems.compat.shims.dbutils  # noqa: F401

            if "volttron.platform.dbutils.basedb" not in sys.modules:
                basedb_file = dbutils_dir / "basedb.py"
                loader = importlib.machinery.SourceFileLoader(
                    "volttron.platform.dbutils.basedb", str(basedb_file)
                )
                spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(basedb_file))
                basedb_mod = importlib.util.module_from_spec(spec)
                sys.modules["volttron.platform.dbutils.basedb"] = basedb_mod
                loader.exec_module(basedb_mod)

            # Load postgresqlfuncts itself
            full_module_name = "volttron.platform.dbutils.postgresqlfuncts"
            if full_module_name not in sys.modules:
                loader = importlib.machinery.SourceFileLoader(
                    full_module_name, str(postgresqlfuncts_file)
                )
                spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(postgresqlfuncts_file))
                pg_module = importlib.util.module_from_spec(spec)
                sys.modules[full_module_name] = pg_module
                loader.exec_module(pg_module)
            else:
                pg_module = sys.modules[full_module_name]

            # Extract the class for convenience
            PostgreSqlFuncts = pg_module.PostgreSqlFuncts
            _log.info("Successfully loaded postgresqlfuncts from VOLTTRON source")
            break

        except Exception as e:
            _log.warning(f"Failed to import postgresqlfuncts from {volttron_path}: {e}")
            import traceback
            _log.debug(f"Traceback: {traceback.format_exc()}")
            continue

if PostgreSqlFuncts is None:
    _log.error(
        f"Could not find postgresqlfuncts module. Searched paths: "
        f"{[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
    )

    class PostgreSqlFuncts:
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PostgreSqlFuncts requires VOLTTRON source code and psycopg2. "
                f"Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
            )


__all__ = ["PostgreSqlFuncts"]
