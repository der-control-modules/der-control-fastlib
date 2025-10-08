"""
Health subsystem for VOLTTRON compatibility.

Provides health status management for agents.
"""

import logging

_log = logging.getLogger(__name__)

# Status constants
STATUS_GOOD = "GOOD"
STATUS_BAD = "BAD"


class Health:
    """
    VOLTTRON Health subsystem for agent health monitoring.

    In VOLTTRON, health tracks agent status and publishes it.
    """

    def __init__(self, owner):
        """
        Initialize Health subsystem.

        Args:
            owner: Agent instance
        """
        self._owner = owner
        self._status = STATUS_GOOD
        self._status_message = ""

    def set_status(self, status, message=""):
        """
        Set agent health status.

        Args:
            status: Health status (STATUS_GOOD, STATUS_BAD, etc.)
            message: Optional status message
        """
        self._status = status
        self._status_message = message

        # Publish health status
        topic = f"health/{self._owner.identity}"
        health_data = {"status": status, "message": message}

        try:
            self._owner.vip.pubsub.publish("", topic, health_data)
            _log.info(f"Health status set to {status}: {message}")
        except Exception as e:
            _log.error(f"Error publishing health status: {e}")

    def get_status(self):
        """
        Get current health status.

        Returns:
            dict: Health status and message
        """
        return {"status": self._status, "message": self._status_message}


__all__ = ["Health", "STATUS_GOOD", "STATUS_BAD"]
