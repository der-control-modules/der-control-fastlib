# fastapi_message_bus.py

import asyncio
import datetime
import logging
import os
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Union

import jwt
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    # Python < 3.8
    from importlib_metadata import PackageNotFoundError, version

from derhost.server.config_store import ConfigStore
from derhost.server.connection_manager import ConnectionManager
from derhost.server.models import Message, MessageBus


# Pydantic models for JSON-RPC 2.0 endpoint
class JsonRpcParams(BaseModel):
    authentication: Union[dict, str] | None = None
    data: dict | None = None
    args: list | None = None
    kwargs: dict | None = None


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str  # Agent identifier
    method: str
    params: JsonRpcParams | None = None


class JsonRpcError(BaseModel):
    code: int
    message: str


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    result: Any | None = None
    error: JsonRpcError | None = None


# Pydantic models for authentication endpoint
class AuthRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    status: str = "success"
    message: str
    username: str
    access_token: str
    refresh_token: str


class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support for different log levels and sections."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }

    # Section-specific colors
    SECTION_COLORS = {
        "timestamp": "\033[90m",  # Dark gray
        "level": "",  # Use level-specific color
        "module": "\033[34m",  # Blue
        "lineno": "\033[90m",  # Dark gray
        "message": "\033[0m",  # Reset to default
    }

    RESET = "\033[0m"  # Reset color
    BOLD = "\033[1m"  # Bold text

    def __init__(self, format_string=None, use_colors=None):
        if format_string is None:
            format_string = "%(asctime)s %(levelname)s %(module)s:%(lineno)d %(message)s"
        super().__init__(format_string)

        # Auto-detect color support if not explicitly set
        if use_colors is None:
            import sys

            self.use_colors = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        else:
            self.use_colors = use_colors

    def format(self, record):
        # Skip coloring if colors are disabled
        if not self.use_colors:
            return super().format(record)

        # Format the record first with the standard formatter
        formatted = super().format(record)

        # Apply colors to the formatted string
        level_color = self.COLORS.get(record.levelname, "")

        # Apply colors using string replacement on the formatted output
        # This avoids type conversion issues with format fields

        # Color the timestamp (first part before first space after the date/time)
        import re

        # Pattern to match timestamp at the beginning
        timestamp_pattern = r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
        formatted = re.sub(
            timestamp_pattern,
            f"{self.SECTION_COLORS['timestamp']}\\1{self.RESET}",
            formatted,
        )

        # Color the log level (looks for level names in the formatted string)
        level_pattern = f"({record.levelname})"
        formatted = re.sub(
            level_pattern,
            f"{level_color}{self.BOLD}\\1{self.RESET}",
            formatted,
            count=1,
        )

        # Color the module name (looks for module:lineno pattern)
        module_pattern = f"({record.module}):"
        formatted = re.sub(
            module_pattern,
            f"{self.SECTION_COLORS['module']}\\1{self.RESET}:",
            formatted,
            count=1,
        )

        # Color the line number (looks for :number pattern after module)
        lineno_pattern = f":({record.lineno})"
        formatted = re.sub(
            lineno_pattern,
            f":{self.SECTION_COLORS['lineno']}\\1{self.RESET}",
            formatted,
            count=1,
        )

        return formatted


# Configure basic logging with colored formatter
def setup_colored_logging():
    """Set up colored logging for the application."""
    # Create console handler with colored formatter
    console_handler = logging.StreamHandler()
    colored_formatter = ColoredFormatter("%(asctime)s %(levelname)s %(module)s:%(lineno)d %(message)s")
    console_handler.setFormatter(colored_formatter)

    # Configure root logger to INFO to reduce noise
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remove existing handlers and add our colored handler
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(console_handler)


# Set up colored logging
setup_colored_logging()
_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)

# Enable debug logging for our application modules
logging.getLogger("derhost").setLevel(logging.DEBUG)

