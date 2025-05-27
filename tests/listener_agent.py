import datetime
import sys

import gevent
from gevent.event import AsyncResult

from agent import Agent, Core, RPC

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

    @Core.periodic(10)
    def _publish_state(self):
        """Just publish something so that we know we can do that."""
        self.vip.pubsub.publish("holy/cow", "It Worked!")
    
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
    
    @RPC.export
    def reconfigure(self, config):
        """Update agent configuration."""
        if not config:
            # No configuration specified, use the default
            config = self.default_config.copy()
        
        self._config.update(config)
        
        # Return success
        return True
    
    @RPC.export
    def update_config(self, config_name, value):
        """RPC method to update configuration at runtime."""
        if config_name in self._config:
            old_value = self._config[config_name]
            self._config[config_name] = value
            print(f"Updated config {config_name} from {old_value} to {value}")
            return f"Updated {config_name}"
        else:
            return f"Unknown config parameter: {config_name}"
    
    @RPC.export(name="get_version")
    def version(self):
        """Return the version of the agent."""
        return "1.0.0"


if __name__ == "__main__":
    from agent import run_agent
    sys.exit(run_agent(ListenerAgent))