# fastapi_message_bus.py

import asyncio
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Callable, Set, Pattern, Optional, Any, Tuple

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from starlette.websockets import WebSocketState


class JSONSerializable:
    """Base class for JSON serializable objects."""
    def __json__(self):
        return self.__dict__


@dataclass(frozen=True, kw_only=True)
class Credentials(JSONSerializable):
    """Credentials for authentication."""
    identity: str

    @staticmethod
    def create(*, identity: str) -> 'Credentials':
        return Credentials(identity=identity)


class Message:
    """Message object for VIP communication."""
    def __init__(self, **kwargs):
        self.__dict__ = kwargs

    def __repr__(self):
        attrs = ", ".join("%r: %r" % (
            name,
            [x for x in value] if isinstance(value, (list, tuple)) else value,
        ) for name, value in self.__dict__.items())
        return "%s(**{%s})" % (self.__class__.__name__, attrs)

    @staticmethod
    def create_message(*,
                       peer: str,
                       user: str,
                       subsystem: str,
                       msg_id: str,
                       args: list = None) -> 'Message':
        if args is None:
            args = []
        return Message(peer=peer, subsystem=subsystem, msg_id=msg_id, user=user, args=args)


class MessageBusStopHandler(ABC):
    """Handler for message bus shutdown events."""
    @abstractmethod
    def message_bus_shutdown(self):
        """Handle message bus shutdown."""
        pass


