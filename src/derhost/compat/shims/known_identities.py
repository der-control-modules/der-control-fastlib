"""
VOLTTRON known agent identities compatibility shim.

Provides standard agent identity constants used throughout VOLTTRON.
"""

# Standard agent identities
PLATFORM_DRIVER = "platform.driver"
CONTROL = "control"
CONFIGURATION_STORE = "config.store"
PLATFORM_HEALTH = "platform.health"
PLATFORM_WEB = "platform.web"
VOLTTRON_CENTRAL = "volttron.central"
VOLTTRON_CENTRAL_PLATFORM = "volttron.central.platform"
PLATFORM_ALERTER = "platform.alerter"
PLATFORM_HISTORIAN = "platform.historian"
MASTER_WEB = "master.web"
KEY_DISCOVERY = "keydiscovery"
CONFIG_STORE = "config.store"

# Export all constants
__all__ = [
    "PLATFORM_DRIVER",
    "CONTROL",
    "CONFIGURATION_STORE",
    "PLATFORM_HEALTH",
    "PLATFORM_WEB",
    "VOLTTRON_CENTRAL",
    "VOLTTRON_CENTRAL_PLATFORM",
    "PLATFORM_ALERTER",
    "PLATFORM_HISTORIAN",
    "MASTER_WEB",
    "KEY_DISCOVERY",
    "CONFIG_STORE",
]
