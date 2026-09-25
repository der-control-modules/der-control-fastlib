"""
VOLTTRON Heartbeat subsystem compatibility shim.

Provides: Heartbeat subsystem for agent health monitoring
"""

import logging
import threading
import time

_log = logging.getLogger(__name__)


class Heartbeat:
    """
    VOLTTRON Heartbeat subsystem compatibility.

    In VOLTTRON, heartbeat publishes periodic status messages.
    This provides a compatible implementation using AEMS pubsub.
    """

    def __init__(self, owner):
        """
        Initialize Heartbeat subsystem.

        Args:
            owner: Agent instance
        """
        self._owner = owner
        self._period = 0
        self._running = False
        self._thread = None

    def start(self):
        """Start heartbeat with default period."""
        self.start_with_period(30)  # Default 30 seconds

    def start_with_period(self, period):
        """
        Start heartbeat with specified period.

        Args:
            period: Heartbeat period in seconds
        """
        if self._running:
            self.stop()

        self._period = period
        self._running = True

        # Start background thread for heartbeat
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

        _log.info(f"Heartbeat started with period {period}s")

    def stop(self):
        """Stop heartbeat."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        _log.info("Heartbeat stopped")

    def _heartbeat_loop(self):
        """Background loop that publishes heartbeat messages."""
        while self._running:
            try:
                # Publish heartbeat message
                topic = f"heartbeat/{self._owner.identity}"
                message = {"timestamp": time.time(), "identity": self._owner.identity}

                self._owner.vip.pubsub.publish("", topic, message)

                _log.debug(f"Heartbeat published to {topic}")

            except Exception as e:
                _log.error(f"Error publishing heartbeat: {e}")

            # Sleep for period
            time.sleep(self._period)


__all__ = ["Heartbeat"]
