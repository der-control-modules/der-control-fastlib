# agent.py

import gevent
from gevent import monkey
# Patch standard library to work with gevent
monkey.patch_all()

import json
import uuid
import websocket
from typing import Dict, Any, Optional, Callable, List
import ssl


class RPC:
    """RPC subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._exported_methods = {}
    
    def export(self, method_name: str, method: Callable):
        """Export an RPC method that can be called remotely."""
        self._exported_methods[method_name] = method
        print(f"Agent {self._agent.identity} exported RPC method: {method_name}")
    
    def call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another agent."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        msg_id = str(uuid.uuid4())
        self._agent.rpc_responses[msg_id] = None
        
        print(f"DEBUG: Agent {self._agent.identity} making RPC call to {peer}.{method} with msg_id {msg_id}")
        
        self._agent.websocket.send(json.dumps({
            "type": "rpc",
            "peer": peer,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "msg_id": msg_id
        }))
        
        print(f"Agent {self._agent.identity} sent RPC call to {peer}: method={method}, args={args}, kwargs={kwargs}")
        
        # Wait for the response with a timeout
        timeout = 10  # seconds
        start_time = gevent.time.time()
        while self._agent.rpc_responses.get(msg_id) is None:
            print(f"DEBUG: Agent {self._agent.identity} waiting for response to msg_id {msg_id}")
            gevent.sleep(0.5)  # Longer sleep for more readable debug output
            if gevent.time.time() - start_time > timeout:
                print(f"DEBUG: Agent {self._agent.identity} RPC call timed out for msg_id {msg_id}")
                del self._agent.rpc_responses[msg_id]
                raise TimeoutError(f"RPC call timed out: {method}")
        
        # Get and remove the response
        result = self._agent.rpc_responses.pop(msg_id)
        print(f"DEBUG: Agent {self._agent.identity} received final result for msg_id {msg_id}: {result}")
        
        # Check if there was an error
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"RPC error: {result['error']}")
            
        return result
    
    def get_exports(self):
        """Get all exported RPC methods."""
        return list(self._exported_methods.keys())
    
    def handle_request(self, sender: str, method_name: str, args: list, kwargs: dict, msg_id: str):
        """Handle an incoming RPC request."""
        # Parse method name for potential remote calls
        parts = method_name.split(".")
        if len(parts) > 1:
            # This is a remote call to another agent
            target = parts[0]
            actual_method = ".".join(parts[1:])
            print(f"DEBUG: Remote call detected: {target}.{actual_method}")
            
            # Forward the call to the target agent
            try:
                result = self.call(target, actual_method, *args, **kwargs)
                return result, None
            except Exception as e:
                error_msg = f"Remote call error: {str(e)}"
                return None, error_msg
        else:
            # This is a local method call
            if method_name in self._exported_methods:
                try:
                    method = self._exported_methods[method_name]
                    print(f"DEBUG: Agent {self._agent.identity} executing method {method_name}")
                    result = method(*args, **kwargs)
                    print(f"DEBUG: Agent {self._agent.identity} method {method_name} result: {result}")
                    return result, None
                except Exception as e:
                    error = str(e)
                    print(f"DEBUG: Agent {self._agent.identity} method {method_name} error: {error}")
                    return None, error
            else:
                error = f"Method {method_name} not found or not exported"
                print(f"DEBUG: {error}")
                return None, error


class PubSub:
    """PubSub subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._subscriptions = {}
    
    def publish(self, topic: str, message: Any, headers: Optional[Dict] = None, bus: str = ""):
        """Publish a message to a topic."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        if headers is None:
            headers = {}
        
        self._agent.websocket.send(json.dumps({
            "type": "publish",
            "bus": bus,
            "topic": topic,
            "headers": headers,
            "message": message
        }))
        
        print(f"Agent {self._agent.identity} published to {topic}: {message}")
    
    def subscribe(self, prefix: str, callback: Optional[Callable] = None):
        """Subscribe to a topic prefix."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        subscription_id = str(uuid.uuid4())
        self._subscriptions[prefix] = callback or (lambda msg: print(f"Subscription callback for {prefix}: {msg}"))
        
        self._agent.websocket.send(json.dumps({
            "type": "subscribe",
            "prefix": prefix,
            "id": subscription_id
        }))
        
        print(f"Agent {self._agent.identity} subscribed to prefix: {prefix}")
        return subscription_id
    
    def subscribe_regex(self, pattern: str, callback: Optional[Callable] = None):
        """Subscribe to a topic pattern."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        subscription_id = str(uuid.uuid4())
        # Note: We're using the pattern as the key here
        self._subscriptions[pattern] = callback or (lambda msg: print(f"Subscription callback for {pattern}: {msg}"))
        
        self._agent.websocket.send(json.dumps({
            "type": "subscribe",
            "pattern": pattern,
            "id": subscription_id
        }))
        
        print(f"Agent {self._agent.identity} subscribed to pattern: {pattern}")
        return subscription_id
    
    def get_subscriptions(self):
        """Get all active subscriptions."""
        return list(self._subscriptions.keys())
    
    def handle_message(self, data: Dict):
        """Handle an incoming pubsub message."""
        topic = data.get("topic", "")
        for prefix, callback in self._subscriptions.items():
            if topic.startswith(prefix) or prefix in ["*", "all"]:
                callback(data)
                return True
        return False


class Config:
    """Config subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._config = {}
    
    def set(self, key: str, value: Any):
        """Set a configuration value."""
        self._config[key] = value
    
    def get(self, key: str, default=None):
        """Get a configuration value."""
        return self._config.get(key, default)
    
    def list(self):
        """List all configuration keys."""
        return list(self._config.keys())


