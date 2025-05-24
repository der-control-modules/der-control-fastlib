# listener_agent_test.py

import gevent
from agent import Agent
from listener_agent import ListenerAgent
import datetime
import random
import sys


def run_listener_test():
    """Test the ListenerAgent."""
    # Create a listener agent
    listener = ListenerAgent()
    
    # Create a publisher agent
    publisher = Agent("publisher")
    
    # Create a controller agent that will interact with the listener
    controller = Agent("controller")
    
    try:
        # Connect all agents
        listener.connect()
        publisher.connect()
        controller.connect()
        
        # Export RPC methods on the listener
        listener.vip.rpc.export("update_config", listener.update_config)
        listener.vip.rpc.export("reconfigure", listener.reconfigure)
        
        # Wait for connections to be established
        gevent.sleep(2)
        
        # Publish some test messages
        print("\n=== Publishing test messages ===")
        
        # Publish to various topics
        topics = [
            "devices/building1/hvac",
            "devices/building2/lights",
            "analysis/energy/consumption",
            "record/weather/temperature"
        ]
        
        for topic in topics:
            # Create a message with some data
            message = {
                "value": random.random() * 100,
                "timestamp": datetime.datetime.now().isoformat(),
                "units": "watts" if "energy" in topic else "celsius" if "temperature" in topic else "state",
                "source": "test_script"
            }
            
            # Publish the message
            print(f"Publishing to {topic}: {message}")
            publisher.vip.pubsub.publish(topic, message).get()
            
            # Wait a moment between publications
            gevent.sleep(1)
        
        # Wait for the listener to process all messages
        print("\nWaiting for listener to process all messages...")
        gevent.sleep(3)
        
        # Use RPC to update listener configuration
        print("\n=== Testing RPC configuration update ===")
        print("Adding an ignore pattern for 'analysis/' topics...")
        result = controller.vip.rpc.call("listener", "update_config", "ignore_patterns", ["analysis/"]).get()
        print(f"Result: {result}")
        
        # Publish another message after configuration change
        topic = "analysis/energy/prediction"
        message = {
            "value": random.random() * 200,
            "timestamp": datetime.datetime.now().isoformat(),
            "units": "watts",
            "source": "test_script",
            "note": "This message should be ignored"
        }
        print(f"\nPublishing to {topic} after config change: {message}")
        publisher.vip.pubsub.publish(topic, message).get()
        
        # Publish a message that should not be ignored
        topic = "devices/building3/hvac"
        message = {
            "value": random.random() * 100,
            "timestamp": datetime.datetime.now().isoformat(),
            "units": "state",
            "source": "test_script",
            "note": "This message should NOT be ignored"
        }
        print(f"\nPublishing to {topic} after config change: {message}")
        publisher.vip.pubsub.publish(topic, message).get()
        
        # Wait for processing
        gevent.sleep(2)
        
    except Exception as e:
        print(f"Error in listener test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        listener.core.stop().get()
        publisher.core.stop().get()
        controller.core.stop().get()


if __name__ == "__main__":
    print("=== Running Listener Agent Test ===")
    run_listener_test()
    
    # If this is not being run directly, allow other options
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        # Just run the listener agent directly
        listener = ListenerAgent()
        # Export RPC methods on the listener
        listener.vip.rpc.export_method("update_config", listener.update_config)
        listener.vip.rpc.export_method("reconfigure", listener.reconfigure)
        
        try:
            listener.connect()
            print("Listener agent running. Press Ctrl+C to exit.")
            
            # Keep the agent running
            while True:
                gevent.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            listener.core.stop().get()