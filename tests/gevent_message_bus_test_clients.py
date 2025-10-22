# gevent_message_bus_test_clients.py

import gevent
from gevent import monkey

# Patch standard library to work with gevent
monkey.patch_all()

import json
import ssl
import uuid
from collections.abc import Callable
from typing import Any

import websocket


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
        self.rpc_responses = {}
        self.exported_rpc_methods = {}  # Methods that can be called remotely

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
            on_open=self._on_open,
        )

        # Start the WebSocket connection in a separate greenlet
        self._listener_greenlet = gevent.spawn(
            self.websocket.run_forever,
            sslopt={"cert_reqs": ssl.CERT_NONE},  # Allow self-signed certs if needed
        )

        # Wait for the connection to be established
        timeout = 5
        start_time = gevent.time.time()
        while not self.connected:
            gevent.sleep(0.1)
            if gevent.time.time() - start_time > timeout:
                raise ConnectionError(f"Connection timeout for client {self.identity}")

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
        print(f"DEBUG: Client {self.identity} websocket connection opened")

    def _on_message(self, ws, message):
        """Callback when a message is received."""
        try:
            data = json.loads(message)
            self.received_messages.append(data)
            print(f"Client {self.identity} received: {data}")

            if self.callback_handler:
                self.callback_handler(data)

            # Handle different message types
            msg_type = data.get("type")

            if msg_type == "pubsub":
                # Handle pubsub messages with registered callbacks
                topic = data.get("topic", "")
                for prefix, callback in self.subscriptions.items():
                    if topic.startswith(prefix) or prefix in ["*", "all"]:
                        callback(data)

            elif msg_type == "rpc_request":
                # Handle RPC request
                print(f"DEBUG: Client {self.identity} received RPC request: {data}")
                data.get("sender")
                method_name = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                msg_id = data.get("msg_id")

                # Parse method name for potential remote calls
                parts = method_name.split(".")
                if len(parts) > 1:
                    # This is a remote call to another client
                    target = parts[0]
                    actual_method = ".".join(parts[1:])
                    print(f"DEBUG: Remote call detected: {target}.{actual_method}")

                    # Forward the call to the target client
                    try:
                        result = self.rpc_call(target, actual_method, *args, **kwargs)

                        # Send response back to original sender
                        print(
                            f"DEBUG: Client {self.identity} sending remote call response: {result}"
                        )
                        self.websocket.send(
                            json.dumps(
                                {
                                    "type": "rpc_response",
                                    "msg_id": msg_id,
                                    "result": result,
                                }
                            )
                        )
                    except Exception as e:
                        # Send error back to original sender
                        error_msg = f"Remote call error: {str(e)}"
                        print(f"DEBUG: {error_msg}")
                        self.websocket.send(
                            json.dumps(
                                {
                                    "type": "rpc_error",
                                    "msg_id": msg_id,
                                    "error": error_msg,
                                }
                            )
                        )
                else:
                    # This is a local method call
                    result = None
                    error = None

                    if method_name in self.exported_rpc_methods:
                        try:
                            method = self.exported_rpc_methods[method_name]
                            print(
                                f"DEBUG: Client {self.identity} executing method {method_name}"
                            )
                            result = method(*args, **kwargs)
                            print(
                                f"DEBUG: Client {self.identity} method {method_name} result: {result}"
                            )
                        except Exception as e:
                            error = str(e)
                            print(
                                f"DEBUG: Client {self.identity} method {method_name} error: {error}"
                            )
                    else:
                        error = f"Method {method_name} not found or not exported"
                        print(f"DEBUG: {error}")

                    # Send response
                    if error:
                        print(
                            f"DEBUG: Client {self.identity} sending RPC error response: {error}"
                        )
                        self.websocket.send(
                            json.dumps(
                                {"type": "rpc_error", "msg_id": msg_id, "error": error}
                            )
                        )
                    else:
                        print(
                            f"DEBUG: Client {self.identity} sending RPC response: {result}"
                        )
                        self.websocket.send(
                            json.dumps(
                                {
                                    "type": "rpc_response",
                                    "msg_id": msg_id,
                                    "result": result,
                                }
                            )
                        )

            elif msg_type == "rpc_response":
                # Handle RPC response
                msg_id = data.get("msg_id")
                result = data.get("result")
                print(
                    f"DEBUG: Client {self.identity} received RPC response for msg_id {msg_id}: {result}"
                )
                if msg_id in self.rpc_responses:
                    self.rpc_responses[msg_id] = result
                else:
                    print(f"DEBUG: No pending RPC request found for msg_id {msg_id}")

            elif msg_type == "rpc_error":
                # Handle RPC error
                msg_id = data.get("msg_id")
                error = data.get("error", "Unknown RPC error")
                print(
                    f"DEBUG: Client {self.identity} received RPC error for msg_id {msg_id}: {error}"
                )
                if msg_id in self.rpc_responses:
                    self.rpc_responses[msg_id] = {"error": error}
                else:
                    print(f"DEBUG: No pending RPC request found for msg_id {msg_id}")

            elif msg_type == "vip":
                # Handle VIP messages
                message = data.get("message", {})
                subsystem = message.get("subsystem")

                if subsystem == "rpc":
                    # This is an RPC request via VIP
                    self._handle_vip_rpc_request(message)
                elif subsystem == "rpc_response":
                    # This is an RPC response via VIP
                    msg_id = message.get("msg_id")
                    args = message.get("args", [])
                    if msg_id in self.rpc_responses and args:
                        self.rpc_responses[msg_id] = args[
                            0
                        ]  # Assuming first arg is result

        except Exception as e:
            print(f"Error processing message in client {self.identity}: {e}")
            import traceback

            traceback.print_exc()

    def _handle_vip_rpc_request(self, message):
        """Handle an incoming RPC request via VIP."""
        peer = message.get("user", "")  # Sender identity
        msg_id = message.get("msg_id", "")
        args = message.get("args", [])

        print(f"DEBUG: Client {self.identity} received VIP RPC request: {message}")

        if len(args) >= 2:
            method_name = args[0]
            method_args = args[1:]

            # Parse method name for potential remote calls
            parts = method_name.split(".")
            if len(parts) > 1:
                # This is a remote call to another client
                target = parts[0]
                actual_method = ".".join(parts[1:])

                # Forward the call to the target client
                try:
                    result = self.rpc_call(target, actual_method, *method_args)

                    # Send response back via VIP
                    self.send_vip_message(
                        peer=peer, subsystem="rpc_response", args=[result, msg_id]
                    )
                except Exception as e:
                    # Send error back via VIP
                    self.send_vip_message(
                        peer=peer, subsystem="rpc_error", args=[str(e), msg_id]
                    )
            else:
                # This is a local method call
                result = None
                error = None

                if method_name in self.exported_rpc_methods:
                    try:
                        method = self.exported_rpc_methods[method_name]
                        result = method(*method_args)
                    except Exception as e:
                        error = str(e)
                else:
                    error = f"Method {method_name} not found or not exported"

                # Send response via VIP
                if error:
                    self.send_vip_message(
                        peer=peer, subsystem="rpc_error", args=[error, msg_id]
                    )
                else:
                    self.send_vip_message(
                        peer=peer, subsystem="rpc_response", args=[result, msg_id]
                    )

    def _on_error(self, ws, error):
        """Callback when an error occurs."""
        print(f"Client {self.identity} error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Callback when the connection is closed."""
        self.connected = False
        print(
            f"Client {self.identity} connection closed: {close_status_code} {close_msg}"
        )

    def subscribe_prefix(self, prefix: str, callback: Callable | None = None):
        """Subscribe to a topic prefix."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        subscription_id = str(uuid.uuid4())
        self.subscriptions[prefix] = callback or (
            lambda msg: print(f"Subscription callback for {prefix}: {msg}")
        )

        self.websocket.send(
            json.dumps({"type": "subscribe", "prefix": prefix, "id": subscription_id})
        )

        print(f"Client {self.identity} subscribed to prefix: {prefix}")
        return subscription_id

    def subscribe_pattern(self, pattern: str, callback: Callable | None = None):
        """Subscribe to a topic pattern."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        subscription_id = str(uuid.uuid4())
        # Note: We're using the pattern as the key here
        self.subscriptions[pattern] = callback or (
            lambda msg: print(f"Subscription callback for {pattern}: {msg}")
        )

        self.websocket.send(
            json.dumps({"type": "subscribe", "pattern": pattern, "id": subscription_id})
        )

        print(f"Client {self.identity} subscribed to pattern: {pattern}")
        return subscription_id

    def publish(
        self, topic: str, message: Any, headers: dict | None = None, bus: str = ""
    ):
        """Publish a message to a topic."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        if headers is None:
            headers = {}

        self.websocket.send(
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

    def send_vip_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        if args is None:
            args = []

        msg_id = str(uuid.uuid4())

        self.websocket.send(
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

    def export_rpc_method(self, method_name: str, method: Callable):
        """Export an RPC method that can be called remotely."""
        self.exported_rpc_methods[method_name] = method
        print(f"Client {self.identity} exported RPC method: {method_name}")

    def rpc_call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another client."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        msg_id = str(uuid.uuid4())
        self.rpc_responses[msg_id] = None

        print(
            f"DEBUG: Client {self.identity} making RPC call to {peer}.{method} with msg_id {msg_id}"
        )

        self.websocket.send(
            json.dumps(
                {
                    "type": "rpc",
                    "peer": peer,
                    "method": method,
                    "args": args,
                    "kwargs": kwargs,
                    "msg_id": msg_id,
                }
            )
        )

        print(
            f"Client {self.identity} sent RPC call to {peer}: method={method}, args={args}, kwargs={kwargs}"
        )

        # Wait for the response with a timeout
        timeout = 10  # seconds
        start_time = gevent.time.time()
        while self.rpc_responses.get(msg_id) is None:
            print(
                f"DEBUG: Client {self.identity} waiting for response to msg_id {msg_id}"
            )
            gevent.sleep(0.5)  # Longer sleep for more readable debug output
            if gevent.time.time() - start_time > timeout:
                print(
                    f"DEBUG: Client {self.identity} RPC call timed out for msg_id {msg_id}"
                )
                del self.rpc_responses[msg_id]
                raise TimeoutError(f"RPC call timed out: {method}")

        # Get and remove the response
        result = self.rpc_responses.pop(msg_id)
        print(
            f"DEBUG: Client {self.identity} received final result for msg_id {msg_id}: {result}"
        )

        # Check if there was an error
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"RPC error: {result['error']}")

        return result

    def vip_rpc_call(self, peer: str, method: str, *args):
        """Make an RPC call to another client using VIP messaging."""
        if not self.connected:
            raise ConnectionError("Client not connected")

        msg_id = str(uuid.uuid4())
        self.rpc_responses[msg_id] = None

        # Send the RPC request via VIP
        self.send_vip_message(peer=peer, subsystem="rpc", args=[method, *args, msg_id])

        print(
            f"Client {self.identity} sent VIP RPC call to {peer}: method={method}, args={args}"
        )

        # Wait for the response with a timeout
        timeout = 10  # seconds
        start_time = gevent.time.time()
        while self.rpc_responses.get(msg_id) is None:
            gevent.sleep(0.1)
            if gevent.time.time() - start_time > timeout:
                del self.rpc_responses[msg_id]
                raise TimeoutError(f"VIP RPC call timed out: {method}")

        # Get and remove the response
        result = self.rpc_responses.pop(msg_id)

        # Check if there was an error
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"VIP RPC error: {result['error']}")

        return result

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


def run_rpc_test():
    """Test RPC functionality between clients."""
    # Create test clients
    server = GeventMessageBusTestClient("server")
    client = GeventMessageBusTestClient("client")

    # Connect clients
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
    """Test multi-hop RPC functionality between clients."""
    # Create test clients
    client1 = GeventMessageBusTestClient("client1")
    client2 = GeventMessageBusTestClient("client2")

    try:
        # Connect clients
        client1.connect()
        client2.connect()

        # Wait for connections to be established
        gevent.sleep(2)  # Give more time for connections

        # Export RPC methods
        client1.export_rpc_method("ping", lambda: "pong from client1")
        client2.export_rpc_method("ping", lambda: "pong from client2")

        # Wait for methods to be registered
        gevent.sleep(1)

        print("\nTesting basic RPC calls:")

        # Direct call from client1 to client2
        print("\nDirect call from client1 to client2:")
        result = client1.rpc_call("client2", "ping")
        print(f"  client1 -> client2.ping() = {result}")

        # Direct call from client2 to client1
        print("\nDirect call from client2 to client1:")
        result = client2.rpc_call("client1", "ping")
        print(f"  client2 -> client1.ping() = {result}")

    except Exception as e:
        print(f"Error in multi-hop RPC test: {e}")
    finally:
        # Clean up
        client1.disconnect()
        client2.disconnect()


if __name__ == "__main__":
    # Run the test functions
    print("=== Running Publisher-Subscriber Test ===")
    run_publisher_subscriber_test()

    print("\n=== Running VIP Message Test ===")
    run_vip_message_test()

    print("\n=== Running RPC Test ===")
    run_rpc_test()

    print("\n=== Running Multi-Hop RPC Test ===")
    run_multi_hop_rpc_test()