# Turn down noisy loggers to reduce debug spam
logging.getLogger("watchdog.observers").setLevel(logging.INFO)
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)  # Silence all uvicorn.error messages
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def get_package_version():
    """Get the current package version."""
    try:
        # Try to get version from installed package
        return version("der-control-fastlib")
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

        # Setup templates directory
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.templates = Jinja2Templates(directory=templates_dir)

        # Setup static files directory
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            self.app.mount("/static", StaticFiles(directory=static_dir), name="static")

        # Add custom exception handler to convert 422 validation errors to 400 bad request
        # This matches the expected behavior for JSON-RPC validation errors
        @self.app.exception_handler(RequestValidationError)
        async def validation_exception_handler(request: Request, exc: RequestValidationError):
            # For RPC endpoint, return 400 instead of 422
            if request.url.path == "/gs":
                detail = "Invalid JSON-RPC format"
                if exc.errors():
                    error = exc.errors()[0]
                    if error.get("type") == "missing":
                        field = error.get("loc", ["unknown"])[-1]
                        detail = f"Missing required field: '{field}'"
                return JSONResponse(status_code=400, content={"detail": detail})
            # For other endpoints, use default 422 behavior
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

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

        self.config_store = ConfigStore(config_store_dir, messagebus=self)
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

                    _log.debug(f"Received data from {identity}: {data['type']}")

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
                                    _log.debug(f"Forwarding VIP RPC message to {message.peer}")
                                    await self.manager.send_message(
                                        message.peer,
                                        {"type": "vip", "message": message.__dict__},
                                    )
                            elif message.subsystem == "rpc_response":
                                # Handle RPC response
                                if hasattr(message, "msg_id"):
                                    _log.debug(f"Received VIP RPC response for msg_id {message.msg_id}")
                                    # Set the result for the waiting future
                                    self.manager.set_rpc_response(
                                        message.msg_id,
                                        (message.args[0] if hasattr(message, "args") and message.args else None),
                                    )

                                    # Forward the response to the original requester
                                    if hasattr(message, "peer") and message.peer in self.manager.active_connections:
                                        await self.manager.send_message(
                                            message.peer,
                                            {
                                                "type": "vip",
                                                "message": message.__dict__,
                                            },
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
                            _log.debug(f"Setting RPC response for msg_id {msg_id}: {result}")
                            self.manager.set_rpc_response(msg_id, result)

                    elif data["type"] == "rpc_error":
                        # Handle RPC error messages - use proper error handling
                        if "msg_id" in data and "error" in data:
                            msg_id = data["msg_id"]
                            error = data["error"]
                            _log.debug(f"Setting RPC error for msg_id {msg_id}: {error}")
                            self.manager.set_rpc_error(msg_id, error)

                    elif data["type"] == "register_rpc_methods":
                        # Handle RPC method registration from agent
                        if "methods" in data:
                            methods = data["methods"]
                            self.manager.agent_rpc_methods[identity] = methods
                            method_names = [m["name"] if isinstance(m, dict) else m for m in methods]
                            _log.info(f"Registered {len(methods)} RPC methods for {identity}: {method_names}")

            except WebSocketDisconnect:
                _log.debug(f"WebSocket disconnect for {identity}")
                self.manager.disconnect(identity)
            except Exception as e:
                _log.error(f"Error in websocket connection for {identity}: {e}")
                self.manager.disconnect(identity)

        @self.app.websocket("/monitor/{monitor_id}")
        async def monitor_websocket(websocket: WebSocket, monitor_id: str):
            """WebSocket endpoint for message bus monitoring."""
            await self.manager.connect_monitor(websocket, monitor_id)
            try:
                # Keep connection alive and handle any incoming control messages
                while True:
                    # Just wait for messages (could be used for control later)
                    data = await websocket.receive_json()
                    _log.debug(f"Monitor {monitor_id} sent: {data}")
            except WebSocketDisconnect:
                _log.debug(f"Monitor {monitor_id} disconnected")
                self.manager.disconnect_monitor(monitor_id)
            except Exception as e:
                _log.error(f"Error in monitor websocket for {monitor_id}: {e}")
                self.manager.disconnect_monitor(monitor_id)

        @self.app.get("/config-store/list")
        async def list_configs(agent_id: str | None = None):
            """List all available configurations."""
            configs = self.config_store.list_configs(agent_id)
            return {"status": "success", "data": configs}

        @self.app.get("/config-store/{agent_id}/{config_name:path}")
        async def get_config(
            agent_id: str,
            config_name: str,
            raw: bool = False,
            resolve_references: bool = True,
        ):
            """Retrieve a configuration for an agent with optional config:// reference resolution."""
            config = self.config_store.retrieve(agent_id, config_name, raw, resolve_references)
            if config is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config {config_name} not found for agent {agent_id}",
                )
            return {"status": "success", "data": config}

        @self.app.put("/config-store/{agent_id}/{config_name:path}")
        async def store_config(
            agent_id: str,
            config_name: str,
            config_data: Any = Body(..., description="Configuration data (JSON object, array, or primitive)"),
            request: Request = None,
            requesting_agent: str = Query(None, description="Identity of the agent making the request"),
            send_update: bool = Query(
                True,
                description="Whether to send config.update RPC notification to the agent",
            ),
        ):
            """Store a configuration for an agent."""
            # Determine if this is an external update or self-update
            is_external_update = (requesting_agent != agent_id) if requesting_agent else True

            # For now, we'll keep the access control but may want to relax it for admin agents
            if requesting_agent and requesting_agent != agent_id:
                # Log the external update attempt
                _log.info(f"External config update attempt: {requesting_agent} trying to update {agent_id}'s config")
                # For now, still enforce access control
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent {requesting_agent} cannot update configs for agent {agent_id}. "
                    f"Agents can only update their own configs.",
                )

            content_type = request.headers.get("Content-Type", "application/json") if request else "application/json"

            # Determine whether to send notifications:
            # - ALWAYS send notifications for external updates (like vctl config)
            # - For self-updates, respect the send_update flag
            should_notify = is_external_update or send_update

            try:
                if "csv" in content_type:
                    # For CSV, we need the raw request body
                    if request is None:
                        raise HTTPException(
                            status_code=400,
                            detail="CSV content requires raw request body",
                        )
                    csv_content = await request.body()
                    csv_text = csv_content.decode("utf-8")
                    success = self.config_store.store(
                        agent_id,
                        config_name,
                        csv_text,
                        "csv",
                        send_update=should_notify,
                    )
                else:
                    # Process as JSON (config_data is already parsed JSON from Body)
                    success = self.config_store.store(
                        agent_id,
                        config_name,
                        config_data,
                        "json",
                        send_update=should_notify,
                    )
            except ValueError as json_error:
                raise HTTPException(status_code=400, detail=f"Invalid JSON data: {str(json_error)}")
            except UnicodeDecodeError as decode_error:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid text encoding: {str(decode_error)}",
                )

            if success:
                # Config store now handles RPC notifications (VOLTTRON-style) based on send_update flag
                return {"status": "success"}
            else:
                raise HTTPException(status_code=500, detail="Failed to store configuration")

        @self.app.delete("/config-store/{agent_id}/{config_name:path}")
        async def delete_config(
            agent_id: str,
            config_name: str,
            requesting_agent: str = Query(None, description="Identity of the agent making the request"),
            send_update: bool = Query(
                True,
                description="Whether to send config.update RPC notification to the agent",
            ),
        ):
            """Delete a configuration for an agent."""
            # Determine if this is an external update or self-update
            is_external_update = (requesting_agent != agent_id) if requesting_agent else True

            # Access control: Only allow agent to delete its own configs (or admin override)
            if requesting_agent and requesting_agent != agent_id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent {requesting_agent} cannot delete configs for agent {agent_id}. "
                    f"Agents can only delete their own configs.",
                )

            # Determine whether to send notifications:
            # - ALWAYS send notifications for external updates (like vctl config)
            # - For self-updates, respect the send_update flag
            should_notify = is_external_update or send_update

            success = self.config_store.delete_config(agent_id, config_name, send_update=should_notify)
            if success:
                # Config store now handles RPC notifications (VOLTTRON-style) based on send_update flag
                return {"status": "success"}
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config {config_name} not found for agent {agent_id}",
                )

        @self.app.get("/")
        async def root(request: Request):
            """Serve the config manager web interface."""
            return self.templates.TemplateResponse(request, "config_manager.html")

        @self.app.get("/control")
        async def rpc_control_panel(request: Request):
            """Serve the RPC control panel web interface."""
            return self.templates.TemplateResponse(request, "rpc_control.html")

        @self.app.get("/message-monitor")
        async def message_monitor_panel(request: Request):
            """Serve the message bus monitor web interface."""
            return self.templates.TemplateResponse(request, "message_monitor.html")

        @self.app.get("/api/agents")
        async def list_agents():
            """List all connected agents and their RPC methods."""
            agents_info = []
            for agent_id in self.manager.active_connections:
                agents_info.append(
                    {
                        "agent_id": agent_id,
                        "rpc_methods": self.manager.agent_rpc_methods.get(agent_id, []),
                        "connected": True,
                    }
                )
            return {"agents": agents_info}

        @self.app.post("/api/rpc/call")
        async def call_rpc_method(
            agent_id: str = Body(...),
            method: str = Body(...),
            args: list = Body(default=[]),
            kwargs: dict = Body(default={}),
        ):
            """Call an RPC method on a specific agent and wait for response."""
            try:
                # Check if agent is connected
                if agent_id not in self.manager.active_connections:
                    return {"success": False, "error": f"Agent {agent_id} not connected"}

                # Use the connection manager's RPC handling
                import asyncio
                import uuid

                msg_id = str(uuid.uuid4())

                # Register a future for the response before sending
                future = self.manager.register_rpc_response_future(msg_id)

                # Build and send RPC message to the agent
                rpc_message = {
                    "type": "rpc_request",
                    "sender": "web.control",
                    "method": method,
                    "args": args,
                    "kwargs": kwargs,
                    "msg_id": msg_id,
                }

                await self.manager.send_message(agent_id, rpc_message)
                _log.info(f"RPC call sent to {agent_id}.{method} with msg_id {msg_id}")

                # Wait for response with 30 second timeout (longer for web UI)
                try:
                    response = await asyncio.wait_for(future, 30.0)
                    _log.info(f"RPC response received for msg_id {msg_id}: {response}")
                    return {
                        "success": True,
                        "result": response,
                        "msg_id": msg_id,
                        "agent_id": agent_id,
                        "method": method,
                    }
                except asyncio.TimeoutError:
                    _log.warning(f"RPC call to {agent_id}.{method} timed out (msg_id: {msg_id})")
                    return {
                        "success": False,
                        "error": "RPC call timed out after 30 seconds",
                        "msg_id": msg_id,
                        "timeout": True,
                    }

            except Exception as e:
                _log.error(f"Error in RPC call: {e}")
                return {"success": False, "error": str(e)}

        @self.app.websocket("/ws/config-ui")
        async def websocket_config_ui(websocket: WebSocket):
            """WebSocket endpoint for config UI real-time updates."""
            await websocket.accept()

            try:
                # Keep connection alive and listen for messages
                while True:
                    # Wait for messages from client
                    data = await websocket.receive_json()

                    # Handle ping/keepalive
                    if data.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})

            except WebSocketDisconnect:
                _log.debug("Config UI WebSocket disconnected")
            except Exception as e:
                _log.error(f"Error in config UI websocket: {e}")

        @self.app.get("/version")
        async def get_version():
            """Get the current server version."""
            version_string = get_package_version()
            return {
                "version": version_string,
                "service": "aems-server",
                "status": "running",
            }

        @self.app.get("/api/topics")
        async def get_topics():
            """Get list of all known pub/sub topics."""
            topics = self.manager.get_known_topics()
            return {"topics": topics}

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "version": get_package_version(),
                "active_connections": len(self.manager.active_connections),
                "service": "aems-server",
            }

        @self.app.get("/connections")
        async def get_connections():
            """Get detailed information about active WebSocket connections."""
            connections = {}
            for identity, websocket in self.manager.active_connections.items():
                connections[identity] = {
                    "identity": identity,
                    "connected": True,
                    "client_state": (websocket.client_state.name if hasattr(websocket, "client_state") else "unknown"),
                    "connection_time": getattr(websocket, "_connection_time", "unknown"),
                }

            return {
                "total_connections": len(self.manager.active_connections),
                "connections": connections,
                "timestamp": datetime.datetime.now().isoformat(),
            }

        @self.app.get("/connections/{agent_identity}")
        async def get_connection_status(agent_identity: str):
            """Get detailed status for a specific agent connection."""
            if agent_identity in self.manager.active_connections:
                websocket = self.manager.active_connections[agent_identity]
                return {
                    "identity": agent_identity,
                    "connected": True,
                    "client_state": (websocket.client_state.name if hasattr(websocket, "client_state") else "unknown"),
                    "connection_time": getattr(websocket, "_connection_time", "unknown"),
                    "pending_rpc_calls": len(
                        [msg_id for msg_id in self.manager.rpc_responses if msg_id.startswith(agent_identity)]
                    ),  # Simplified check
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            else:
                raise HTTPException(status_code=404, detail=f"Agent {agent_identity} not connected")

        @self.app.post("/connections/{agent_identity}/ping")
        async def ping_agent(agent_identity: str):
            """Send a ping message to test agent connection."""
            if agent_identity not in self.manager.active_connections:
                raise HTTPException(status_code=404, detail=f"Agent {agent_identity} not connected")

            try:
                ping_id = str(uuid.uuid4())
                await self.manager.send_message(
                    agent_identity,
                    {
                        "type": "ping",
                        "ping_id": ping_id,
                        "timestamp": datetime.datetime.now().isoformat(),
                    },
                )
                return {
                    "status": "ping_sent",
                    "ping_id": ping_id,
                    "agent": agent_identity,
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to ping agent: {str(e)}")

        @self.app.get("/rpc-status")
        async def get_rpc_status():
            """Get status of pending RPC calls."""
            pending_calls = {}
            for msg_id, future in self.manager.rpc_responses.items():
                pending_calls[msg_id] = {
                    "msg_id": msg_id,
                    "done": future.done(),
                    "cancelled": (future.cancelled() if hasattr(future, "cancelled") else False),
                }

            return {
                "pending_rpc_calls": len(self.manager.rpc_responses),
                "rpc_calls": pending_calls,
                "timestamp": datetime.datetime.now().isoformat(),
            }

        @self.app.get("/api/containers")
        async def list_containers():
            """List all running Docker containers."""
            try:
                import aiodocker

                async with aiodocker.Docker() as docker:
                    containers = await docker.containers.list()
                    result = []
                    for c in containers:
                        info = c._container
                        result.append(
                            {
                                "id": info.get("Id", "")[:12],
                                "name": info.get("Names", [""])[0].lstrip("/"),
                                "image": info.get("Image", ""),
                                "status": info.get("Status", ""),
                                "state": info.get("State", ""),
                            }
                        )
                    return {"containers": result, "count": len(result)}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Docker error: {e}")

        @self.app.get("/api/containers/{container_name}/logs")
        async def get_container_logs(
            container_name: str,
            tail: int = Query(default=100, ge=1, le=5000),
            since: str = Query(default=None, description="Time filter e.g. '1h', '30m', '2024-01-01T00:00:00'"),
        ):
            """Get logs from a Docker container."""
            try:
                import aiodocker

                async with aiodocker.Docker() as docker:
                    containers = await docker.containers.list()
                    container = None
                    for c in containers:
                        names = c._container.get("Names", [])
                        if any(n.lstrip("/") == container_name for n in names):
                            container = c
                            break
                    if container is None:
                        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")

                    kwargs = {"stdout": True, "stderr": True, "tail": tail, "follow": False}
                    if since:
                        kwargs["since"] = since

                    log_lines = await container.log(**kwargs)
                    return {
                        "container": container_name,
                        "tail": tail,
                        "lines": len(log_lines),
                        "logs": log_lines,
                    }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Docker error: {e}")

        from fastapi.responses import StreamingResponse

        @self.app.get("/api/containers/{container_name}/logs/stream")
        async def stream_container_logs(
            container_name: str,
            tail: int = Query(default=50, ge=1, le=1000),
        ):
            """Stream logs from a Docker container as Server-Sent Events."""
            try:
                import aiodocker

                async def event_generator():
                    async with aiodocker.Docker() as docker:
                        containers = await docker.containers.list()
                        container = None
                        for c in containers:
                            names = c._container.get("Names", [])
                            if any(n.lstrip("/") == container_name for n in names):
                                container = c
                                break
                        if container is None:
                            yield f"event: error\ndata: Container '{container_name}' not found\n\n"
                            return

                        async for line in container.log(stdout=True, stderr=True, tail=tail, follow=True):
                            yield f"data: {line}\n\n"

                return StreamingResponse(
                    event_generator(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Docker error: {e}")

        @self.app.post("/authenticate", response_model=AuthResponse)
        async def authenticate(request: Request):
            """Authenticate a user with username and password.

            Accepts username and password and returns JWT tokens for authentication.

            Example request:
            ```json
            {
                "username": "your_username",
                "password": "your_password"
            }
            ```
            """
            # Handle both JSON and form data
            content_type = request.headers.get("content-type", "")

            if "application/json" in content_type:
                # Handle JSON request
                try:
                    json_data = await request.json()
                    final_username = json_data.get("username")
                    final_password = json_data.get("password")
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid JSON format")
            elif "application/x-www-form-urlencoded" in content_type:
                # Handle form data
                try:
                    form_data = await request.form()
                    final_username = form_data.get("username")
                    final_password = form_data.get("password")
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid form data")
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported content type. Use application/json or application/x-www-form-urlencoded",
                )

            # Validate required fields are present (distinguish between missing and empty)
            if final_username is None or final_password is None:
                missing_fields = []
                if final_username is None:
                    missing_fields.append("username")
                if final_password is None:
                    missing_fields.append("password")
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required fields: {', '.join(missing_fields)}",
                )

            # Check for empty credentials (should be 401, not 400)
            if not final_username or not final_password:
                raise HTTPException(status_code=401, detail="Invalid credentials")

            # TODO: Implement actual authentication logic here
            # For now, this is a placeholder that accepts any non-empty credentials
            if final_username and final_password:
                try:
                    # JWT configuration
                    secret_key = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
                    algorithm = "HS256"

                    # Current time
                    now = datetime.datetime.utcnow()

                    # Generate access token (expires in 1 hour)
                    access_token_payload = {
                        "sub": final_username,  # subject (user identifier)
                        "iat": now,  # issued at
                        "exp": now + datetime.timedelta(hours=1),  # expires
                        "type": "access",
                    }
                    access_token = jwt.encode(access_token_payload, secret_key, algorithm=algorithm)

                    # Generate refresh token (expires in 7 days)
                    refresh_token_payload = {
                        "sub": final_username,
                        "iat": now,
                        "exp": now + datetime.timedelta(days=7),
                        "type": "refresh",
                    }
                    refresh_token = jwt.encode(refresh_token_payload, secret_key, algorithm=algorithm)

                    return AuthResponse(
                        message="Authentication successful",
                        username=final_username,
                        access_token=access_token,
                        refresh_token=refresh_token,
                    )
                except (jwt.InvalidTokenError, jwt.PyJWTError) as jwt_error:
                    _log.error(f"JWT encoding error: {jwt_error}")
                    raise HTTPException(status_code=500, detail="Token generation failed")
            else:
                raise HTTPException(status_code=401, detail="Invalid credentials")

        @self.app.post("/gs", response_model=JsonRpcResponse)
        async def rpc_endpoint(rpc_request: JsonRpcRequest):
            """Handle JSON-RPC 2.0 requests and route them to connected agents.

            Send commands and data to connected agents via JSON-RPC 2.0 protocol.

            Example using 'data' (legacy format):
            ```json
            {
                "jsonrpc": "2.0",
                "id": "platform.driver",
                "method": "set_point",
                "params": {
                    "data": {
                        "device": "PNNL/ROB/RTU02",
                        "point": "OccupiedCoolingSetPoint",
                        "value": 75.0
                    }
                }
            }
            ```

            Example using 'args' (positional arguments):
            ```json
            {
                "jsonrpc": "2.0",
                "id": "platform.driver",
                "method": "set_point",
                "params": {
                    "args": ["PNNL/ROB/RTU02", "OccupiedCoolingSetPoint", 75.0]
                }
            }
            ```

            Example using 'kwargs' (keyword arguments):
            ```json
            {
                "jsonrpc": "2.0",
                "id": "platform.driver",
                "method": "set_point",
                "params": {
                    "kwargs": {
                        "device": "PNNL/ROB/RTU02",
                        "point": "OccupiedCoolingSetPoint",
                        "value": 75.0
                    }
                }
            }
            ```
            """
            agent_id = rpc_request.id
            method = rpc_request.method
            params = rpc_request.params or JsonRpcParams()

            # Extract authentication and parameters from params
            authentication = params.authentication
            data = params.data
            args = params.args
            kwargs = params.kwargs or {}

            # Check if the target agent is connected
            if agent_id not in self.manager.active_connections:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' is not connected")

            # Generate a unique message ID for this RPC call
            import asyncio
            import uuid

            msg_id = str(uuid.uuid4())

            # Register a future for the RPC response
            future = self.manager.register_rpc_response_future(msg_id)

            try:
                # Determine which parameter format to use and create RPC message
                rpc_args = []
                rpc_kwargs = kwargs.copy()

                if args is not None:
                    # Use args format
                    rpc_args = args
                elif data is not None:
                    # Use legacy data format (single argument)
                    rpc_args = [data]

                # Add authentication to kwargs if provided
                if authentication:
                    rpc_kwargs["authentication"] = authentication

                # Create and send the RPC message
                rpc_message = {
                    "type": "rpc",
                    "method": method,
                    "args": rpc_args,
                    "kwargs": rpc_kwargs,
                    "msg_id": msg_id,
                }

                _log.debug(f"Sending HTTP RPC request to {agent_id}: {method}\nparams: {params}")
                await self.manager.send_message(agent_id, rpc_message)

                # Wait for the response (with timeout)
                try:
                    result = await asyncio.wait_for(future, timeout=30.0)  # 30 second timeout

                    # Return JSON-RPC 2.0 success response
                    return JsonRpcResponse(id=agent_id, result=result)

                except asyncio.TimeoutError:
                    # Clean up the future
                    self.manager.clear_rpc_response(msg_id)
                    # Return JSON-RPC 2.0 error response for timeout
                    return JsonRpcResponse(
                        id=agent_id,
                        error=JsonRpcError(code=-32603, message="Internal error: RPC call timed out"),
                    )

            except KeyError as key_error:
                # Clean up the future
                self.manager.clear_rpc_response(msg_id)
                _log.error(f"Missing key in RPC processing: {key_error}")
                return JsonRpcResponse(
                    id=agent_id,
                    error=JsonRpcError(code=-32602, message=f"Invalid params: missing {str(key_error)}"),
                )
            except ConnectionError as conn_error:
                # Clean up the future
                self.manager.clear_rpc_response(msg_id)
                _log.error(f"Connection error during RPC: {conn_error}")
                return JsonRpcResponse(
                    id=agent_id,
                    error=JsonRpcError(code=-32603, message="Internal error: Connection failed"),
                )
            except Exception as rpc_error:
                # Clean up the future - this is our fallback for truly unexpected errors
                self.manager.clear_rpc_response(msg_id)
                _log.error(f"Unexpected error in RPC processing: {type(rpc_error).__name__}: {rpc_error}")
                return JsonRpcResponse(
                    id=agent_id,
                    error=JsonRpcError(code=-32603, message="Internal error: Unexpected error occurred"),
                )

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
        self._server_thread = threading.Thread(target=self._run_server, daemon=True, name="FastAPIMessageBus-Server")
        self._server_thread.start()

        _log.info(f"FastAPIMessageBus running on http://{self.host}:{self.port}")

        # Initialize other components after server start
        if hasattr(self, "_init_after_start"):
            self._init_after_start()

    def _run_server(self):
        """Run the uvicorn server."""
        try:
            _log.debug(f"Starting server thread for {self.host}:{self.port}")
            # Since we're in a separate thread, we need to create our own event loop
            # This avoids conflicts with any existing event loop in the main thread
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                _log.debug("About to start uvicorn server...")
                new_loop.run_until_complete(self.server.serve())
                _log.debug("Uvicorn server finished serving")
            finally:
                _log.debug("Closing event loop")
                new_loop.close()
        except Exception as e:
            _log.error(f"Error running server: {e}")
            import traceback

            traceback.print_exc()
            raise
        finally:
            _log.debug("Server thread ending, setting running=False")
            self.running = False

    def stop(self):
        """Stop the message bus."""
        if not self.running or self.server is None:
            return

        if self._stop_handler:
            self._stop_handler.message_bus_shutdown()

        _log.info("Stopping FastAPIMessageBus...")

        # Signal the server to stop
        self.server.should_exit = True

        # Wait for the server thread to finish
        if hasattr(self, "_server_thread") and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)

        self.running = False
        _log.debug("MessageBus stopped")

    def is_running(self) -> bool:
        """Check if the message bus is running."""
        return self.running

    def send_vip_message(self, message: Message):
        """Send a VIP message."""
        asyncio.create_task(self._send_vip_message_async(message))

    async def _send_vip_message_async(self, message: Message):
        """Async implementation of send_vip_message."""
        if hasattr(message, "peer") and message.peer in self.manager.active_connections:
            await self.manager.send_message(message.peer, {"type": "vip", "message": message.__dict__})

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

    Returns
    -------
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
    _log.info(f"AEMS message bus server started at {host}:{port}")
    _log.info(f"Using config store directory: {server.config_store.base_dir}")

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
        "--volttron-home",
        default=os.environ.get("VOLTTRON_HOME"),
        help="VOLTTRON_HOME directory",
    )
    parser.add_argument(
        "--config-dir",
        help="Config store directory (defaults to VOLTTRON_HOME/aems_config_store)",
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
        # Skip debugger detection if we're in a test environment
        if "pytest" in sys.modules or "unittest" in sys.modules:
            return False

        # Check for common debugger indicators
        if hasattr(sys, "gettrace") and sys.gettrace() is not None:
            # Make sure it's not just pytest's trace function
            tracer = sys.gettrace()
            if tracer and hasattr(tracer, "__name__"):
                if "pytest" in tracer.__name__ or "coverage" in tracer.__name__:
                    return False
            return True
        # Check for debugpy (VS Code debugger)
        if "debugpy" in sys.modules:
            return True
        # Check for pdb
        return "pdb" in sys.modules

    if is_debugger_attached():
        _log.info("Debugger detected - using direct uvicorn.run() for better debugging support")

        # Create the FastAPI app directly for uvicorn.run()
        server = FastAPIMessageBus(
            host=args.host,
            port=args.port,
            config_store_dir=config_dir,
            reload=args.reload,
        )

        # Use uvicorn.run() directly for debugging
        # Set up colored logging before starting uvicorn to avoid config issues
        root_logger = logging.getLogger()

        # Clear any existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Add our colored handler
        console_handler = logging.StreamHandler()
        colored_formatter = ColoredFormatter("%(asctime)s %(levelname)s %(module)s:%(lineno)d %(message)s")
        console_handler.setFormatter(colored_formatter)
        root_logger.addHandler(console_handler)
        root_logger.setLevel(logging.INFO)  # Set to INFO to reduce uvicorn debug spam

        # Enable debug logging for our application modules
        logging.getLogger("derhost").setLevel(logging.DEBUG)

        # Configure all uvicorn loggers
        uvicorn_loggers = {
            "uvicorn": logging.INFO,
            "uvicorn.error": logging.CRITICAL,  # Silence all uvicorn.error messages
            "uvicorn.access": logging.INFO,
        }
        for logger_name, level in uvicorn_loggers.items():
            logger = logging.getLogger(logger_name)
            # Clear existing handlers
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
            # Add our colored handler
            handler = logging.StreamHandler()
            formatter = ColoredFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(level)
            logger.propagate = False

        uvicorn.run(
            server.app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="debug",
            log_config=None,  # Disable uvicorn's logging config since we set it up ourselves
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
            _log.info("Stopping server...")
            server.stop()


if __name__ == "__main__":
    _main()
