"""
VOLTTRON health subsystem compatibility shim.

Provides: STATUS_GOOD, STATUS_BAD, health status constants, Status class
"""

import json
from datetime import datetime

import pytz


def get_aware_utc_now():
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(pytz.UTC)


def format_timestamp(timestamp):
    """Format timestamp to ISO 8601 string."""
    if isinstance(timestamp, str):
        return timestamp
    return timestamp.isoformat()


# Health status constants (VOLTTRON compatibility)
STATUS_GOOD = "GOOD"
STATUS_BAD = "BAD"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_STARTING = "STARTING"
STATUS_STOPPING = "STOPPING"

# Aliases
UNKNOWN = STATUS_UNKNOWN
GOOD_STATUS = STATUS_GOOD
BAD_STATUS = STATUS_BAD
UNKNOWN_STATUS = STATUS_UNKNOWN
STARTING_STATUS = STATUS_STARTING

# Acceptable status values
ACCEPTABLE_STATUS = (GOOD_STATUS, BAD_STATUS, UNKNOWN_STATUS, STARTING_STATUS)


class Status:
    """
    The `Status` object wraps the context status and last reported into a
    small object that can be serialized and sent across the message bus.

    There are two static methods for constructing `Status` objects:
      - from_json() Expects a json string as input.
      - build() Expects at least a status in the `ACCEPTABLE_STATUS` tuple.

    The build() method also takes a context and a callback function that will
    be called when the status changes.
    """

    def __init__(self):
        self._status = GOOD_STATUS
        self._context = None
        self._last_updated = format_timestamp(get_aware_utc_now())
        self._status_changed_callback = None

    @property
    def status(self):
        return self._status

    @property
    def context(self):
        if self._context:
            if isinstance(self._context, str):
                return self._context
            return self._context.copy()
        return None

    @property
    def last_updated(self):
        return self._last_updated

    def update_status(self, status, context=None):
        """
        Updates the internal state of the `Status` object.

        This method will throw errors if the context is not serializable or
        if the status parameter is not within the ACCEPTABLE_STATUS tuple.

        :param status: New status value (must be in ACCEPTABLE_STATUS)
        :param context: Optional context dictionary
        :return: None
        """
        if status not in ACCEPTABLE_STATUS:
            raise ValueError(f"Invalid status value {status}")
        try:
            json.dumps(context)
        except TypeError:
            raise ValueError("Context must be JSON serializable.")

        status_changed = status != self._status
        self._status = status
        self._context = context
        self._last_updated = format_timestamp(get_aware_utc_now())

        if status_changed and self._status_changed_callback:
            self._status_changed_callback()

    def as_dict(self):
        """
        Returns a copy of the status object properties as a dictionary.

        @return: Dictionary with status, context, and last_updated
        """
        return {"status": self.status, "context": self.context, "last_updated": self.last_updated}

    def as_json(self):
        """
        Serializes the object to a json string.

        Note:
            Does not serialize the change callback function.

        :return: JSON string representation
        """
        return json.dumps(self.as_dict())

    @staticmethod
    def from_json(data, status_changed_callback=None):
        """
        Deserializes a `Status` object and returns it to the caller.

        :param data: JSON string to deserialize
        :param status_changed_callback: Optional callback for status changes
        :return: Status object
        """
        statusobj = Status()
        cp = json.loads(data)
        cp["_status"] = cp["status"]
        cp["_last_updated"] = cp["last_updated"]
        cp["_context"] = cp["context"]
        del cp["status"]
        del cp["last_updated"]
        del cp["context"]
        statusobj.__dict__ = cp
        statusobj._status_changed_callback = status_changed_callback
        return statusobj

    @staticmethod
    def build(status, context=None, status_changed_callback=None):
        """
        Creates a new Status object with the given status and context.

        :param status: Initial status (must be in ACCEPTABLE_STATUS)
        :param context: Optional context dictionary
        :param status_changed_callback: Optional callback for status changes
        :return: Status object
        """
        statusobj = Status()
        statusobj._status_changed_callback = status_changed_callback
        statusobj.update_status(status, context)
        return statusobj


__all__ = [
    "STATUS_GOOD",
    "STATUS_BAD",
    "STATUS_UNKNOWN",
    "STATUS_STARTING",
    "STATUS_STOPPING",
    "UNKNOWN",
    "GOOD_STATUS",
    "BAD_STATUS",
    "UNKNOWN_STATUS",
    "STARTING_STATUS",
    "ACCEPTABLE_STATUS",
    "Status",
]
