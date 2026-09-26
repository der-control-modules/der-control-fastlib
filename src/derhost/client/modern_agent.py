# modern_agent.py

import datetime
import logging

import gevent
from agent import Agent

from derhost._redact import redact_secrets

_log = logging.getLogger(__name__)


class ModernAgent(Agent):
    """
    An example agent that uses the modern callback style with a single message parameter.
    """

    def __init__(self, identity="modern", **kwargs):
        super().__init__(identity=identity, **kwargs)
        self.core.onstart(self._onstart)

    def _onstart(self):
        """Handle startup tasks for the agent."""
        _log.info(f"{self.identity} agent starting...")

        # Subscribe with the modern-style callback
        self.vip.pubsub.subscribe("test/", self._on_message)
        _log.info(f"{self.identity} agent started!")

    # This method uses the modern callback style with a single message parameter
    def _on_message(self, message):
        """Handle incoming pub/sub messages with modern callback style."""
        # Extract data fields from the message
        topic = message.get("topic", "unknown")
        data = message.get("message", {})
        sender = message.get("sender", "unknown")

        # Log the message
        timestamp = datetime.datetime.now().isoformat()
        _log.info(f"[MODERN] [{timestamp}] {self.identity} received: {topic} from {sender}")
        _log.info(f"  Data: {redact_secrets(data)}")


if __name__ == "__main__":
    # Create and run the modern agent
    agent = ModernAgent()

    try:
        # Connect to the server
        agent.connect()
        _log.info(f"{agent.identity} agent running. Press Ctrl+C to exit.")

        # Keep the agent running
        while True:
            gevent.sleep(1)
    except KeyboardInterrupt:
        _log.info("\nShutting down...")
    finally:
        agent.core.stop().get()
