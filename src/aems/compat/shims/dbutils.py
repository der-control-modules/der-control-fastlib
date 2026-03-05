"""
Shim for volttron.platform.dbutils module.

This imports database utilities from the actual VOLTTRON source code.
This module acts as a package to allow submodule imports like volttron.platform.dbutils.basedb
"""

import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Mark this as a package
__path__ = []

# Try to import from actual VOLTTRON installation
sqlutils = None
basedb = None

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
    dbutils_dir = volttron_path / "volttron" / "platform" / "dbutils"

    if dbutils_dir.exists():
        _log.debug(f"Found VOLTTRON dbutils at: {dbutils_dir}")

        # Add VOLTTRON source to path
        volttron_src = str(volttron_path)
        if volttron_src not in sys.path:
            sys.path.insert(0, volttron_src)
            _log.debug(f"Added VOLTTRON source to path: {volttron_src}")

        try:
            # Import using importlib to bypass the import hook (avoid circular dependency)
            import importlib.machinery
            import importlib.util
            from types import ModuleType

            # Create a fake volttron.platform.dbutils package so submodule imports work
            # This allows "from volttron.platform.dbutils.basedb import ..." to work
            fake_dbutils_pkg = ModuleType("volttron.platform.dbutils")
            fake_dbutils_pkg.__path__ = [str(dbutils_dir)]
            fake_dbutils_pkg.__file__ = str(dbutils_dir / "__init__.py")
            fake_dbutils_pkg.__package__ = "volttron.platform.dbutils"
            sys.modules["volttron.platform.dbutils"] = fake_dbutils_pkg

            # Import basedb first (sqlutils depends on it)
            basedb_file = dbutils_dir / "basedb.py"
            loader = importlib.machinery.SourceFileLoader("volttron.platform.dbutils.basedb", str(basedb_file))
            spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(basedb_file))
            basedb = importlib.util.module_from_spec(spec)
            sys.modules["volttron.platform.dbutils.basedb"] = basedb
            loader.exec_module(basedb)

            # Import sqlutils
            sqlutils_file = dbutils_dir / "sqlutils.py"
            loader = importlib.machinery.SourceFileLoader("volttron.platform.dbutils.sqlutils", str(sqlutils_file))
            spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(sqlutils_file))
            sqlutils = importlib.util.module_from_spec(spec)
            sys.modules["volttron.platform.dbutils.sqlutils"] = sqlutils
            loader.exec_module(sqlutils)

            # Import database-specific modules (sqlitefuncts, mysqlfuncts, etc.)
            # These are loaded dynamically by sqlutils.get_dbfuncts_class()
            loaded_count = 0
            for db_module_file in dbutils_dir.glob("*functs.py"):
                module_name = db_module_file.stem
                full_module_name = f"volttron.platform.dbutils.{module_name}"

                try:
                    loader = importlib.machinery.SourceFileLoader(full_module_name, str(db_module_file))
                    spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(db_module_file))
                    db_module = importlib.util.module_from_spec(spec)
                    sys.modules[full_module_name] = db_module
                    loader.exec_module(db_module)

                    # Add to fake package
                    setattr(fake_dbutils_pkg, module_name, db_module)
                    _log.debug(f"Loaded {module_name}")
                    loaded_count += 1
                except Exception as module_error:
                    # Skip modules that have missing dependencies (e.g., psycopg2 for postgresql)
                    _log.debug(f"Skipping {module_name}: {module_error}")

            if loaded_count == 0:
                raise Exception("Failed to load any database functs modules")

            # Add the main modules to the fake package
            fake_dbutils_pkg.basedb = basedb
            fake_dbutils_pkg.sqlutils = sqlutils

            _log.info("Successfully loaded dbutils modules from VOLTTRON source")
            break
        except Exception as e:
            _log.warning(f"Failed to import dbutils from {volttron_path}: {e}")
            import traceback

            _log.debug(f"Traceback: {traceback.format_exc()}")
            continue

# If we couldn't import from VOLTTRON source, provide a helpful error
if sqlutils is None:
    _log.error(f"Could not find VOLTTRON dbutils modules. Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}")

    # Create a placeholder module
    class sqlutils:
        @staticmethod
        def get_dbfuncts_class(database_type):
            raise ImportError(
                f"sqlutils.get_dbfuncts_class() requires VOLTTRON source code. "
                f"Searched paths: {[str(p) for p in VOLTTRON_SEARCH_PATHS]}"
            )

    class basedb:
        pass


# Export modules
__all__ = ["sqlutils", "basedb"]
