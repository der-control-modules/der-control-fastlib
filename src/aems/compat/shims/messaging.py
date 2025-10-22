"""
VOLTTRON messaging compatibility shim.

Provides: messaging module namespace with topics and headers
"""

# Re-export health constants explicitly
from aems.compat.shims.health import STATUS_BAD, STATUS_GOOD, UNKNOWN
from aems.compat.shims.messaging_utils import Topic


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

    # Topic templates (these are callable Topic objects)
    # Format: devices/{campus}/{building}/{unit}/{path}/{point}
    DEVICES_PATH = Topic("devices//{campus}//{building}//{unit}//{path!S}//{point}")
    DEVICES_VALUE = Topic("devices//{campus}//{building}//{unit}//{path!S}//{point}")

    # RPC device path (for actuator agent RPC calls, no 'devices' prefix)
    # Format: {campus}/{building}/{unit}/{path}/{point}
    RPC_DEVICE_PATH = Topic("{campus}//{building}//{unit}//{path!S}//{point}")

    # Actuator topics
    ACTUATOR_GET = "devices/actuators/get/{campus}/{building}/{unit}/{path}/{point}"
    ACTUATOR_SET = "devices/actuators/set/{campus}/{building}/{unit}/{path}/{point}"
    ACTUATOR_VALUE = "devices/actuators/value/{campus}/{building}/{unit}/{path}/{point}"
    ACTUATOR_REVERT_POINT = "devices/actuators/revert/point/{campus}/{building}/{unit}/{path}/{point}"
    ACTUATOR_REVERT_DEVICE = "devices/actuators/revert/device/{campus}/{building}/{unit}/{path}/{point}"
    ACTUATOR_SCHEDULE_REQUEST = "devices/actuators/schedule/request"
    ACTUATOR_SCHEDULE_RESULT = "devices/actuators/schedule/result"

    # Platform topics
    PLATFORM_SEND = "platform/send"


__all__ = ["STATUS_GOOD", "STATUS_BAD", "UNKNOWN", "headers", "topics"]
