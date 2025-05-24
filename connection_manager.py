# connection_manager.py

import asyncio
import re
from typing import Dict, List, Callable, Pattern, Any, Tuple

from fastapi import WebSocket
from starlette.websockets import WebSocketState

# Subscription callback type
SubscriptionCallback = Callable[[str, str, str, str, dict, Any], None]


class ConnectionManager:
    """Manages WebSocket connections for the MessageBus."""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.prefix_subscriptions: Dict[str, Dict[str, List[Tuple[str, SubscriptionCallback]]]] = {}
        self.regex_subscriptions: Dict[str, List[Tuple[Pattern, str, SubscriptionCallback]]] = {}
        self.rpc_responses: Dict[str, asyncio.Future] = {}
    
    async def connect(self, websocket: WebSocket, identity: str):
        """Connect a client to the message bus."""
        await websocket.accept()
        self.active_connections[identity] = websocket
        self.prefix_subscriptions[identity] = {}
        print(f"DEBUG: Client {identity} connected")
    
    def disconnect(self, identity: str):
        """Disconnect a client from the message bus."""
        if identity in self.active_connections:
            del self.active_connections[identity]
        if identity in self.prefix_subscriptions:
            del self.prefix_subscriptions[identity]
        # Remove any regex subscriptions for this identity
        self.regex_subscriptions = {
            identity_key: subscriptions 
            for identity_key, subscriptions in self.regex_subscriptions.items() 
            if identity_key != identity
        }
        print(f"DEBUG: Client {identity} disconnected")
    
    async def send_message(self, identity: str, message: dict):
        """Send a message to a specific client."""
        if identity in self.active_connections:
            websocket = self.active_connections[identity]
            if websocket.client_state != WebSocketState.DISCONNECTED:
                print(f"DEBUG: Sending message to {identity}: {message}")
                await websocket.send_json(message)
            else:
                print(f"DEBUG: Cannot send message to {identity}, websocket is disconnected")
        else:
            print(f"DEBUG: Cannot send message to {identity}, client not found")
    
    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        for identity, websocket in self.active_connections.items():
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.send_json(message)
    
    def add_prefix_subscription(self, identity: str, prefix: str, callback: SubscriptionCallback):
        """Add a prefix-based subscription for a client."""
        if identity not in self.prefix_subscriptions:
            self.prefix_subscriptions[identity] = {}
        
        if prefix not in self.prefix_subscriptions[identity]:
            self.prefix_subscriptions[identity][prefix] = []
        
        self.prefix_subscriptions[identity][prefix].append((identity, callback))
    
    def add_regex_subscription(self, identity: str, pattern: str, callback: SubscriptionCallback):
        """Add a regex-based subscription for a client."""
        if identity not in self.regex_subscriptions:
            self.regex_subscriptions[identity] = []
        
        compiled_pattern = re.compile(pattern)
        self.regex_subscriptions[identity].append((compiled_pattern, identity, callback))
    
    async def publish(self, bus: str, topic: str, headers: dict, message: Any, sender: str):
        """Publish a message to subscribers."""
        # Process prefix subscriptions
        for identity, prefixes in self.prefix_subscriptions.items():
            for prefix, callbacks in prefixes.items():
                if topic.startswith(prefix):
                    for peer, callback in callbacks:
                        try:
                            callback(peer, sender, bus, topic, headers, message)
                        except Exception as e:
                            print(f"Error in prefix subscription callback: {e}")
        
        # Process regex subscriptions
        for identity, patterns in self.regex_subscriptions.items():
            for pattern, peer, callback in patterns:
                if pattern.match(topic):
                    try:
                        callback(peer, sender, bus, topic, headers, message)
                    except Exception as e:
                        print(f"Error in regex subscription callback: {e}")
    
    def register_rpc_response_future(self, msg_id: str) -> asyncio.Future:
        """Register a future for an RPC response."""
        future = asyncio.get_event_loop().create_future()
        self.rpc_responses[msg_id] = future
        print(f"DEBUG: Registered RPC response future for msg_id {msg_id}")
        return future
    
    def set_rpc_response(self, msg_id: str, response: Any):
        """Set the result for an RPC response future."""
        if msg_id in self.rpc_responses:
            future = self.rpc_responses.pop(msg_id)
            if not future.done():
                print(f"DEBUG: Setting RPC response for msg_id {msg_id}: {response}")
                future.set_result(response)
            else:
                print(f"DEBUG: Future for msg_id {msg_id} was already done")
        else:
            print(f"DEBUG: No future found for msg_id {msg_id}")
    
    def clear_rpc_response(self, msg_id: str):
        """Clear an RPC response future."""
        if msg_id in self.rpc_responses:
            future = self.rpc_responses.pop(msg_id)
            if not future.done():
                future.cancel()
            print(f"DEBUG: Cleared RPC response future for msg_id {msg_id}")
    
    async def handle_rpc(self, sender: str, peer: str, method: str, args: list, kwargs: dict, msg_id: str):
        """Handle RPC request between clients."""
        print(f"DEBUG: RPC request from {sender} to {peer}: {method}({args}, {kwargs}) [msg_id: {msg_id}]")
        
        if peer not in self.active_connections:
            print(f"DEBUG: RPC target {peer} not found")
            await self.send_message(sender, {
                "type": "rpc_error",
                "msg_id": msg_id,
                "error": f"Peer {peer} not found"
            })
            return
            
        # Create a message for the RPC call
        rpc_message = {
            "type": "rpc_request",
            "sender": sender,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "msg_id": msg_id
        }
        
        # Register a future for the response
        future = self.register_rpc_response_future(msg_id)
        
        # Send to the target peer
        print(f"DEBUG: Sending RPC request to {peer}")
        await self.send_message(peer, rpc_message)
            
        # Wait for response with timeout
        try:
            print(f"DEBUG: Waiting for RPC response for msg_id {msg_id}")
            response = await asyncio.wait_for(future, 10.0)  # 10 second timeout
            print(f"DEBUG: Received RPC response for msg_id {msg_id}: {response}")
            await self.send_message(sender, {
                "type": "rpc_response",
                "msg_id": msg_id,
                "result": response
            })
        except asyncio.TimeoutError:
            print(f"DEBUG: RPC request timed out for msg_id {msg_id}")
            await self.send_message(sender, {
                "type": "rpc_error",
                "msg_id": msg_id,
                "error": "RPC request timed out"
            })
            self.clear_rpc_response(msg_id)