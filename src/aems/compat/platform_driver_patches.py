"""
Monkey patches for VOLTTRON Platform Driver compatibility.

These patches fix issues where BaseInterface doesn't store kwargs like device_path.
"""

import logging

_log = logging.getLogger(__name__)


def patch_base_interface():
    """
    Monkey patch BaseInterface to store kwargs like device_path.

    In the PNNL VOLTTRON tree, BaseInterface is expected to store kwargs
    passed during initialization, but the code doesn't explicitly do this.
    This patch ensures device_path and other kwargs are stored as instance attributes.
    """
    try:
        # Import the actual VOLTTRON BaseInterface
        from platform_driver.interfaces import BaseInterface

        # Store original __init__
        original_init = BaseInterface.__init__

        def patched_init(self, vip=None, core=None, **kwargs):
            """Patched __init__ that stores kwargs as instance attributes."""
            # Call original init
            original_init(self, vip=vip, core=core, **kwargs)

            # Store all kwargs as instance attributes
            for key, value in kwargs.items():
                setattr(self, key, value)
                _log.debug(f"BaseInterface: Set {key} = {value}")

        # Apply the patch
        BaseInterface.__init__ = patched_init
        _log.info("Successfully patched BaseInterface.__init__ to store kwargs")

    except ImportError as e:
        _log.debug(f"BaseInterface not found (not using platform driver): {e}")
    except Exception as e:
        _log.error(f"Failed to patch BaseInterface: {e}")


def apply_all_patches():
    """Apply all platform driver patches."""
    patch_base_interface()
