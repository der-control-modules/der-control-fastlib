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

    DATE = "Date"
    TIMESTAMP = "TimeStamp"
    SYNC_TIMESTAMP = "SynchronizedTimeStamp"
    CONTENT_TYPE = "Content-Type"
    FROM = "From"
    TO = "To"
    REQUESTER_ID = "requesterID"
    COOKIE = "Cookie"


# VOLTTRON messaging topics
class topics:
    """VOLTTRON topic name constants and utilities."""

    # Driver topics
    DRIVER_TOPIC_BASE = "devices"
    DRIVER_TOPIC_ALL = "all"

    # Topic templates (these are callable Topic objects)
    # DEVICES_PATH includes base and node for full flexibility
    # Format: {base}/{node}/{campus}/{building}/{unit}/{path}/{point}
    DEVICES_PATH = Topic("{base}//{node}//{campus}//{building}//{unit}//{path!S}//{point}")

    # DEVICES_VALUE is the standard topic with "devices" prefix and no node
    # Format: devices/{campus}/{building}/{unit}/{path}/{point}
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

    # BACnet topics
    BACNET_I_AM = "bacnet/i_am"

    # Logger topics (for historian agents)
    LOGGER_BASE = "datalogger"
    LOGGER = "datalogger/{subtopic}"
    LOGGER_LOG = "datalogger/log"
    LOGGER_STATUS = "datalogger/status"

    # Record topics (for historian agents)
    RECORD_BASE = "record"
    RECORD = "record/{subtopic}"

    # Analysis topics
    ANALYSIS_TOPIC_BASE = "analysis"
    ANALYSIS_VALUE = Topic("analysis//{analysis_name}//{campus}//{building}//{unit}//{point}")

    # Alert topics
    ALERTS = "alerts/{agent_class}/{agent_identity}"

    # Heartbeat topics
    HEARTBEAT = "heartbeats"

    # Platform topics
    PLATFORM_BASE = "platform"
    PLATFORM_SEND_EMAIL = "platform/send_email"
    PLATFORM = "platform/{subtopic}"
    PLATFORM_SHUTDOWN = "platform/shutdown"
    PLATFORM_VCP_DEVICES = "platforms/{platform_uuid}/devices/{topic}"

    # Market topics
    MARKET_BASE = "market/{subtopic}"
    MARKET_RESERVE = "market/reserve"
    MARKET_BID = "market/bid"
    MARKET_CLEAR = "market/cleared_price"
    MARKET_AGGREGATE = "market/aggregate"
    MARKET_ERROR = "market/error"
    MARKET_RECORD = "record/market/cleared_price"

    # Agent topics
    AGENT_SHUTDOWN = "agent/{agent}/shutdown"
    AGENT_PING = "agent/ping"


# Export topics at module level for direct import (from volttron.platform.messaging.topics import DEVICES_VALUE)
DRIVER_TOPIC_BASE = topics.DRIVER_TOPIC_BASE
DRIVER_TOPIC_ALL = topics.DRIVER_TOPIC_ALL
DEVICES_PATH = topics.DEVICES_PATH
DEVICES_VALUE = topics.DEVICES_VALUE


__all__ = [
    "STATUS_GOOD",
    "STATUS_BAD",
    "UNKNOWN",
    "headers",
    "topics",
    "DRIVER_TOPIC_BASE",
    "DRIVER_TOPIC_ALL",
    "DEVICES_PATH",
    "DEVICES_VALUE",
]
