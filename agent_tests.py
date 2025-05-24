# agent_tests.py

import gevent
from agent import Agent


def run_publisher_subscriber_test():
    """Test publisher and subscriber functionality."""
    # Create test agents
    publisher = Agent("publisher")
    subscriber1 = Agent("subscriber1")
    subscriber2 = Agent("subscriber2")
    
    # Connect all agents
    publisher.connect()
    subscriber1.connect()
    subscriber2.connect()
    
    # Wait for connections to be established
    gevent.sleep(1)
    
    # Set up subscriptions
    subscriber1.subscribe_prefix("test/")
    subscriber2.subscribe_prefix("test/special/")
    
    # Set up pattern subscription
    subscriber1.subscribe_pattern(r"^pattern/\d+/test$")
    
    # Wait for subscriptions to be processed
    gevent.sleep(1)
    
    # Publish messages
    publisher.publish("test/topic1", "Hello from topic1")
    publisher.publish("test/special/topic2", "Hello from special topic2")
    publisher.publish("other/topic3", "Hello from other topic3")
    publisher.publish("pattern/123/test", "Hello from pattern match")
    
    # Wait for messages to be processed
    gevent.sleep(2)
    
    # Print results
    print("\nSubscriber 1 received messages:")
    for msg in subscriber1.get_received_messages():
        print(f"  {msg}")
    
    print("\nSubscriber 2 received messages:")
    for msg in subscriber2.get_received_messages():
        print(f"  {msg}")
    
    # Clean up
    publisher.disconnect()
    subscriber1.disconnect()
    subscriber2.disconnect()


def run_vip_message_test():
    """Test VIP message functionality."""
    # Create test agents
    agent1 = Agent("agent1")
    agent2 = Agent("agent2")
    
    # Connect agents
    agent1.connect()
    agent2.connect()
    
    # Wait for connections to be established
    gevent.sleep(1)
    
    # Send VIP messages
    agent1.send_vip_message("agent2", "rpc", ["hello", "world"])
    agent2.send_vip_message("agent1", "rpc", ["response", "received"])
    
    # Wait for messages to be processed
    gevent.sleep(2)
    
    # Print results
    print("\nAgent 1 received VIP messages:")
    for msg in agent1.get_received_messages():
        print(f"  {msg}")
    
    print("\nAgent 2 received VIP messages:")
    for msg in agent2.get_received_messages():
        print(f"  {msg}")
    
    # Clean up
    agent1.disconnect()
    agent2.disconnect()


def run_rpc_test():
    """Test RPC functionality between agents."""
    # Create test agents
    server = Agent("server")
    client = Agent("client")
    
    # Connect agents
    server.connect()
    client.connect()
    
    # Wait for connections to be established
    gevent.sleep(1)
    
    # Export RPC methods on the server
    server.export_rpc_method("add", lambda x, y: x + y)
    server.export_rpc_method("multiply", lambda x, y: x * y)
    server.export_rpc_method("greet", lambda name: f"Hello, {name}!")
    
    # Wait for methods to be registered
    gevent.sleep(1)
    
    # Make RPC calls from the client to the server
    try:
        print("\nMaking RPC calls:")
        
        result1 = client.rpc_call("server", "add", 5, 3)
        print(f"  add(5, 3) = {result1}")
        
        result2 = client.rpc_call("server", "multiply", 4, 7)
        print(f"  multiply(4, 7) = {result2}")
        
        result3 = client.rpc_call("server", "greet", "VOLTTRON")
        print(f"  greet('VOLTTRON') = {result3}")
        
    except Exception as e:
        print(f"Error in RPC test: {e}")
    
    # Clean up
    server.disconnect()
    client.disconnect()


def run_multi_hop_rpc_test():
    """Test multi-hop RPC functionality between agents."""
    # Create test agents
    agent1 = Agent("agent1")
    agent2 = Agent("agent2")
    
    try:
        # Connect agents
        agent1.connect()
        agent2.connect()
        
        # Wait for connections to be established
        gevent.sleep(2)
        
        # Export RPC methods
        agent1.export_rpc_method("ping", lambda: "pong from agent1")
        agent2.export_rpc_method("ping", lambda: "pong from agent2")
        
        # Wait for methods to be registered
        gevent.sleep(1)
        
        print("\nTesting basic RPC calls:")
        
        # Direct call from agent1 to agent2
        print("\nDirect call from agent1 to agent2:")
        result = agent1.rpc_call("agent2", "ping")
        print(f"  agent1 -> agent2.ping() = {result}")
        
        # Direct call from agent2 to agent1
        print("\nDirect call from agent2 to agent1:")
        result = agent2.rpc_call("agent1", "ping")
        print(f"  agent2 -> agent1.ping() = {result}")
        
    except Exception as e:
        print(f"Error in multi-hop RPC test: {e}")
    finally:
        # Clean up
        agent1.disconnect()
        agent2.disconnect()


if __name__ == "__main__":
    import sys
    
    # Check if an argument was provided
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "pubsub":
            print("Running Publisher-Subscriber Test")
            run_publisher_subscriber_test()
        elif test_name == "vip":
            print("Running VIP Message Test")
            run_vip_message_test()
        elif test_name == "rpc":
            print("Running RPC Test")
            run_rpc_test()
        elif test_name == "multihop":
            print("Running Multi-Hop RPC Test")
            run_multi_hop_rpc_test()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: pubsub, vip, rpc, multihop")
    else:
        # Run all tests
        print("Running all tests")
        print("\n=== Running Publisher-Subscriber Test ===")
        run_publisher_subscriber_test()
        
        print("\n=== Running VIP Message Test ===")
        run_vip_message_test()
        
        print("\n=== Running RPC Test ===")
        run_rpc_test()
        
        print("\n=== Running Multi-Hop RPC Test ===")
        run_multi_hop_rpc_test()