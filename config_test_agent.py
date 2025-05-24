# config_test_agent.py

from agent import Agent, Core, RPC, AsyncResult
import gevent
import datetime
import json


class ConfigTestAgent(Agent):
    """
    An agent that demonstrates the ConfigStore functionality.
    """
    
    def __init__(self, identity="config_test", **kwargs):
        # First initialize the base Agent class
        super().__init__(identity=identity, **kwargs)
        
        # Then initialize our own attributes
        self.default_config = {
            "interval": 60,
            "threshold": 100,
            "enabled": True,
            "targets": ["device1", "device2"],
            "nested": {
                "setting1": "value1",
                "setting2": "value2"
            }
        }
        
        # Current active configuration
        self._config = self.default_config.copy()
        
        # Last time the configuration was updated
        self._last_update = None
        
        # Status information
        self._status = {
            "startup_time": datetime.datetime.now().isoformat(),
            "config_updates": 0,
            "config_errors": 0,
            "last_update": None
        }
        
        # Processing loop greenlet
        self._processing_greenlet = None
        
        # Initialize the agent
        super().__init__(identity=identity, **kwargs)
    
    @Core.receiver('onstart')
    def _onstart(self, sender=None, **kwargs):
        """Handle startup tasks."""
        print(f"{self.identity} agent starting...")
        
        # Try to load configuration from config store
        self._load_config()
        
        # Watch for configuration changes
        self.config.watch("config", self._config_updated)
        
        # Start the processing loop
        self._processing_greenlet = gevent.spawn(self._processing_loop)
        
        print(f"{self.identity} agent started!")
    
    @Core.receiver('onstop')
    def _onstop(self, sender=None, **kwargs):
        """Handle shutdown tasks."""
        print(f"{self.identity} agent stopping...")
        
        # Stop the processing loop
        if self._processing_greenlet:
            self._processing_greenlet.kill()
        
        # Stop watching for configuration changes
        self.config.unwatch("config")
    
    def _load_config(self):
        """Load configuration from config store."""
        try:
            # Try to get the configuration from the config store
            config_future = self.config.get("config")
            config = config_future.get(timeout=5)
            
            if config:
                print(f"Loaded configuration from config store: {config}")
                self._apply_config(config)
            else:
                print("No configuration found in config store, using defaults")
                # Store the default configuration
                self.config.set("config", self.default_config).get(timeout=5)
                print("Default configuration stored in config store")
        except Exception as e:
            print(f"Error loading configuration: {e}")
            self._status["config_errors"] += 1
    
    def _config_updated(self, config_name, config_data):
        """Handle configuration updates."""
        if config_name == "config":
            if config_data is None:
                print("Configuration was deleted, reverting to defaults")
                self._apply_config(self.default_config.copy())
                # Re-store the default configuration
                self.config.set("config", self.default_config).get(timeout=5)
            else:
                print(f"Configuration updated: {config_data}")
                self._apply_config(config_data)
    
    def _apply_config(self, config):
        """Apply configuration changes."""
        try:
            # Make sure all required fields are present
            required_fields = ["interval", "threshold", "enabled", "targets"]
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required field: {field}")
            
            # Update the configuration
            self._config = config
            self._last_update = datetime.datetime.now().isoformat()
            self._status["config_updates"] += 1
            self._status["last_update"] = self._last_update
            
            print(f"Applied new configuration: {self._config}")
        except Exception as e:
            print(f"Error applying configuration: {e}")
            self._status["config_errors"] += 1
    
    def _processing_loop(self):
        """Simulate periodic processing based on configuration."""
        while True:
            if self._config["enabled"]:
                # Only process if enabled
                current_time = datetime.datetime.now().isoformat()
                targets = self._config["targets"]
                
                print(f"[{current_time}] Processing {len(targets)} targets with threshold {self._config['threshold']}")
                for target in targets:
                    print(f"  - Processing target: {target}")
            
            # Sleep for the configured interval
            interval = self._config["interval"]
            print(f"Sleeping for {interval} seconds...")
            gevent.sleep(interval)
    
    @RPC.export
    def get_config(self):
        """RPC method to get the current configuration."""
        return self._config
    
    @RPC.export
    def set_config_value(self, key, value):
        """RPC method to set a specific configuration value."""
        try:
            # Create a copy of the current config
            new_config = self._config.copy()
            
            # Update the value (supporting nested paths like "nested.setting1")
            keys = key.split(".")
            target = new_config
            for k in keys[:-1]:
                if k not in target or not isinstance(target[k], dict):
                    target[k] = {}
                target = target[k]
            target[keys[-1]] = value
            
            # Store the updated config
            result = self.config.set("config", new_config).get(timeout=5)
            return {"success": True, "message": f"Updated {key} to {value}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @RPC.export
    def reset_config(self):
        """RPC method to reset the configuration to defaults."""
        try:
            result = self.config.set("config", self.default_config.copy()).get(timeout=5)
            return {"success": True, "message": "Configuration reset to defaults"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @RPC.export
    def get_status(self):
        """RPC method to get the agent's status."""
        self._status["current_time"] = datetime.datetime.now().isoformat()
        return self._status


if __name__ == "__main__":
    # Create and run the config test agent
    agent = ConfigTestAgent()
    
    try:
        # Connect to the server
        print("Connecting config test agent to the platform...")
        agent.connect()
        
        # Keep the agent running
        print("Config test agent is running...")
        while True:
            gevent.sleep(10)  # Sleep to keep the agent alive
            
    except KeyboardInterrupt:
        print("Keyboard interrupt received, stopping agent...")
    finally:
        # Ensure proper shutdown
        if agent.core.stop().get(timeout=5):
            print("Config test agent stopped cleanly")
        else:
            print("Error stopping config test agent")