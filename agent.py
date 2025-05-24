# agent.py

import gevent
from gevent import monkey
# Patch standard library to work with gevent
monkey.patch_all()

import json
import uuid
import websocket
from typing import Dict, Any, Optional, Callable
import ssl


class Agent:
    """A gevent-based agent that connects to the VOLTTRON MessageBus."""
    
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
            on_open=self._on_open
        )
        
        # Start the WebSocket connection in a separate greenlet
        self._listener_greenlet = gevent.spawn(
            self.websocket.run_forever, 
            sslopt={"cert_reqs": ssl.CERT_NONE}  # Allow self-signed certs if needed
        )
        
        # Wait for the connection to be established
        timeout = 5
        start_time = gevent.time.time()
        while not self.connected:
            gevent.sleep(0.1)
            if gevent.time.time() - start_time > timeout:
                raise ConnectionError(f"Connection timeout for agent {self.identity}")
        
        print(f"Agent {self.identity} connected")
    
    def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            self.websocket.close()
            # Wait for the close to complete
            if self._listener_greenlet:
                self._listener_greenlet.join(timeout=1)
            self.connected = False
            print(f"Agent {self.identity} disconnected")
    
    def _on_open(self, ws):
        """Callback when WebSocket connection is opened."""
        self.connected = True
        print(f"DEBUG: Agent {self.identity} websocket connection opened")
    
    def _on_message(self, ws, message):
        """Callback when a message is received."""
        try:
            data = json.loads(message)
            self.received_messages.append(data)
            print(f"Agent {self.identity} received: {data}")
            
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
                print(f"DEBUG: Agent {self.identity} received RPC request: {data}")
                sender = data.get("sender")
                method_name = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                msg_id = data.get("msg_id")
                
                # Parse method name for potential remote calls
                parts = method_name.split(".")
                if len(parts) > 1:
                    # This is a remote call to another agent
                    target = parts[0]
                    actual_method = ".".join(parts[1:])
                    print(f"DEBUG: Remote call detected: {target}.{actual_method}")
                    
                    # Forward the call to the target agent
                    try:
                        result = self.rpc_call(target, actual_method, *args, **kwargs)
                        
                        # Send response back to original sender
                        print(f"DEBUG: Agent {self.identity} sending remote call response: {result}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_response",
                            "msg_id": msg_id,
                            "result": result
                        }))
                    except Exception as e:
                        # Send error back to original sender
                        error_msg = f"Remote call error: {str(e)}"
                        print(f"DEBUG: {error_msg}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_error",
                            "msg_id": msg_id,
                            "error": error_msg
                        }))
                else:
                    # This is a local method call
                    result = None
                    error = None
                    
                    if method_name in self.exported_rpc_methods:
                        try:
                            method = self.exported_rpc_methods[method_name]
                            print(f"DEBUG: Agent {self.identity} executing method {method_name}")
                            result = method(*args, **kwargs)
                            print(f"DEBUG: Agent {self.identity} method {method_name} result: {result}")
                        except Exception as e:
                            error = str(e)
                            print(f"DEBUG: Agent {self.identity} method {method_name} error: {error}")
                    else:
                        error = f"Method {method_name} not found or not exported"
                        print(f"DEBUG: {error}")
                    
                    # Send response
                    if error:
                        print(f"DEBUG: Agent {self.identity} sending RPC error response: {error}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_error",
                            "msg_id": msg_id,
                            "error": error
                        }))
                    else:
                        print(f"DEBUG: Agent {self.identity} sending RPC response: {result}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_response",
                            "msg_id": msg_id,
                            "result": result
                        }))
                
            elif msg_type == "rpc_response":
                # Handle RPC response
                msg_id = data.get("msg_id")
                result = data.get("result")
                print(f"DEBUG: Agent {self.identity} received RPC response for msg_id {msg_id}: {result}")
                if msg_id in self.rpc_responses:
                    self.rpc_responses[msg_id] = result
                else:
                    print(f"DEBUG: No pending RPC request found for msg_id {msg_id}")
                
            elif msg_type == "rpc_error":
                # Handle RPC error
                msg_id = data.get("msg_id")
                error = data.get("error", "Unknown RPC error")
                print(f"DEBUG: Agent {self.identity} received RPC error for msg_id {msg_id}: {error}")
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
                        self.rpc_responses[msg_id] = args[0]  # Assuming first arg is result
            
        except Exception as e:
            print(f"Error processing message in agent {self.identity}: {e}")
            import traceback
            traceback.print_exc()
    
    def _handle_vip_rpc_request(self, message):
        """Handle an incoming RPC request via VIP."""
        peer = message.get("user", "")  # Sender identity
        msg_id = message.get("msg_id", "")
        args = message.get("args", [])
        
        print(f"DEBUG: Agent {self.identity} received VIP RPC request: {message}")
        
        if len(args) >= 2:
            method_name = args[0]
            method_args = args[1:]
            
            # Parse method name for potential remote calls
            parts = method_name.split(".")
            if len(parts) > 1:
                # This is a remote call to another agent
                target = parts[0]
                actual_method = ".".join(parts[1:])
                
                # Forward the call to the target agent
                try:
                    result = self.rpc_call(target, actual_method, *method_args)
                    
                    # Send response back via VIP
                    self.send_vip_message(
                        peer=peer, 
                        subsystem="rpc_response",
                        args=[result, msg_id]
                    )
                except Exception as e:
                    # Send error back via VIP
                    self.send_vip_message(
                        peer=peer, 
                        subsystem="rpc_error",
                        args=[str(e), msg_id]
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
                        peer=peer, 
                        subsystem="rpc_error",
                        args=[error, msg_id]
                    )
                else:
                    self.send_vip_message(
                        peer=peer, 
                        subsystem="rpc_response",
                        args=[result, msg_id]
                    )
    
    def _on_error(self, ws, error):
        """Callback when an error occurs."""
        print(f"Agent {self.identity} error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Callback when the connection is closed."""
        self.connected = False
        print(f"Agent {self.identity} connection closed: {close_status_code} {close_msg}")
    
    def subscribe_prefix(self, prefix: str, callback: Optional[Callable] = None):
        """Subscribe to a topic prefix."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
        subscription_id = str(uuid.uuid4())
        self.subscriptions[prefix] = callback or (lambda msg: print(f"Subscription callback for {prefix}: {msg}"))
        
        self.websocket.send(json.dumps({
            "type": "subscribe",
            "prefix": prefix,
            "id": subscription_id
        }))
        
        print(f"Agent {self.identity} subscribed to prefix: {prefix}")
        return subscription_id
    
    def subscribe_pattern(self, pattern: str, callback: Optional[Callable] = None):
        """Subscribe to a topic pattern."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
        subscription_id = str(uuid.uuid4())
        # Note: We're using the pattern as the key here
        self.subscriptions[pattern] = callback or (lambda msg: print(f"Subscription callback for {pattern}: {msg}"))
        
        self.websocket.send(json.dumps({
            "type": "subscribe",
            "pattern": pattern,
            "id": subscription_id
        }))
        
        print(f"Agent {self.identity} subscribed to pattern: {pattern}")
        return subscription_id
    
    def publish(self, topic: str, message: Any, headers: Optional[Dict] = None, bus: str = ""):
        """Publish a message to a topic."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
        if headers is None:
            headers = {}
        
        self.websocket.send(json.dumps({
            "type": "publish",
            "bus": bus,
            "topic": topic,
            "headers": headers,
            "message": message
        }))
        
        print(f"Agent {self.identity} published to {topic}: {message}")
    
    def send_vip_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
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
        
        print(f"Agent {self.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}")
        return msg_id
    
    def export_rpc_method(self, method_name: str, method: Callable):
        """Export an RPC method that can be called remotely."""
        self.exported_rpc_methods[method_name] = method
        print(f"Agent {self.identity} exported RPC method: {method_name}")
    
    def rpc_call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another agent."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
        msg_id = str(uuid.uuid4())
        self.rpc_responses[msg_id] = None
        
        print(f"DEBUG: Agent {self.identity} making RPC call to {peer}.{method} with msg_id {msg_id}")
        
        self.websocket.send(json.dumps({
            "type": "rpc",
            "peer": peer,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "msg_id": msg_id
        }))
        
        print(f"Agent {self.identity} sent RPC call to {peer}: method={method}, args={args}, kwargs={kwargs}")
        
        # Wait for the response with a timeout
        timeout = 10  # seconds
        start_time = gevent.time.time()
        while self.rpc_responses.get(msg_id) is None:
            print(f"DEBUG: Agent {self.identity} waiting for response to msg_id {msg_id}")
            gevent.sleep(0.5)  # Longer sleep for more readable debug output
            if gevent.time.time() - start_time > timeout:
                print(f"DEBUG: Agent {self.identity} RPC call timed out for msg_id {msg_id}")
                del self.rpc_responses[msg_id]
                raise TimeoutError(f"RPC call timed out: {method}")
        
        # Get and remove the response
        result = self.rpc_responses.pop(msg_id)
        print(f"DEBUG: Agent {self.identity} received final result for msg_id {msg_id}: {result}")
        
        # Check if there was an error
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"RPC error: {result['error']}")
            
        return result
    
    def vip_rpc_call(self, peer: str, method: str, *args):
        """Make an RPC call to another agent using VIP messaging."""
        if not self.connected:
            raise ConnectionError("Agent not connected")
        
        msg_id = str(uuid.uuid4())
        self.rpc_responses[msg_id] = None
        
        # Send the RPC request via VIP
        self.send_vip_message(
            peer=peer, 
            subsystem="rpc",
            args=[method, *args, msg_id]
        )
        
        print(f"Agent {self.identity} sent VIP RPC call to {peer}: method={method}, args={args}")
        
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