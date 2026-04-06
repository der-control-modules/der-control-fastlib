"""
Import hook system to redirect VOLTTRON imports to AEMS equivalents.

This allows running VOLTTRON agents without modifying their code.
"""

import importlib
import logging
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType

_log = logging.getLogger(__name__)


class VolttronImportRedirector(MetaPathFinder, Loader):
    """
    Import hook that redirects VOLTTRON module imports to AEMS equivalents.

    When a VOLTTRON agent tries to import from volttron.platform.*, this
    redirector intercepts the import and provides AEMS-compatible modules.
    """

    # Mapping of VOLTTRON modules to AEMS equivalents
    REDIRECT_MAP = {
        # Core agent imports
        "volttron.platform.vip.agent.errors": "aems.compat.shims.vip_agent_errors",
        "volttron.platform.vip.agent": "aems.compat.shims.vip_agent",
        "volttron.platform.agent.base_historian": "aems.compat.shims.base_historian",
        "volttron.platform.agent.base_weather": "aems.compat.shims.base_weather",
        "volttron.platform.agent.math_utils": "aems.compat.shims.math_utils",
        "volttron.platform.agent.known_identities": "aems.compat.shims.known_identities",
        "volttron.platform.agent": "aems.compat.shims.platform_agent",
        # Database utilities
        "volttron.platform.dbutils": "aems.compat.shims.dbutils",
        "volttron.platform.dbutils.basedb": "aems.compat.shims.dbutils_basedb",
        "volttron.platform.dbutils.sqlutils": "aems.compat.shims.dbutils_sqlutils",
        "volttron.platform.dbutils.postgresqlfuncts": "aems.compat.shims.dbutils_postgresqlfuncts",
        # Messaging and health
        "volttron.platform.messaging.headers": "aems.compat.shims.messaging_headers",
        "volttron.platform.messaging.health": "aems.compat.shims.health",
        "volttron.platform.messaging": "aems.compat.shims.messaging",
        # Subsystems
        "volttron.platform.vip.agent.subsystems.query": "aems.compat.shims.query",
        "volttron.platform.vip.agent.subsystems.heartbeat": "aems.compat.shims.heartbeat",
        "volttron.platform.vip.agent.subsystems": "aems.compat.shims.subsystems",
        # VIP base
        "volttron.platform.vip": "aems.compat.shims.vip",
        # Platform base
        "volttron.platform.jsonapi": "aems.compat.shims.jsonapi",
        "volttron.platform": "aems.compat.shims.platform",
        # Utils
        "volttron.utils.docs": "aems.compat.shims.utils_docs",
        "volttron.utils": "aems.compat.shims.utils",
    }

    def __init__(self):
        self._loaded_modules = {}

    def find_spec(self, fullname, path, target=None):
        """
        Find module spec for VOLTTRON imports.

        This is called by Python's import system when trying to import a module.
        """
        # Check if this is a VOLTTRON import we should redirect
        if fullname.startswith("volttron."):
            # Try exact match first
            if fullname in self.REDIRECT_MAP:
                _log.debug(f"Redirecting import: {fullname} -> {self.REDIRECT_MAP[fullname]}")
                return ModuleSpec(fullname, self, origin="aems-compat")

            # Try parent modules (for submodule imports)
            parts = fullname.split(".")
            for i in range(len(parts), 0, -1):
                parent = ".".join(parts[:i])
                if parent in self.REDIRECT_MAP:
                    _log.debug(f"Redirecting import: {fullname} -> {self.REDIRECT_MAP[parent]} (via parent {parent})")
                    return ModuleSpec(fullname, self, origin="aems-compat")

        return None  # Let other finders handle it

    def create_module(self, spec):
        """Create the module - return None to use default module creation."""
        return None

    def exec_module(self, module):
        """
        Execute the module by loading the AEMS equivalent.

        This replaces the module's contents with the AEMS compatibility shim.
        """
        fullname = module.__name__

        # Find the redirect target
        redirect_to = None
        if fullname in self.REDIRECT_MAP:
            redirect_to = self.REDIRECT_MAP[fullname]
        else:
            # Check parent modules
            parts = fullname.split(".")
            for i in range(len(parts), 0, -1):
                parent = ".".join(parts[:i])
                if parent in self.REDIRECT_MAP:
                    redirect_to = self.REDIRECT_MAP[parent]
                    break

        if redirect_to:
            try:
                # Import the AEMS compatibility module
                aems_module = importlib.import_module(redirect_to)

                # Copy all attributes from AEMS module to the VOLTTRON module
                for attr in dir(aems_module):
                    if not attr.startswith("_") or attr in ("__path__", "__package__"):
                        setattr(module, attr, getattr(aems_module, attr))

                # Mark as successfully loaded
                module.__file__ = getattr(aems_module, "__file__", "<aems-compat>")
                module.__loader__ = self

                # IMPORTANT: Mark as a package so sub-imports work
                # This allows "from volttron.platform.agent import X" to work
                if not hasattr(module, "__path__"):
                    module.__path__ = []

                # Set package name correctly for relative imports
                if "." in fullname:
                    module.__package__ = fullname.rpartition(".")[0]
                else:
                    module.__package__ = fullname

                _log.info(f"Successfully redirected {fullname} to {redirect_to}")

            except ImportError as e:
                _log.error(f"Failed to load AEMS compatibility module {redirect_to}: {e}")
                raise ImportError(f"AEMS compatibility module not found: {redirect_to}") from e
        else:
            raise ImportError(f"No AEMS equivalent found for {fullname}")


# Global redirector instance
_redirector = None


def install_volttron_compatibility():
    """
    Install the VOLTTRON import redirector.

    This must be called before importing any VOLTTRON agent code.
    After calling this, imports like:
        from volttron.platform.vip.agent import Agent

    Will be redirected to AEMS equivalents.
    """
    global _redirector

    if _redirector is None:
        _redirector = VolttronImportRedirector()
        # Insert at the beginning of meta_path so we intercept imports first
        sys.meta_path.insert(0, _redirector)

        # Pre-create the top-level volttron package to avoid issues
        # This ensures "volttron" exists before "volttron.platform" is imported
        if "volttron" not in sys.modules:
            volttron_module = ModuleType("volttron")
            volttron_module.__path__ = []
            volttron_module.__package__ = "volttron"
            volttron_module.__file__ = "<aems-compat>"
            sys.modules["volttron"] = volttron_module
            _log.debug("Created volttron top-level package")

        _log.info("VOLTTRON compatibility layer installed")
    else:
        _log.debug("VOLTTRON compatibility layer already installed")


def uninstall_volttron_compatibility():
    """Remove the VOLTTRON import redirector."""
    global _redirector

    if _redirector is not None:
        try:
            sys.meta_path.remove(_redirector)
            _redirector = None
            _log.info("VOLTTRON compatibility layer uninstalled")
        except ValueError:
            _log.warning("VOLTTRON compatibility layer was not in sys.meta_path")


def is_installed():
    """Check if the VOLTTRON compatibility layer is installed."""
    return _redirector is not None
