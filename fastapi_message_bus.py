# fastapi_message_bus.py

import asyncio
import threading
from typing import Dict, Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from models import MessageBus, Message, MessageBusStopHandler
from connection_manager import ConnectionManager


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