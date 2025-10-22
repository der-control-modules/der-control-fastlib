# agent_tests.py

import gevent

from aems.client.agent import Agent


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
    subscriber1.vip.pubsub.subscribe("test/").get()  # Wait for result
    subscriber2.vip.pubsub.subscribe("test/special/").get()  # Wait for result

    # Set up pattern subscription
    subscriber1.vip.pubsub.subscribe_regex(
        r"^pattern/\d+/test$"
    ).get()  # Wait for result

    # Wait for subscriptions to be processed
    gevent.sleep(1)

    # Publish messages - using the new hierarchical API with AsyncResult
    publisher.vip.pubsub.publish(
        "test/topic1", "Hello from topic1"
    ).get()  # Wait for result
    publisher.vip.pubsub.publish(
        "test/special/topic2", "Hello from special topic2"
    ).get()  # Wait for result
    publisher.vip.pubsub.publish(
        "other/topic3", "Hello from other topic3"
    ).get()  # Wait for result
    publisher.vip.pubsub.publish(
        "pattern/123/test", "Hello from pattern match"
    ).get()  # Wait for result

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
    publisher.core.stop().get()  # Wait for stop to complete
    subscriber1.core.stop().get()  # Wait for stop to complete
    subscriber2.core.stop().get()  # Wait for stop to complete


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

    # Send VIP messages - using AsyncResults to wait for completion
    agent1.vip.send_message("agent2", "rpc", ["hello", "world"]).get()
    agent2.vip.send_message("agent1", "rpc", ["response", "received"]).get()

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
    agent1.core.stop().get()
    agent2.core.stop().get()


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
    server.vip.rpc.export("add", lambda x, y: x + y)
    server.vip.rpc.export("multiply", lambda x, y: x * y)
    server.vip.rpc.export("greet", lambda name: f"Hello, {name}!")

    # Wait for methods to be registered
    gevent.sleep(1)

    # Make RPC calls from the client to the server - using AsyncResult
    try:
        print("\nMaking RPC calls:")

        # Get AsyncResult from RPC call then wait for the result
        result1 = client.vip.rpc.call("server", "add", 5, 3).get(timeout=5)
        print(f"  add(5, 3) = {result1}")

        # Get AsyncResult from RPC call then wait for the result
        result2 = client.vip.rpc.call("server", "multiply", 4, 7).get(timeout=5)
        print(f"  multiply(4, 7) = {result2}")

        # Get AsyncResult from RPC call then wait for the result
        result3 = client.vip.rpc.call("server", "greet", "VOLTTRON").get(timeout=5)
        print(f"  greet('VOLTTRON') = {result3}")

        # Test non-blocking operation - saving the AsyncResult for later use
        print("\nTesting non-blocking operation:")
        async_result = client.vip.rpc.call("server", "add", 10, 20)
        print("  RPC call made, continuing without waiting...")
        gevent.sleep(1)  # Do other work
        print(f"  Now getting the result: {async_result.get()}")

    except Exception as e:
        print(f"Error in RPC test: {e}")

    # Clean up
    server.core.stop().get()
    client.core.stop().get()


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
        agent1.vip.rpc.export("ping", lambda: "pong from agent1")
        agent2.vip.rpc.export("ping", lambda: "pong from agent2")

        # Wait for methods to be registered
        gevent.sleep(1)

        print("\nTesting basic RPC calls:")

        # Direct call from agent1 to agent2 - using AsyncResult
        print("\nDirect call from agent1 to agent2:")
        result = agent1.vip.rpc.call("agent2", "ping").get(timeout=5)
        print(f"  agent1 -> agent2.ping() = {result}")

        # Direct call from agent2 to agent1
        print("\nDirect call from agent2 to agent1:")
        result = agent2.vip.rpc.call("agent1", "ping").get(timeout=5)
        print(f"  agent2 -> agent1.ping() = {result}")

        # Test ping utility function
        print("\nTesting ping utility:")
        is_alive = agent1.vip.ping("agent2").get(timeout=5)
        print(f"  agent2 is alive: {is_alive}")

    except Exception as e:
        print(f"Error in multi-hop RPC test: {e}")
    finally:
        # Clean up
        agent1.core.stop().get(timeout=2)
        agent2.core.stop().get(timeout=2)


def run_config_test():
    """Test agent configuration functionality."""
    # Create test agent
    agent = Agent("config_test_agent")

    # Connect agent
    agent.connect()

    # Set configuration values - using AsyncResult to wait for completion
    agent.vip.config.set("max_retries", 3).get()
    agent.vip.config.set("timeout", 30).get()
    agent.vip.config.set("server_address", "tcp://127.0.0.1:22916").get()

    # Get configuration values - using AsyncResult to get values
    max_retries = agent.vip.config.get("max_retries").get()
    timeout = agent.vip.config.get("timeout").get()
    server_address = agent.vip.config.get("server_address").get()

    print("\nConfiguration values:")
    print(f"  max_retries: {max_retries}")
    print(f"  timeout: {timeout}")
    print(f"  server_address: {server_address}")

    # List all configuration keys
    keys = agent.vip.config.list().get()
    print(f"  All keys: {keys}")

    # Clean up
    agent.core.stop().get()


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
    agent.core.stop().get()
    gevent.sleep(1)

    print(f"Stop callback was called: {stop_called[0]}")


def run_async_result_test():
    """Test AsyncResult functionality."""
    # Create test agents
    server = Agent("server")
    client = Agent("client")

    # Connect agents
    server.connect()
    client.connect()

    # Export a method that takes time to complete
    def slow_operation(seconds):
        print(f"Starting slow operation for {seconds} seconds...")
        gevent.sleep(seconds)  # Simulate a time-consuming operation
        print("Slow operation completed")
        return f"Completed after {seconds} seconds"

    server.vip.rpc.export("slow_operation", slow_operation)

    # Wait for method to be registered
    gevent.sleep(1)

    print("\nTesting AsyncResult with non-blocking calls:")

    # Make a non-blocking RPC call
    print("Making RPC call...")
    async_result = client.vip.rpc.call("server", "slow_operation", 3)
    print("RPC call made, continuing execution...")

    # Check if the result is ready
    print(f"Result ready? {async_result.ready()}")

    # Do some other work while waiting
    print("Doing other work while waiting for the result...")
    for i in range(3):
        print(f"  Working... {i + 1}")
        gevent.sleep(1)

    # Now wait for the result
    print("Now waiting for the result...")
    result = async_result.get()
    print(f"Result received: {result}")

    # Clean up
    server.core.stop().get()
    client.core.stop().get()


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
        elif test_name == "async":
            print("Running AsyncResult Test")
            run_async_result_test()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: pubsub, vip, rpc, multihop, config, core, async")
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

        print("\n=== Running AsyncResult Test ===")
        run_async_result_test()
