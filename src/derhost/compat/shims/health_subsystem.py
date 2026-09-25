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

    def send_alert(self, alert_key, statusobj):
        """
        Send an alert with the given key and status object.

        Args:
            alert_key: Quasi-unique key for the alert
            statusobj: Status object with alert information
        """
        from derhost.compat.shims.health import Status
        from derhost.compat.shims.messaging import topics

        if not isinstance(statusobj, Status):
            raise ValueError("statusobj must be a Status object.")

        agent_class = self._owner.__class__.__name__
        identity = self._owner.identity
        # Replace '.' with '_' for compatibility with message bus routing
        topic_str = topics.ALERTS.format(agent_class=agent_class, agent_identity=identity.replace(".", "_"))
        headers = {"alert_key": alert_key}

        try:
            self._owner.vip.pubsub.publish("pubsub", topic=topic_str, headers=headers, message=statusobj.as_json()).get(
                timeout=10
            )
            _log.debug(f"Alert sent: {alert_key} to {topic_str}")
        except Exception as e:
            _log.error(f"Failed to send alert {alert_key}: {e}")


__all__ = ["Health", "STATUS_GOOD", "STATUS_BAD"]
