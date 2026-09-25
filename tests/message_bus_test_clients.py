# message_bus_test_clients.py

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import websockets


class MessageBusTestClient:
    """Test client for connecting to the FastAPI MessageBus."""

    def __init__(self, identity: str, host: str = "127.0.0.1", port: int = 8000):
        self.identity = identity
        self.websocket_url = f"ws://{host}:{port}/ws/{identity}"
        self.websocket = None
        self.connected = False
        self.subscriptions = {}
        self.received_messages = []
        self.callback_handler = None

    async def connect(self):
        """Connect to the message bus."""
        self.websocket = await websockets.connect(self.websocket_url)
        self.connected = True
        # Start the listener task
        asyncio.create_task(self._listen())
        print(f"Client {self.identity} connected")

    async def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            await self.websocket.close()
            self.connected = False
            print(f"Client {self.identity} disconnected")

    async def _listen(self):
        """Listen for incoming messages."""
        try:
            while self.connected:
                message = await self.websocket.recv()
                data = json.loads(message)
                self.received_messages.append(data)
                print(f"Client {self.identity} received: {data}")

                if self.callback_handler:
                    await self.callback_handler(data)

                # Handle pubsub messages with registered callbacks
                if data.get("type") == "pubsub":
                    topic = data.get("topic", "")
                    for prefix, callback in self.subscriptions.items():
                        if topic.startswith(prefix):
                            callback(data)
        except websockets.exceptions.ConnectionClosed:
            self.connected = False
            print(f"Client {self.identity} connection closed")
        except Exception as e:
            print(f"Error in client {self.identity} listener: {e}")

    async def subscribe_prefix(self, prefix: str, callback: Callable | None = None):
        """Subscribe to a topic prefix."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        subscription_id = str(uuid.uuid4())
        self.subscriptions[prefix] = callback or (
            lambda msg: print(f"Subscription callback for {prefix}: {msg}")
        )

        await self.websocket.send(
            json.dumps({"type": "subscribe", "prefix": prefix, "id": subscription_id})
        )

        print(f"Client {self.identity} subscribed to prefix: {prefix}")
        return subscription_id

    async def subscribe_pattern(self, pattern: str, callback: Callable | None = None):
        """Subscribe to a topic pattern."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        subscription_id = str(uuid.uuid4())
        # Note: We're using the pattern as the key here, which might not be ideal for regex patterns
        self.subscriptions[pattern] = callback or (
            lambda msg: print(f"Subscription callback for {pattern}: {msg}")
        )

        await self.websocket.send(
            json.dumps({"type": "subscribe", "pattern": pattern, "id": subscription_id})
        )

        print(f"Client {self.identity} subscribed to pattern: {pattern}")
        return subscription_id

    async def publish(
        self, topic: str, message: Any, headers: dict | None = None, bus: str = ""
    ):
        """Publish a message to a topic."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        if headers is None:
            headers = {}

        await self.websocket.send(
            json.dumps(
                {
                    "type": "publish",
                    "bus": bus,
                    "topic": topic,
                    "headers": headers,
                    "message": message,
                }
            )
        )

        print(f"Client {self.identity} published to {topic}: {message}")

    async def send_vip_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        if args is None:
            args = []

        msg_id = str(uuid.uuid4())

        await self.websocket.send(
            json.dumps(
                {
                    "type": "vip",
                    "message": {
                        "peer": peer,
                        "user": self.identity,
                        "subsystem": subsystem,
                        "msg_id": msg_id,
                        "args": args,
                    },
                }
            )
        )

        print(
            f"Client {self.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}"
        )
        return msg_id

    def set_callback_handler(self, callback):
        """Set a global callback handler for all messages."""
        self.callback_handler = callback

    def get_received_messages(self):
        """Get all received messages."""
        return self.received_messages

    def clear_received_messages(self):
        """Clear the received messages list."""
        self.received_messages = []


async def run_publisher_subscriber_test():
    """Test publisher and subscriber functionality."""
    # Create test clients
    publisher = MessageBusTestClient("publisher")
    subscriber1 = MessageBusTestClient("subscriber1")
    subscriber2 = MessageBusTestClient("subscriber2")

    # Connect all clients
    await publisher.connect()
    await subscriber1.connect()
    await subscriber2.connect()

    # Wait for connections to be established
    await asyncio.sleep(1)

    # Set up subscriptions
    await subscriber1.subscribe_prefix("test/")
    await subscriber2.subscribe_prefix("test/special/")

    # Set up pattern subscription
    await subscriber1.subscribe_pattern(r"^pattern/\d+/test$")

    # Wait for subscriptions to be processed
    await asyncio.sleep(1)

    # Publish messages
    await publisher.publish("test/topic1", "Hello from topic1")
    await publisher.publish("test/special/topic2", "Hello from special topic2")
    await publisher.publish("other/topic3", "Hello from other topic3")
    await publisher.publish("pattern/123/test", "Hello from pattern match")

    # Wait for messages to be processed
    await asyncio.sleep(2)

    # Print results
    print("\nSubscriber 1 received messages:")
    for msg in subscriber1.get_received_messages():
        print(f"  {msg}")

    print("\nSubscriber 2 received messages:")
    for msg in subscriber2.get_received_messages():
        print(f"  {msg}")

    # Clean up
    await publisher.disconnect()
    await subscriber1.disconnect()
    await subscriber2.disconnect()


async def run_vip_message_test():
    """Test VIP message functionality."""
    # Create test clients
    client1 = MessageBusTestClient("client1")
    client2 = MessageBusTestClient("client2")

    # Connect clients
    await client1.connect()
    await client2.connect()

    # Wait for connections to be established
    await asyncio.sleep(1)

    # Send VIP messages
    await client1.send_vip_message("client2", "rpc", ["hello", "world"])
    await client2.send_vip_message("client1", "rpc", ["response", "received"])

    # Wait for messages to be processed
    await asyncio.sleep(2)

    # Print results
    print("\nClient 1 received VIP messages:")
    for msg in client1.get_received_messages():
        print(f"  {msg}")

    print("\nClient 2 received VIP messages:")
    for msg in client2.get_received_messages():
        print(f"  {msg}")

    # Clean up
    await client1.disconnect()
    await client2.disconnect()


async def run_complex_test():
    """Run a more complex test with multiple clients and interactions."""
    # Create test clients
    coordinator = MessageBusTestClient("coordinator")
    agent1 = MessageBusTestClient("agent1")
    agent2 = MessageBusTestClient("agent2")
    agent3 = MessageBusTestClient("agent3")

    # Connect all clients
    await coordinator.connect()
    await agent1.connect()
    await agent2.connect()
    await agent3.connect()

    # Wait for connections to be established
    await asyncio.sleep(1)

    # Set up subscriptions for different patterns
    await agent1.subscribe_prefix("control/agent1/")
    await agent2.subscribe_prefix("control/agent2/")
    await agent3.subscribe_prefix("control/all/")

    # All agents subscribe to broadcast
    await agent1.subscribe_prefix("broadcast/")
    await agent2.subscribe_prefix("broadcast/")
    await agent3.subscribe_prefix("broadcast/")

    # Wait for subscriptions to be processed
    await asyncio.sleep(1)

    # Coordinator sends control messages
    await coordinator.publish(
        "control/agent1/start", {"command": "start", "parameters": {"delay": 0}}
    )
    await coordinator.publish(
        "control/agent2/stop", {"command": "stop", "reason": "maintenance"}
    )
    await coordinator.publish("control/all/status", {"command": "report_status"})

    # Coordinator sends a broadcast
    await coordinator.publish(
        "broadcast/attention", {"message": "System will restart in 5 minutes"}
    )

    # Agent1 responds with VIP message
    await agent1.send_vip_message("coordinator", "response", ["status", "running"])

    # Wait for messages to be processed
    await asyncio.sleep(2)

    # Print results
    print("\nCoordinator received messages:")
    for msg in coordinator.get_received_messages():
        print(f"  {msg}")

    print("\nAgent1 received messages:")
    for msg in agent1.get_received_messages():
        print(f"  {msg}")

    print("\nAgent2 received messages:")
    for msg in agent2.get_received_messages():
        print(f"  {msg}")

    print("\nAgent3 received messages:")
    for msg in agent3.get_received_messages():
        print(f"  {msg}")

    # Clean up
    await coordinator.disconnect()
    await agent1.disconnect()
    await agent2.disconnect()
    await agent3.disconnect()


if __name__ == "__main__":
    # Run the test functions
    print("=== Running Publisher-Subscriber Test ===")
    asyncio.run(run_publisher_subscriber_test())

    print("\n=== Running VIP Message Test ===")
    asyncio.run(run_vip_message_test())

    print("\n=== Running Complex Interaction Test ===")
    asyncio.run(run_complex_test())
