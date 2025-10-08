"""
VOLTTRON messaging compatibility shim.

Provides: messaging module namespace with topics and headers
"""

# Re-export health constants explicitly
from aems.compat.shims.health import STATUS_BAD, STATUS_GOOD, UNKNOWN


# VOLTTRON messaging headers
class headers:
    """VOLTTRON message headers constants."""

    TIMESTAMP = "TimeStamp"
    DATE = "Date"
    CONTENT_TYPE = "Content-Type"
    REQUESTER_ID = "requesterID"


# VOLTTRON messaging topics
class topics:
    """VOLTTRON topic name constants and utilities."""

    # Driver topics
    DRIVER_TOPIC_BASE = "devices"
    DRIVER_TOPIC_ALL = "devices/#"

    @staticmethod
    def DEVICES_VALUE(device, point=""):
        """Build a device value topic path."""
        return f"devices/{device}/{point}"

    @staticmethod
    def DEVICES_PATH(device):
        """Build a device path topic."""
        return f"devices/{device}"

    # Actuator topics
    ACTUATOR_GET = "platform/actuator/get"
    ACTUATOR_SET = "platform/actuator/set"
    ACTUATOR_SCHEDULE_REQUEST = "platform/actuator/schedule/request"
    ACTUATOR_SCHEDULE_RESULT = "platform/actuator/schedule/result"

    # Platform topics
    PLATFORM_SEND = "platform/send"


__all__ = ["STATUS_GOOD", "STATUS_BAD", "UNKNOWN", "headers", "topics"]
