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
    
    # Set up subscriptions - using the new hierarchical API
    subscriber1.vip.pubsub.subscribe("test/")
    subscriber2.vip.pubsub.subscribe("test/special/")
    
    # Set up pattern subscription
    subscriber1.vip.pubsub.subscribe_regex(r"^pattern/\d+/test$")
    
    # Wait for subscriptions to be processed
    gevent.sleep(1)
    
    # Publish messages - using the new hierarchical API
    publisher.vip.pubsub.publish("test/topic1", "Hello from topic1")
    publisher.vip.pubsub.publish("test/special/topic2", "Hello from special topic2")
    publisher.vip.pubsub.publish("other/topic3", "Hello from other topic3")
    publisher.vip.pubsub.publish("pattern/123/test", "Hello from pattern match")
    
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
    publisher.core.stop()
    subscriber1.core.stop()
    subscriber2.core.stop()


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
    
    # Send VIP messages - using the new hierarchical API
    agent1.vip.send_message("agent2", "rpc", ["hello", "world"])
    agent2.vip.send_message("agent1", "rpc", ["response", "received"])
    
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
    agent1.core.stop()
    agent2.core.stop()


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
    
    # Export RPC methods on the server - using the new hierarchical API
    server.vip.rpc.export("add", lambda x, y: x + y)
    server.vip.rpc.export("multiply", lambda x, y: x * y)
    server.vip.rpc.export("greet", lambda name: f"Hello, {name}!")
    
    # Wait for methods to be registered
    gevent.sleep(1)
    
    # Make RPC calls from the client to the server - using the new hierarchical API
    try:
        print("\nMaking RPC calls:")
        
        result1 = client.vip.rpc.call("server", "add", 5, 3)
        print(f"  add(5, 3) = {result1}")
        
        result2 = client.vip.rpc.call("server", "multiply", 4, 7)
        print(f"  multiply(4, 7) = {result2}")
        
        result3 = client.vip.rpc.call("server", "greet", "VOLTTRON")
        print(f"  greet('VOLTTRON') = {result3}")
        
    except Exception as e:
        print(f"Error in RPC test: {e}")
    
    # Clean up
    server.core.stop()
    client.core.stop()


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
        
        # Export RPC methods - using the new hierarchical API
        agent1.vip.rpc.export("ping", lambda: "pong from agent1")
        agent2.vip.rpc.export("ping", lambda: "pong from agent2")
        
        # Wait for methods to be registered
        gevent.sleep(1)
        
        print("\nTesting basic RPC calls:")
        
        # Direct call from agent1 to agent2
        print("\nDirect call from agent1 to agent2:")
        result = agent1.vip.rpc.call("agent2", "ping")
        print(f"  agent1 -> agent2.ping() = {result}")
        
        # Direct call from agent2 to agent1
        print("\nDirect call from agent2 to agent1:")
        result = agent2.vip.rpc.call("agent1", "ping")
        print(f"  agent2 -> agent1.ping() = {result}")
        
        # Test ping utility function
        print("\nTesting ping utility:")
        is_alive = agent1.vip.ping("agent2")
        print(f"  agent2 is alive: {is_alive}")
        
    except Exception as e:
        print(f"Error in multi-hop RPC test: {e}")
    finally:
        # Clean up
        agent1.core.stop()
        agent2.core.stop()


def run_config_test():
    """Test agent configuration functionality."""
    # Create test agent
    agent = Agent("config_test_agent")
    
    # Connect agent
    agent.connect()
    
    # Set configuration values
    agent.vip.config.set("max_retries", 3)
    agent.vip.config.set("timeout", 30)
    agent.vip.config.set("server_address", "tcp://127.0.0.1:22916")
    
    # Get configuration values
    max_retries = agent.vip.config.get("max_retries")
    timeout = agent.vip.config.get("timeout")
    server_address = agent.vip.config.get("server_address")
    
    print("\nConfiguration values:")
    print(f"  max_retries: {max_retries}")
    print(f"  timeout: {timeout}")
    print(f"  server_address: {server_address}")
    
    # List all configuration keys
    keys = agent.vip.config.list()
    print(f"  All keys: {keys}")
    
    # Clean up
    agent.core.stop()


def run_core_callback_test():
    """Test agent core callbacks."""
    # Create test agent
    agent = Agent("callback_test_agent")
    
    # Set up callbacks
    start_called = [False]
    stop_called = [False]
    
    def on_start():
        print("Agent started callback executed")
        start_called[0] = True
    
    def on_stop():
        print("Agent stop callback executed")
        stop_called[0] = True
    
    # Register callbacks
    agent.core.onstart(on_start)
    agent.core.onstop(on_stop)
    
    # Connect agent (should trigger onstart)
    agent.connect()
    gevent.sleep(1)
    
    print(f"\nStart callback was called: {start_called[0]}")
    
    # Stop agent (should trigger onstop)
    agent.core.stop()
    gevent.sleep(1)
    
    print(f"Stop callback was called: {stop_called[0]}")


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
        elif test_name == "config":
            print("Running Config Test")
            run_config_test()
        elif test_name == "core":
            print("Running Core Callback Test")
            run_core_callback_test()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: pubsub, vip, rpc, multihop, config, core")
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
        
        print("\n=== Running Config Test ===")
        run_config_test()
        
        print("\n=== Running Core Callback Test ===")
        run_core_callback_test()