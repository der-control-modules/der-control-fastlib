# modern_agent.py

from agent import Agent
import gevent
from gevent.event import AsyncResult
import datetime


class ModernAgent(Agent):
    """
    An example agent that uses the modern callback style with a single message parameter.
    """

    def __init__(self, identity="modern", **kwargs):
        super().__init__(identity=identity, **kwargs)
        self.core.onstart(self._onstart)

    def _onstart(self):
        """Handle startup tasks for the agent."""
        print(f"{self.identity} agent starting...")

        # Subscribe with the modern-style callback
        self.vip.pubsub.subscribe("test/", self._on_message)
        print(f"{self.identity} agent started!")

    # This method uses the modern callback style with a single message parameter
    def _on_message(self, message):
        """Handle incoming pub/sub messages with modern callback style."""
        # Extract data fields from the message
        topic = message.get("topic", "unknown")
        data = message.get("message", {})
        sender = message.get("sender", "unknown")

        # Log the message
        timestamp = datetime.datetime.now().isoformat()
        print(f"[MODERN] [{timestamp}] {self.identity} received: {topic} from {sender}")
        print(f"  Data: {data}")


if __name__ == "__main__":
    # Create and run the modern agent
    agent = ModernAgent()

    try:
        # Connect to the server
        agent.connect()
        print(f"{agent.identity} agent running. Press Ctrl+C to exit.")

        # Keep the agent running
        while True:
            gevent.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        agent.core.stop().get()