class MessageBus(ABC):
    """Abstract base class for message bus implementations."""
    # This should be set so it is called for the main
    # program clean up when either the `stop` method is
    # called.
    _stop_handler: MessageBusStopHandler

    @abstractmethod
    def start(self):
        """Start the message bus."""
        pass

    @abstractmethod
    def stop(self):
        """Stop the message bus."""
        pass

    def set_stop_handler(self, value: MessageBusStopHandler):
        """Set the stop handler for the message bus."""
        self._stop_handler = value

    def get_stop_handler(self) -> MessageBusStopHandler | None:
        """Get the stop handler for the message bus."""
        return self._stop_handler

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the message bus is running."""
        pass

    @abstractmethod
    def send_vip_message(self, message: Message):
        """Send a VIP message."""
        pass

    @abstractmethod
    def receive_vip_message(self) -> Message:
        """Receive a VIP message."""
        pass


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


class FastAPIMessageBus(MessageBus):
    """FastAPI implementation of the MessageBus."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.app = FastAPI(title="VOLTTRON Modular MessageBus")
        self.host = host
        self.port = port
        self.running = False
        self.manager = ConnectionManager()
        self._stop_handler = None
        self.server = None
        self.setup_routes()
        self.message_queue = asyncio.Queue()
    
    def setup_routes(self):
        """Set up the FastAPI routes."""
        
        @self.app.websocket("/ws/{identity}")
        async def websocket_endpoint(websocket: WebSocket, identity: str):
            # In a production environment, we'd validate the credentials here
            await self.manager.connect(websocket, identity)
            try:
                while True:
                    data = await websocket.receive_json()
                    
                    print(f"DEBUG: Received data from {identity}: {data}")
                    
                    # Process the incoming message based on its type
                    if "type" not in data:
                        continue
                    
                    if data["type"] == "vip":
                        # Handle VIP message
                        message_data = data["message"]
                        message = Message(**message_data)
                        await self.message_queue.put(message)
                        
                        # If this is an RPC, handle it
                        if hasattr(message, "subsystem"):
                            if message.subsystem == "rpc":
                                # Forward the RPC message to the target peer
                                if hasattr(message, "peer") and message.peer in self.manager.active_connections:
                                    print(f"DEBUG: Forwarding VIP RPC message to {message.peer}")
                                    await self.manager.send_message(message.peer, {
                                        "type": "vip",
                                        "message": message.__dict__
                                    })
                            elif message.subsystem == "rpc_response":
                                # Handle RPC response
                                if hasattr(message, "msg_id"):
                                    print(f"DEBUG: Received VIP RPC response for msg_id {message.msg_id}")
                                    # Set the result for the waiting future
                                    self.manager.set_rpc_response(message.msg_id, message.args[0] if hasattr(message, "args") and message.args else None)
                                    
                                    # Forward the response to the original requester
                                    if hasattr(message, "peer") and message.peer in self.manager.active_connections:
                                        await self.manager.send_message(message.peer, {
                                            "type": "vip",
                                            "message": message.__dict__
                                        })
                    
                    elif data["type"] == "subscribe":
                        # Handle subscription
                        if "prefix" in data:
                            self.manager.add_prefix_subscription(
                                identity, 
                                data["prefix"], 
                                lambda peer, sender, bus, topic, headers, message: 
                                    asyncio.create_task(self.manager.send_message(
                                        identity, 
                                        {
                                            "type": "pubsub",
                                            "peer": peer,
                                            "sender": sender,
                                            "bus": bus,
                                            "topic": topic,
                                            "headers": headers,
                                            "message": message
                                        }
                                    ))
                            )
                        elif "pattern" in data:
                            self.manager.add_regex_subscription(
                                identity, 
                                data["pattern"],
                                lambda peer, sender, bus, topic, headers, message: 
                                    asyncio.create_task(self.manager.send_message(
                                        identity, 
                                        {
                                            "type": "pubsub",
                                            "peer": peer,
                                            "sender": sender,
                                            "bus": bus,
                                            "topic": topic,
                                            "headers": headers,
                                            "message": message
                                        }
                                    ))
                            )
                    
                    elif data["type"] == "publish":
                        # Handle publish
                        if all(k in data for k in ["bus", "topic", "headers", "message"]):
                            await self.manager.publish(
                                data["bus"],
                                data["topic"],
                                data["headers"],
                                data["message"],
                                identity
                            )
                    
                    elif data["type"] == "rpc":
                        # Handle direct RPC calls
                        if all(k in data for k in ["peer", "method", "msg_id"]):
                            peer = data["peer"]
                            method = data["method"]
                            args = data.get("args", [])
                            kwargs = data.get("kwargs", {})
                            msg_id = data["msg_id"]
                            
                            await self.manager.handle_rpc(
                                sender=identity,
                                peer=peer,
                                method=method,
                                args=args,
                                kwargs=kwargs,
                                msg_id=msg_id
                            )
                    
                    elif data["type"] == "rpc_response":
                        # Handle RPC response messages
                        if "msg_id" in data and "result" in data:
                            msg_id = data["msg_id"]
                            result = data["result"]
                            print(f"DEBUG: Setting RPC response for msg_id {msg_id}: {result}")
                            self.manager.set_rpc_response(msg_id, result)
                    
                    elif data["type"] == "rpc_error":
                        # Handle RPC error messages
                        if "msg_id" in data and "error" in data:
                            msg_id = data["msg_id"]
                            error = data["error"]
                            print(f"DEBUG: Setting RPC error for msg_id {msg_id}: {error}")
                            self.manager.set_rpc_response(msg_id, {"error": error})
            
            except WebSocketDisconnect:
                print(f"DEBUG: WebSocket disconnect for {identity}")
                self.manager.disconnect(identity)
            except Exception as e:
                print(f"Error in websocket connection for {identity}: {e}")
                self.manager.disconnect(identity)
    
    def start(self):
        """Start the message bus."""
        if not self.running:
            # Start the uvicorn server in a separate thread
            import threading
            self.server_thread = threading.Thread(
                target=uvicorn.run,
                kwargs={
                    "app": self.app,
                    "host": self.host,
                    "port": self.port,
                }
            )
            self.server_thread.daemon = True
            self.server_thread.start()
            self.running = True
            print(f"DEBUG: MessageBus started on {self.host}:{self.port}")
    
    def stop(self):
        """Stop the message bus."""
        if self.running:
            self.running = False
            if self._stop_handler:
                self._stop_handler.message_bus_shutdown()
            print("DEBUG: MessageBus stopped")
    
    def is_running(self) -> bool:
        """Check if the message bus is running."""
        return self.running
    
    def send_vip_message(self, message: Message):
        """Send a VIP message."""
        asyncio.create_task(self._send_vip_message_async(message))
    
    async def _send_vip_message_async(self, message: Message):
        """Async implementation of send_vip_message."""
        if hasattr(message, "peer") and message.peer in self.manager.active_connections:
            await self.manager.send_message(message.peer, {
                "type": "vip",
                "message": message.__dict__
            })
    
    def receive_vip_message(self) -> Message:
        """Receive a VIP message synchronously."""
        # This is a blocking call, which isn't ideal in an async context
        # In a real implementation, you might want to consider a different approach
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.message_queue.get())


# Example usage
if __name__ == "__main__":
    class SimpleStopHandler(MessageBusStopHandler):
        def message_bus_shutdown(self):
            print("Message bus is shutting down")
    
    # Create and start the message bus
    message_bus = FastAPIMessageBus(host="127.0.0.1", port=8000)
    message_bus.set_stop_handler(SimpleStopHandler())
    message_bus.start()
    
    try:
        # Keep the main thread alive
        import time
        while message_bus.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        message_bus.stop()