class VIP:
    """VIP subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self.rpc = RPC(agent)
        self.pubsub = PubSub(agent)
        self.config = Config(agent)
    
    def send_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        if args is None:
            args = []
        
        msg_id = str(uuid.uuid4())
        
        self._agent.websocket.send(json.dumps({
            "type": "vip",
            "message": {
                "peer": peer,
                "user": self._agent.identity,
                "subsystem": subsystem,
                "msg_id": msg_id,
                "args": args
            }
        }))
        
        print(f"Agent {self._agent.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}")
        return msg_id
    
    def ping(self, peer: str):
        """Ping another agent to check if it's alive."""
        try:
            result = self.rpc.call(peer, "ping")
            return result == "pong"
        except Exception:
            return False


class Core:
    """Core functionality for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
    
    def stop(self):
        """Stop the agent."""
        self._agent.disconnect()
    
    def identity(self):
        """Get the agent's identity."""
        return self._agent.identity
    
    def onstart(self, callback: Callable):
        """Register a callback to be executed when the agent starts."""
        self._agent.on_start_callbacks.append(callback)
    
    def onstop(self, callback: Callable):
        """Register a callback to be executed when the agent stops."""
        self._agent.on_stop_callbacks.append(callback)


class Agent:
    """A gevent-based agent that connects to the VOLTTRON MessageBus."""
    
    def __init__(self, identity: str, host: str = "127.0.0.1", port: int = 8000):
        self.identity = identity
        self.websocket_url = f"ws://{host}:{port}/ws/{identity}"
        self.websocket = None
        self.connected = False
        self.received_messages = []
        self._listener_greenlet = None
        self.rpc_responses = {}
        
        # Hierarchical structure
        self.vip = VIP(self)
        self.core = Core(self)
        
        # Callbacks
        self.on_start_callbacks = []
        self.on_stop_callbacks = []
    
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
        
        # Call onstart callbacks
        for callback in self.on_start_callbacks:
            try:
                callback()
            except Exception as e:
                print(f"Error in onstart callback: {e}")
    
    def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            # Call onstop callbacks
            for callback in self.on_stop_callbacks:
                try:
                    callback()
                except Exception as e:
                    print(f"Error in onstop callback: {e}")
            
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
            
            # Handle different message types
            msg_type = data.get("type")
            
            if msg_type == "pubsub":
                # Handle pubsub messages
                self.vip.pubsub.handle_message(data)
                        
            elif msg_type == "rpc_request":
                # Handle RPC request
                print(f"DEBUG: Agent {self.identity} received RPC request: {data}")
                sender = data.get("sender")
                method_name = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                msg_id = data.get("msg_id")
                
                # Process the RPC request
                result, error = self.vip.rpc.handle_request(sender, method_name, args, kwargs, msg_id)
                
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
            
            # Process the RPC request
            result, error = self.vip.rpc.handle_request(peer, method_name, method_args, {}, msg_id)
            
            # Send response via VIP
            if error:
                self.vip.send_message(
                    peer=peer, 
                    subsystem="rpc_error",
                    args=[error, msg_id]
                )
            else:
                self.vip.send_message(
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
    
    def get_received_messages(self):
        """Get all received messages."""
        return self.received_messages
    
    def clear_received_messages(self):
        """Clear the received messages list."""
        self.received_messages = []