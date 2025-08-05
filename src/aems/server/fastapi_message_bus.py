# fastapi_message_bus.py

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional
import subprocess
import jwt
import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:
    # Python < 3.8
    from importlib_metadata import version, PackageNotFoundError

from aems.server.models import MessageBus, Message
from aems.server.connection_manager import ConnectionManager
from aems.server.config_store import ConfigStore

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)

def get_package_version():
    """Get the current package version."""
    try:
        # Try to get version from installed package
        return version("aems")
    except PackageNotFoundError:
        # Fallback to git if package not installed (development mode)
        try:
            result = subprocess.run(
                ["git", "tag", "-l", "--sort=-version:refname"],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(__file__),
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Get the most recent tag and strip 'v' prefix
                latest_tag = result.stdout.strip().split("\n")[0]
                return latest_tag.lstrip("v")
            return "unknown-dev"
        except (subprocess.SubprocessError, OSError):
            return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifespan."""
    # Startup
    await app.state.manager.startup()
    yield
    # Shutdown
    await app.state.manager.shutdown()


class FastAPIMessageBus(MessageBus):
    """FastAPI implementation of the MessageBus."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        config_store_dir: str = None,
        reload: bool = False,
        reload_dirs: list = None,
        reload_delay: float = 0.25,
    ):
        self.app = FastAPI(title="AEMS MessageBus", lifespan=lifespan)
        self.host = host
        self.port = port
        self.running = False
        self.manager = ConnectionManager()
        self._stop_handler = None
        self.server = None

        # Store manager in app state for lifespan access
        self.app.state.manager = self.manager

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

                    print(f"DEBUG: Received data from {identity}: {data['type']}")

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
                                if (
                                    hasattr(message, "peer")
                                    and message.peer in self.manager.active_connections
                                ):
                                    print(f"DEBUG: Forwarding VIP RPC message to {message.peer}")
                                    await self.manager.send_message(
                                        message.peer, {"type": "vip", "message": message.__dict__}
                                    )
                            elif message.subsystem == "rpc_response":
                                # Handle RPC response
                                if hasattr(message, "msg_id"):
                                    print(
                                        f"DEBUG: Received VIP RPC response for msg_id {message.msg_id}"
                                    )
                                    # Set the result for the waiting future
                                    self.manager.set_rpc_response(
                                        message.msg_id,
                                        (
                                            message.args[0]
                                            if hasattr(message, "args") and message.args
                                            else None
                                        ),
                                    )

                                    # Forward the response to the original requester
                                    if (
                                        hasattr(message, "peer")
                                        and message.peer in self.manager.active_connections
                                    ):
                                        await self.manager.send_message(
                                            message.peer,
                                            {"type": "vip", "message": message.__dict__},
                                        )

                    elif data["type"] == "subscribe":
                        # Handle subscription
                        if "prefix" in data:
                            self.manager.add_prefix_subscription(
                                identity,
                                data["prefix"],
                                lambda peer, sender, bus, topic, headers, message: asyncio.create_task(
                                    self.manager.send_message(
                                        identity,
                                        {
                                            "type": "pubsub",
                                            "peer": peer,
                                            "sender": sender,
                                            "bus": bus,
                                            "topic": topic,
                                            "headers": headers,
                                            "message": message,
                                        },
                                    )
                                ),
                            )
                        elif "pattern" in data:
                            self.manager.add_regex_subscription(
                                identity,
                                data["pattern"],
                                lambda peer, sender, bus, topic, headers, message: asyncio.create_task(
                                    self.manager.send_message(
                                        identity,
                                        {
                                            "type": "pubsub",
                                            "peer": peer,
                                            "sender": sender,
                                            "bus": bus,
                                            "topic": topic,
                                            "headers": headers,
                                            "message": message,
                                        },
                                    )
                                ),
                            )

                    elif data["type"] == "publish":
                        # Handle publish
                        if all(k in data for k in ["bus", "topic", "headers", "message"]):
                            await self.manager.publish(
                                data["bus"],
                                data["topic"],
                                data["headers"],
                                data["message"],
                                identity,
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
                                msg_id=msg_id,
                            )

                    elif data["type"] == "rpc_response":
                        # Handle RPC response messages
                        if "msg_id" in data and "result" in data:
                            msg_id = data["msg_id"]
                            result = data["result"]
                            print(f"DEBUG: Setting RPC response for msg_id {msg_id}: {result}")
                            self.manager.set_rpc_response(msg_id, result)

                    elif data["type"] == "rpc_error":
                        # Handle RPC error messages - use proper error handling
                        if "msg_id" in data and "error" in data:
                            msg_id = data["msg_id"]
                            error = data["error"]
                            print(f"DEBUG: Setting RPC error for msg_id {msg_id}: {error}")
                            self.manager.set_rpc_error(msg_id, error)

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
                raise HTTPException(
                    status_code=404, detail=f"Config {config_name} not found for agent {agent_id}"
                )
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
                    csv_text = csv_content.decode("utf-8")
                    success = self.config_store.store(agent_id, config_name, csv_text, "csv")
                else:
                    # Default to JSON
                    config_data = await request.json()
                    success = self.config_store.store(agent_id, config_name, config_data, "json")

                if success:
                    # Notify the agent of the config update if it's connected
                    if agent_id in self.manager.active_connections:
                        await self.manager.send_message(
                            agent_id, {"type": "config_update", "config_name": config_name}
                        )
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
                    await self.manager.send_message(
                        agent_id, {"type": "config_delete", "config_name": config_name}
                    )
                return {"status": "success"}
            else:
                raise HTTPException(
                    status_code=404, detail=f"Config {config_name} not found for agent {agent_id}"
                )

        @self.app.get("/version")
        async def get_version():
            """Get the current server version."""
            version_string = get_package_version()
            return {"version": version_string, "service": "aems-server", "status": "running"}

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "version": get_package_version(),
                "active_connections": len(self.manager.active_connections),
                "service": "aems-server",
            }

        @self.app.post("/authenticate")
        async def authenticate(request: Request):
            """Authenticate a user with username and password."""
            try:
                auth_data = None
                content_type = request.headers.get("content-type", "").lower()
                
                _log.debug(f"Content-Type: {content_type}")
                
                # Handle different content types
                if "application/x-www-form-urlencoded" in content_type:
                    # This is the case for requests.post with data parameter
                    form_data = await request.form()
                    auth_data = dict(form_data)
                    _log.debug(f"Parsed form data: {auth_data}")
                elif "application/json" in content_type:
                    # This is for JSON requests
                    auth_data = await request.json()
                    _log.debug(f"Parsed JSON data: {auth_data}")
                else:
                    # Try to determine the format by attempting to parse
                    try:
                        # First try form data (most common for requests.post with data=)
                        form_data = await request.form()
                        if form_data:
                            auth_data = dict(form_data)
                            _log.debug(f"Fallback parsed form data: {auth_data}")
                        else:
                            # If form data is empty, try JSON
                            body = await request.body()
                            if body:
                                import json
                                auth_data = json.loads(body.decode())
                                _log.debug(f"Fallback parsed JSON data: {auth_data}")
                    except Exception as parse_error:
                        _log.error(f"Failed to parse request data: {parse_error}")
                        raise HTTPException(
                            status_code=400,
                            detail=f"Unable to parse request data: {str(parse_error)}"
                        )

                if not auth_data:
                    raise HTTPException(
                        status_code=400,
                        detail="No data received in request"
                    )
                
                _log.debug(f"Final auth data: {auth_data}")
                
                # Validate required fields
                if "username" not in auth_data or "password" not in auth_data:
                    raise HTTPException(
                        status_code=400,
                        detail="Missing required fields: username and password"
                    )

                username = auth_data["username"]
                password = auth_data["password"]

                # TODO: Implement actual authentication logic here
                # For now, this is a placeholder that accepts any non-empty credentials
                if username and password:
                    # JWT configuration
                    secret_key = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
                    algorithm = "HS256"

                    # Current time
                    now = datetime.datetime.utcnow()

                    # Generate access token (expires in 1 hour)
                    access_token_payload = {
                        "sub": username,  # subject (user identifier)
                        "iat": now,  # issued at
                        "exp": now + datetime.timedelta(hours=1),  # expires
                        "type": "access"
                    }
                    access_token = jwt.encode(access_token_payload, secret_key, algorithm=algorithm)

                    # Generate refresh token (expires in 7 days)
                    refresh_token_payload = {
                        "sub": username,
                        "iat": now,
                        "exp": now + datetime.timedelta(days=7),
                        "type": "refresh"
                    }
                    refresh_token = jwt.encode(refresh_token_payload, secret_key, algorithm=algorithm)

                    return {
                        "status": "success",
                        "message": "Authentication successful",
                        "username": username,
                        "access_token": access_token,
                        "refresh_token": refresh_token
                    }
                else:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid credentials"
                    )

            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid request data: {str(e)}")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Authentication error: {str(e)}")

        @self.app.post("/gs")
        async def rpc_endpoint(request: Request):
            """Handle JSON-RPC 2.0 requests and route them to connected agents."""
            try:
                rpc_data = await request.json()
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON data: {str(e)}")

            # Validate JSON-RPC 2.0 format
            if "jsonrpc" not in rpc_data or rpc_data["jsonrpc"] != "2.0":
                raise HTTPException(
                    status_code=400,
                    detail="Invalid JSON-RPC format. Must include 'jsonrpc': '2.0'"
                )

            if "id" not in rpc_data:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required field: 'id' (agent identifier)"
                )

            if "method" not in rpc_data:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required field: 'method'"
                )

            agent_id = rpc_data["id"]
            method = rpc_data["method"]
            params = rpc_data.get("params", {})

            # Extract authentication and data from params
            authentication = params.get("authentication")
            data = params.get("data", {})

            # Check if the target agent is connected
            if agent_id not in self.manager.active_connections:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{agent_id}' is not connected"
                )

            # Generate a unique message ID for this RPC call
            import uuid
            import asyncio
            msg_id = str(uuid.uuid4())

            try:
                # Register a future for the RPC response
                future = self.manager.register_rpc_response_future(msg_id)

                # Create and send the RPC message directly
                rpc_message = {
                    "type": "rpc",
                    "method": method,
                    "args": [data] if data else [],
                    "kwargs": {"authentication": authentication} if authentication else {},
                    "msg_id": msg_id,
                }

                print(f"DEBUG: Sending HTTP RPC request to {agent_id}: {method}")
                await self.manager.send_message(agent_id, rpc_message)

                # Wait for the response (with timeout)
                try:
                    result = await asyncio.wait_for(
                        future,
                        timeout=30.0  # 30 second timeout
                    )

                    # Return JSON-RPC 2.0 success response
                    return {
                        "jsonrpc": "2.0",
                        "id": rpc_data.get("id"),
                        "result": result
                    }

                except asyncio.TimeoutError:
                    # Clean up the future
                    self.manager.clear_rpc_response(msg_id)
                    # Return JSON-RPC 2.0 error response for timeout
                    return {
                        "jsonrpc": "2.0",
                        "id": rpc_data.get("id"),
                        "error": {
                            "code": -32603,
                            "message": "Internal error: RPC call timed out"
                        }
                    }

            except Exception as rpc_error:
                # Clean up the future if it was created
                self.manager.clear_rpc_response(msg_id)
                # Return JSON-RPC 2.0 error response
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_data.get("id"),
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(rpc_error)}"
                    }
                }

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
            reload_delay=self.reload_delay,
        )

        # Create and start the server
        self.server = uvicorn.Server(config)

        # Run the server in a separate thread
        self._server_thread = threading.Thread(
            target=self._run_server, daemon=True, name="FastAPIMessageBus-Server"
        )
        self._server_thread.start()

        print(f"FastAPIMessageBus running on http://{self.host}:{self.port}")

        # Initialize other components after server start
        if hasattr(self, "_init_after_start"):
            self._init_after_start()

    def _run_server(self):
        """Run the uvicorn server."""
        try:
            # Since we're in a separate thread, we need to create our own event loop
            # This avoids conflicts with any existing event loop in the main thread
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(self.server.serve())
            finally:
                new_loop.close()
        except Exception as e:
            print(f"Error running server: {e}")
            raise
        finally:
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
        if hasattr(self, "_server_thread") and self._server_thread.is_alive():
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
            await self.manager.send_message(
                message.peer, {"type": "vip", "message": message.__dict__}
            )

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
    import sys

    parser = argparse.ArgumentParser(description="AEMS Message Bus Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument(
        "--volttron-home", default=os.environ.get("VOLTTRON_HOME"), help="VOLTTRON_HOME directory"
    )
    parser.add_argument(
        "--config-dir", help="Config store directory (defaults to VOLTTRON_HOME/aems_config_store)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Puts the server into debug mode and will reload the server when changes are made.",
    )

    args = parser.parse_args()

    # Set VOLTTRON_HOME environment variable if provided
    if args.volttron_home:
        os.environ["VOLTTRON_HOME"] = args.volttron_home

    # Determine config store directory
    config_dir = args.config_dir
    if not config_dir and args.volttron_home:
        config_dir = os.path.join(args.volttron_home, "aems_config_store")

    # Check if running under debugger
    def is_debugger_attached():
        """Check if a debugger is attached."""
        # Check for common debugger indicators
        if hasattr(sys, 'gettrace') and sys.gettrace() is not None:
            return True
        # Check for debugpy (VS Code debugger)
        if 'debugpy' in sys.modules:
            return True
        # Check for pdb
        if 'pdb' in sys.modules:
            return True
        return False

    if is_debugger_attached():
        print("Debugger detected - using direct uvicorn.run() for better debugging support")
        
        # Create the FastAPI app directly for uvicorn.run()
        server = FastAPIMessageBus(host=args.host, port=args.port, config_store_dir=config_dir, reload=args.reload)
        
        # Use uvicorn.run() directly for debugging
        uvicorn.run(
            server.app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="debug"
        )
    else:
        # Use the threaded approach for production
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
