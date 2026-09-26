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
        "volttron.platform.vip.agent.errors": "derhost.compat.shims.vip_agent_errors",
        "volttron.platform.vip.agent": "derhost.compat.shims.vip_agent",
        "volttron.platform.agent.base_historian": "derhost.compat.shims.base_historian",
        "volttron.platform.agent.base_weather": "derhost.compat.shims.base_weather",
        "volttron.platform.agent.math_utils": "derhost.compat.shims.math_utils",
        "volttron.platform.agent.known_identities": "derhost.compat.shims.known_identities",
        "volttron.platform.agent": "derhost.compat.shims.platform_agent",
        # Database utilities
        "volttron.platform.dbutils": "derhost.compat.shims.dbutils",
        # Messaging and health
        "volttron.platform.messaging.headers": "derhost.compat.shims.messaging_headers",
        "volttron.platform.messaging.health": "derhost.compat.shims.health",
        "volttron.platform.messaging": "derhost.compat.shims.messaging",
        # Subsystems
        "volttron.platform.vip.agent.subsystems.query": "derhost.compat.shims.query",
        "volttron.platform.vip.agent.subsystems.heartbeat": "derhost.compat.shims.heartbeat",
        "volttron.platform.vip.agent.subsystems": "derhost.compat.shims.subsystems",
        # VIP base
        "volttron.platform.vip": "derhost.compat.shims.vip",
        # Platform base
        "volttron.platform.jsonapi": "derhost.compat.shims.jsonapi",
        "volttron.platform.scheduling": "derhost.compat.shims.scheduling",
        "volttron.platform": "derhost.compat.shims.platform",
        # Utils
        "volttron.utils.docs": "derhost.compat.shims.utils_docs",
        "volttron.utils": "derhost.compat.shims.utils",
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
                target_module = importlib.import_module(redirect_to)

                # Copy all attributes from AEMS module to the VOLTTRON module
                for attr in dir(target_module):
                    if not attr.startswith("_") or attr in ("__path__", "__package__"):
                        setattr(module, attr, getattr(target_module, attr))

                # Mark as successfully loaded
                module.__file__ = getattr(target_module, "__file__", "<aems-compat>")
                module.__loader__ = self

                # Mark as a package only when the map has a deeper entry under
                # this name (e.g. "volttron.platform.agent" has
                # ".base_historian"), so "from volttron.platform.agent import X"
                # keeps working. A leaf entry (no deeper map key) is left
                # without __path__: with it, "from <leaf> import anything"
                # falls back to importing "<leaf>.anything" as a submodule,
                # which this redirector resolves via the same parent-match
                # rule and so always "succeeds", masking a genuinely unknown
                # name behind a fabricated module instead of ImportError.
                if not hasattr(module, "__path__") and any(
                    key != fullname and key.startswith(fullname + ".") for key in self.REDIRECT_MAP
                ):
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


def _install_bacpypes_compat():
    """
    Install bacpypes compatibility shims for version differences.

    bacpypes 0.19 moved LocalDeviceObject from bacpypes.service.device
    to bacpypes.local.device. This shim ensures the old import path works
    regardless of which bacpypes version is installed, so VOLTTRON's BACnet
    proxy agent can use `from bacpypes.service.device import LocalDeviceObject`.
    """
    # Only shim if the old import path doesn't work natively
    try:
        importlib.import_module("bacpypes.service.device")
        _log.debug("bacpypes.service.device exists natively, no shim needed")
        return
    except ImportError:
        pass

    # Try to load from the new location (bacpypes 0.19+)
    try:
        local_device_mod = importlib.import_module("bacpypes.local.device")
    except ImportError:
        _log.debug("Neither bacpypes.service.device nor bacpypes.local.device found, skipping shim")
        return

    # Create fake bacpypes.service package if it doesn't exist
    if "bacpypes.service" not in sys.modules:
        bacpypes_service = ModuleType("bacpypes.service")
        bacpypes_service.__path__ = []
        bacpypes_service.__package__ = "bacpypes.service"
        bacpypes_service.__file__ = "<bacpypes-compat>"
        sys.modules["bacpypes.service"] = bacpypes_service
        _log.debug("Created bacpypes.service shim package")

    # Create fake bacpypes.service.device module with LocalDeviceObject
    bacpypes_service_device = ModuleType("bacpypes.service.device")
    bacpypes_service_device.__package__ = "bacpypes.service"
    bacpypes_service_device.__file__ = "<bacpypes-compat>"
    bacpypes_service_device.LocalDeviceObject = local_device_mod.LocalDeviceObject
    sys.modules["bacpypes.service.device"] = bacpypes_service_device
    _log.info("Installed bacpypes compat shim: bacpypes.service.device -> bacpypes.local.device")


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

        # Install bacpypes version compatibility shims
        _install_bacpypes_compat()

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
