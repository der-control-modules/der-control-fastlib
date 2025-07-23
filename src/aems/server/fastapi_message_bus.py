# fastapi_message_bus.py

import asyncio
import os
import threading
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request

from aems.server.models import MessageBus, Message, MessageBusStopHandler
from aems.server.connection_manager import ConnectionManager
from aems.server.config_store import ConfigStore


class FastAPIMessageBus(MessageBus):
    """FastAPI implementation of the MessageBus."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8000, config_store_dir: str = None,
                 reload: bool = False, reload_dirs: list = None, reload_delay: float = 0.25):
        self.app = FastAPI(title="AEMS MessageBus")
        self.host = host
        self.port = port
        self.running = False
        self.manager = ConnectionManager()
        self._stop_handler = None
        self.server = None

        self.reload = reload
        self.reload_dirs = reload_dirs or ["./"]
        self.reload_delay = reload_delay
        self.setup_routes()
        
        self.config_store = ConfigStore(config_store_dir)
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

        @self.app.get("/config-store/list")
        async def list_configs(agent_id: Optional[str] = None):
            """List all available configurations."""
            configs = self.config_store.list_configs(agent_id)
            return {"status": "success", "data": configs}

        @self.app.get("/config-store/{agent_id}/{config_name}")
        async def get_config(agent_id: str, config_name: str, raw: bool = False):
            """Retrieve a configuration for an agent."""
            config = self.config_store.retrieve(agent_id, config_name, raw)
            if config is None:
                raise HTTPException(status_code=404, detail=f"Config {config_name} not found for agent {agent_id}")
            return {"status": "success", "data": config}

        @self.app.put("/config-store/{agent_id}/{config_name}")
        async def store_config(agent_id: str, config_name: str, request: Request):
            """Store a configuration for an agent."""
            try:
                content_type = request.headers.get("Content-Type", "application/json")
                
                if "json" in content_type:
                    # Process as JSON
                    config_data = await request.json()
                    success = self.config_store.store(agent_id, config_name, config_data, "json")
                elif "csv" in content_type:
                    # Process as CSV
                    csv_content = await request.body()
                    csv_text = csv_content.decode('utf-8')
                    success = self.config_store.store(agent_id, config_name, csv_text, "csv")
                else:
                    # Default to JSON
                    config_data = await request.json()
                    success = self.config_store.store(agent_id, config_name, config_data, "json")
                
                if success:
                    # Notify the agent of the config update if it's connected
                    if agent_id in self.manager.active_connections:
                        await self.manager.send_message(agent_id, {
                            "type": "config_update",
                            "config_name": config_name
                        })
                    return {"status": "success"}
                else:
                    raise HTTPException(status_code=500, detail="Failed to store configuration")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid config data: {str(e)}")

        @self.app.delete("/config-store/{agent_id}/{config_name}")
        async def delete_config(agent_id: str, config_name: str):
            """Delete a configuration for an agent."""
            success = self.config_store.delete_config(agent_id, config_name)
            if success:
                # Notify the agent of the config deletion if it's connected
                if agent_id in self.manager.active_connections:
                    await self.manager.send_message(agent_id, {
                        "type": "config_delete",
                        "config_name": config_name
                    })
                return {"status": "success"}
            else:
                raise HTTPException(status_code=404, detail=f"Config {config_name} not found for agent {agent_id}")
    
    def start(self):
        """Start the message bus."""
        self.running = True
        
        # Configure uvicorn with hot reload if enabled
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            reload=self.reload,
            reload_dirs=self.reload_dirs,
            reload_delay=self.reload_delay
        )
        
        # Create and start the server
        self.server = uvicorn.Server(config)
        
        # Run the server in a separate thread
        self._server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="FastAPIMessageBus-Server"
        )
        self._server_thread.start()
        
        print(f"FastAPIMessageBus running on http://{self.host}:{self.port}")
        
        # Initialize other components after server start
        if hasattr(self, '_init_after_start'):
            self._init_after_start()
    def _run_server(self):
        """Run the uvicorn server."""
        self.server.run()
        self.running = False

    def stop(self):
        """Stop the message bus."""
        if not self.running or self.server is None:
            return
        
        if self._stop_handler:
            self._stop_handler.message_bus_shutdown()

        print("Stopping FastAPIMessageBus...")
                
        # Signal the server to stop
        self.server.should_exit = True

        # Wait for the server thread to finish
        if hasattr(self, '_server_thread') and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)

        self.running = False
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

def start_server(host="127.0.0.1", port=8000, config_store_dir=None):
    """
    Start the AEMS message bus server.
    
    Args:
        host: Host address to bind to
        port: Port to listen on
        config_store_dir: Directory for the config store, defaults to VOLTTRON_HOME/aems_config_store
        
    Returns:
        The running server instance
    """
    # Use VOLTTRON_HOME for config_store_dir if not explicitly provided
    if config_store_dir is None:
        volttron_home = os.environ.get("VOLTTRON_HOME")
        if volttron_home:
            config_store_dir = os.path.join(volttron_home, "aems_config_store")
    
    # Create and start the server
    server = FastAPIMessageBus(host=host, port=port, config_store_dir=config_store_dir)
    server.start()
    print(f"AEMS message bus server started at {host}:{port}")
    print(f"Using config store directory: {server.config_store.base_dir}")
    
    return server


def _main():
    """Main entry point for running the server from command line."""
    import argparse
    import os
    
    parser = argparse.ArgumentParser(description="AEMS Message Bus Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument(
        "--volttron-home", 
        default=os.environ.get("VOLTTRON_HOME"),
        help="VOLTTRON_HOME directory"
    )
    parser.add_argument(
        "--config-dir", 
        help="Config store directory (defaults to VOLTTRON_HOME/aems_config_store)"
    )
    parser.add_argument(
        "--reload", action="store_true", default=False,
        help="Puts the server into debug mode and will reload the server when changes are made."
    )
    
    args = parser.parse_args()
    
    # Set VOLTTRON_HOME environment variable if provided
    if args.volttron_home:
        os.environ["VOLTTRON_HOME"] = args.volttron_home
    
    # Determine config store directory
    config_dir = args.config_dir
    if not config_dir and args.volttron_home:
        config_dir = os.path.join(args.volttron_home, "aems_config_store")
    
    # Start the server
    server = start_server(args.host, args.port, config_dir)
    
    try:
        # Keep the main thread alive
        import time
        while server.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping server...")
        server.stop()


if __name__ == "__main__":
   _main()
