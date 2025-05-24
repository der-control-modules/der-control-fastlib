# listener_agent.py

from agent import Agent, Core
import gevent
from gevent.event import AsyncResult
import json
import datetime


class ListenerAgent(Agent):
    """
    A simple agent that subscribes to all topics and logs the messages it receives.
    """
    
    def __init__(self, identity="listener", **kwargs):
        # Configure topic subscriptions
        self.default_config = {
            "subscribe_patterns": [""],    # Empty string subscribes to everything
            "ignore_patterns": []          # Patterns to ignore (e.g., for testing)
        }
        
        # Set up the configuration
        self._config = self.default_config.copy()
        
        # Initialize the agent after setting up our attributes
        super().__init__(identity=identity, **kwargs)
    
    @Core.receiver('onstart')
    def _onstart(self, sender=None, **kwargs):
        """Handle startup tasks for the Listener agent."""
        print(f"{self.identity} agent starting...")
        print(f"  Sender: {sender}")
        print(f"  Additional parameters: {kwargs}")
        
        # Set up subscriptions
        for pattern in self._config["subscribe_patterns"]:
            print(f"Subscribing to pattern: '{pattern}' (empty string means all topics)")
            self.vip.pubsub.subscribe(pattern, self._on_message)
        
        # Start listening
        print(f"{self.identity} agent started!")
    
    @Core.receiver('onstop')
    def _onstop(self, sender=None, **kwargs):
        """Handle shutdown tasks for the Listener agent."""
        print(f"{self.identity} agent stopping...")
        print(f"  Sender: {sender}")
        print(f"  Additional parameters: {kwargs}")
    
    def _on_message(self, peer, sender, bus, topic, headers, message):
        """Handle incoming pub/sub messages using original VOLTTRON callback style."""
        # Check if this is a topic we should ignore
        for ignore in self._config["ignore_patterns"]:
            if topic.startswith(ignore):
                print(f"Ignoring message on topic {topic} (matched ignore pattern)")
                return
        
        # Log the message - this is the primary function of the Listener agent
        timestamp = datetime.datetime.now().isoformat()
        print(f"[{timestamp}] Received topic: {topic} from {sender}")
        print(f"  Bus: {bus}")
        print(f"  Headers: {headers}")
        print(f"  Message: {message}")
    
    def reconfigure(self, config):
        """Update agent configuration."""
        if not config:
            # No configuration specified, use the default
            config = self.default_config.copy()
        
        self._config.update(config)
        
        # Return an AsyncResult for API consistency
        async_result = AsyncResult()
        async_result.set(True)
        return async_result
    
    # Add an RPC method to update configuration
    def update_config(self, config_name, value):
        """RPC method to update configuration at runtime."""
        if config_name in self._config:
            old_value = self._config[config_name]
            self._config[config_name] = value
            print(f"Updated config {config_name} from {old_value} to {value}")
            return f"Updated {config_name}"
        else:
            return f"Unknown config parameter: {config_name}"


if __name__ == "__main__":
    # Create and run the listener agent
    listener = ListenerAgent()
    
    # Export RPC methods
    listener.vip.rpc.export("update_config", listener.update_config)
    listener.vip.rpc.export("reconfigure", listener.reconfigure)
    listener.vip.rpc.export("ping", lambda: "pong from listener")
    
    try:
        # Connect to the server
        print("Connecting listener agent to the platform...")
        listener.connect()
        
        # Keep the agent running
        print("Listener agent is running...")
        while True:
            gevent.sleep(10)  # Sleep to keep the agent alive
            
    except KeyboardInterrupt:
        print("Keyboard interrupt received, stopping agent...")
    finally:
        # Ensure proper shutdown
        if listener.core.stop().get():
            print("Listener agent stopped cleanly")
        else:
            print("Error stopping listener agent")