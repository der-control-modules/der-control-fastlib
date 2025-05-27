# config_test_agent_test.py - Updated with cron testing

import gevent
from agent import Agent
from config_test_agent import ConfigTestAgent
import datetime
import random
import sys


def run_config_test():
    """Test the ConfigTestAgent and ConfigStore functionality."""
    # Create a config test agent
    config_agent = ConfigTestAgent()
    
    # Create a controller agent that will interact with the config agent
    controller = Agent("controller")
    
    try:
        # Connect agents
        config_agent.connect()
        controller.connect()
        
        # Wait for connections to be established
        gevent.sleep(2)
        
        # Get the config agent's initial status
        print("\n=== Initial Status ===")
        status = controller.vip.rpc.call("config_test", "get_status").get(timeout=5)
        print(f"Status: {status}")
        
        # Get the config agent's initial configuration
        print("\n=== Initial Configuration ===")
        config = controller.vip.rpc.call("config_test", "get_config").get(timeout=5)
        print(f"Configuration: {config}")
        
        # Wait a moment to see the agent process with the current config
        gevent.sleep(5)
        
        # Modify a configuration value
        print("\n=== Modifying Configuration ===")
        result = controller.vip.rpc.call("config_test", "set_config_value", "interval", 10).get(timeout=5)
        print(f"Set interval=10 result: {result}")
        
        # Wait to see the effect of the configuration change
        gevent.sleep(15)
        
        # Modify a nested configuration value
        print("\n=== Modifying Nested Configuration ===")
        result = controller.vip.rpc.call("config_test", "set_config_value", "nested.setting1", "newvalue").get(timeout=5)
        print(f"Set nested.setting1=newvalue result: {result}")
        
        # Add new targets
        print("\n=== Adding Targets ===")
        new_targets = ["device1", "device2", "device3", "device4"]
        result = controller.vip.rpc.call("config_test", "set_config_value", "targets", new_targets).get(timeout=5)
        print(f"Set new targets result: {result}")
        
        # Wait to see the effect of the configuration changes
        gevent.sleep(15)
        
        # Get the updated status
        print("\n=== Updated Status ===")
        status = controller.vip.rpc.call("config_test", "get_status").get(timeout=5)
        print(f"Status: {status}")
        
        # Test switching to cron scheduling
        print("\n=== Switching to Cron Scheduling ===")
        # Use "*/1 * * * *" for testing to ensure it runs every minute
        result = controller.vip.rpc.call("config_test", "switch_to_cron", "*/1 * * * *").get(timeout=5)
        print(f"Switch to cron result: {result}")
        
        # Wait to see the effect of cron scheduling
        print("Waiting for cron schedule to trigger (up to 70 seconds)...")
        gevent.sleep(70)  # Wait long enough for the cron schedule to trigger
        
        # Check the status after cron scheduling
        print("\n=== Status After Cron Scheduling ===")
        status = controller.vip.rpc.call("config_test", "get_status").get(timeout=5)
        print(f"Status: {status}")
        
        # Switch back to interval scheduling
        print("\n=== Switching Back to Interval Scheduling ===")
        result = controller.vip.rpc.call("config_test", "switch_to_interval", 15).get(timeout=5)
        print(f"Switch to interval result: {result}")
        
        # Wait to see the effect of interval scheduling
        print("Waiting for interval schedule to trigger (20 seconds)...")
        gevent.sleep(20)
        
        # Check the status after interval scheduling
        print("\n=== Status After Interval Scheduling ===")
        status = controller.vip.rpc.call("config_test", "get_status").get(timeout=5)
        print(f"Status: {status}")
        
        # Reset the configuration to defaults
        print("\n=== Resetting Configuration ===")
        result = controller.vip.rpc.call("config_test", "reset_config").get(timeout=5)
        print(f"Reset result: {result}")
        
        # Wait to see the effect of the configuration reset
        gevent.sleep(15)
        
        # Get final status
        print("\n=== Final Status ===")
        status = controller.vip.rpc.call("config_test", "get_status").get(timeout=5)
        print(f"Status: {status}")
        
        # Get final configuration
        print("\n=== Final Configuration ===")
        config = controller.vip.rpc.call("config_test", "get_config").get(timeout=5)
        print(f"Configuration: {config}")
        
    except Exception as e:
        print(f"Error in config test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        config_agent.core.stop().get(timeout=5)
        controller.core.stop().get(timeout=5)


if __name__ == "__main__":
    print("=== Running Config Test Agent Test ===")
    run_config_test()
    
    # If this is not being run directly, allow other options
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        # Just run the config agent directly
        agent = ConfigTestAgent()
        
        try:
            agent.connect()
            print("Config test agent running. Press Ctrl+C to exit.")
            
            # Keep the agent running
            while True:
                gevent.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            agent.core.stop().get(timeout=5)