"""
VOLTTRON health subsystem compatibility shim.

Provides: STATUS_GOOD, STATUS_BAD, health status constants
"""

# Health status constants (VOLTTRON compatibility)
STATUS_GOOD = "GOOD"
STATUS_BAD = "BAD"
UNKNOWN = "UNKNOWN"

# Additional status values
STATUS_STARTING = "STARTING"
STATUS_STOPPING = "STOPPING"

__all__ = ["STATUS_GOOD", "STATUS_BAD", "UNKNOWN", "STATUS_STARTING", "STATUS_STOPPING"]
