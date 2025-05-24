# gevent_message_bus_test_clients.py

import gevent
from gevent import monkey
# Patch standard library to work with gevent
monkey.patch_all()

import json
import uuid
import websocket
from typing import Dict, Any, Optional, Callable
import ssl


class GeventMessageBusTestClient:
    """Gevent-based test client for connecting to the FastAPI MessageBus."""
    
    def __init__(self, identity: str, host: str = "127.0.0.1", port: int = 8000):
        self.identity = identity
        self.websocket_url = f"ws://{host}:{port}/ws/{identity}"
        self.websocket = None
        self.connected = False
        self.subscriptions = {}
        self.received_messages = []
        self.callback_handler = None
        self._listener_greenlet = None
    
    def connect(self):
        """Connect to the message bus."""
        # Enable trace for debugging if needed
        # websocket.enableTrace(True)
        
        # Create a WebSocketApp
        self.websocket = websocket.WebSocketApp(
            self.websocket_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )
        
        # Start the WebSocket connection in a separate greenlet
        self._listener_greenlet = gevent.spawn(
            self.websocket.run_forever, 
            sslopt={"cert_reqs": ssl.CERT_NONE}  # Allow self-signed certs if needed
        )
        
        # Wait for the connection to be established
        while not self.connected:
            gevent.sleep(0.1)
        
        print(f"Client {self.identity} connected")
    
    def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            self.websocket.close()
            # Wait for the close to complete
            if self._listener_greenlet:
                self._listener_greenlet.join(timeout=1)
            self.connected = False
            print(f"Client {self.identity} disconnected")
    
    def _on_open(self, ws):
        """Callback when WebSocket connection is opened."""
        self.connected = True
    
    def _on_message(self, ws, message):
        """Callback when a message is received."""
        try:
            data = json.loads(message)
            self.received_messages.append(data)
            print(f"Client {self.identity} received: {data}")
            
            if self.callback_handler:
                self.callback_handler(data)
            
            # Handle pubsub messages with registered callbacks
            if data.get("type") == "pubsub":
                topic = data.get("topic", "")
                for prefix, callback in self.subscriptions.items():
                    if topic.startswith(prefix) or prefix in ["*", "all"]:
                        callback(data)
        except Exception as e:
            print(f"Error processing message in client {self.identity}: {e}")
    
    def _on_error(self, ws, error):
        """Callback when an error occurs."""
        print(f"Client {self.identity} error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Callback when the connection is closed."""
        self.connected = False
        print(f"Client {self.identity} connection closed: {close_status_code} {close_msg}")
    
    def subscribe_prefix(self, prefix: str, callback: Optional[Callable] = None):
        """Subscribe to a topic prefix."""
        if not self.connected:
            raise ConnectionError("Client not connected")
        
        subscription_id = str(uuid.uuid4())
        self.subscriptions[prefix] = callback or (lambda msg: print(f"Subscription callback for {prefix}: {msg}"))
        
        self.websocket.send(json.dumps({
            "type": "subscribe",
            "prefix": prefix,
            "id": subscription_id
        }))
        
        print(f"Client {self.identity} subscribed to prefix: {prefix}")
        return subscription_id
    
    def subscribe_pattern(self, pattern: str, callback: Optional[Callable] = None):
        """Subscribe to a topic pattern."""
        if not self.connected:
            raise ConnectionError("Client not connected")
        
        subscription_id = str(uuid.uuid4())
        # Note: We're using the pattern as the key here
        self.subscriptions[pattern] = callback or (lambda msg: print(f"Subscription callback for {pattern}: {msg}"))
        
        self.websocket.send(json.dumps({
            "type": "subscribe",
            "pattern": pattern,
            "id": subscription_id
        }))
        
        print(f"Client {self.identity} subscribed to pattern: {pattern}")
        return subscription_id
    
    def publish(self, topic: str, message: Any, headers: Optional[Dict] = None, bus: str = ""):
        """Publish a message to a topic."""
        if not self.connected:
            raise ConnectionError("Client not connected")
        
        if headers is None:
            headers = {}
        
        self.websocket.send(json.dumps({
            "type": "publish",
            "bus": bus,
            "topic": topic,
            "headers": headers,
            "message": message
        }))
        
        print(f"Client {self.identity} published to {topic}: {message}")
    
    def send_vip_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message."""
        if not self.connected:
            raise ConnectionError("Client not connected")
        
        if args is None:
            args = []
        
        msg_id = str(uuid.uuid4())
        
        self.websocket.send(json.dumps({
            "type": "vip",
            "message": {
                "peer": peer,
                "user": self.identity,
                "subsystem": subsystem,
                "msg_id": msg_id,
                "args": args
            }
        }))
        
        print(f"Client {self.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}")
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


def run_publisher_subscriber_test():
    """Test publisher and subscriber functionality."""
    # Create test clients
    publisher = GeventMessageBusTestClient("publisher")
    subscriber1 = GeventMessageBusTestClient("subscriber1")
    subscriber2 = GeventMessageBusTestClient("subscriber2")
    
    # Connect all clients
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
    # Create test clients
    client1 = GeventMessageBusTestClient("client1")
    client2 = GeventMessageBusTestClient("client2")
    
    # Connect clients
    client1.connect()
    client2.connect()
    
    # Wait for connections to be established
    gevent.sleep(1)
    
    # Send VIP messages
    client1.send_vip_message("client2", "rpc", ["hello", "world"])
    client2.send_vip_message("client1", "rpc", ["response", "received"])
    
    # Wait for messages to be processed
    gevent.sleep(2)
    
    # Print results
    print("\nClient 1 received VIP messages:")
    for msg in client1.get_received_messages():
        print(f"  {msg}")
    
    print("\nClient 2 received VIP messages:")
    for msg in client2.get_received_messages():
        print(f"  {msg}")
    
    # Clean up
    client1.disconnect()
    client2.disconnect()


def run_complex_test():
    """Run a more complex test with multiple clients and interactions."""
    # Create test clients
    coordinator = GeventMessageBusTestClient("coordinator")
    agent1 = GeventMessageBusTestClient("agent1")
    agent2 = GeventMessageBusTestClient("agent2")
    agent3 = GeventMessageBusTestClient("agent3")
    
    # Connect all clients
    coordinator.connect()
    agent1.connect()
    agent2.connect()
    agent3.connect()
    
    # Wait for connections to be established
    gevent.sleep(1)
    
    # Set up subscriptions for different patterns
    agent1.subscribe_prefix("control/agent1/")
    agent2.subscribe_prefix("control/agent2/")
    agent3.subscribe_prefix("control/all/")
    
    # All agents subscribe to broadcast
    agent1.subscribe_prefix("broadcast/")
    agent2.subscribe_prefix("broadcast/")
    agent3.subscribe_prefix("broadcast/")
    
    # Wait for subscriptions to be processed
    gevent.sleep(1)
    
    # Coordinator sends control messages
    coordinator.publish("control/agent1/start", {"command": "start", "parameters": {"delay": 0}})
    coordinator.publish("control/agent2/stop", {"command": "stop", "reason": "maintenance"})
    coordinator.publish("control/all/status", {"command": "report_status"})
    
    # Coordinator sends a broadcast
    coordinator.publish("broadcast/attention", {"message": "System will restart in 5 minutes"})
    
    # Agent1 responds with VIP message
    agent1.send_vip_message("coordinator", "response", ["status", "running"])
    
    # Wait for messages to be processed
    gevent.sleep(2)
    
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
    coordinator.disconnect()
    agent1.disconnect()
    agent2.disconnect()
    agent3.disconnect()


if __name__ == "__main__":
    # Run the test functions
    print("=== Running Publisher-Subscriber Test ===")
    run_publisher_subscriber_test()
    
    print("\n=== Running VIP Message Test ===")
    run_vip_message_test()
    
    print("\n=== Running Complex Interaction Test ===")
    run_complex_test()