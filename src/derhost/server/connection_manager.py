# connection_manager.py

import asyncio
import logging
import re
from collections.abc import Callable
from re import Pattern
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from derhost._redact import redact_known_secret_values, redact_secrets, truncate_for_log

_log = logging.getLogger(__name__)


def truncate_debug_message(message: Any, max_length: int = 200) -> str:
    """Truncate a message for debug logging to limit output size.

    Redaction runs before truncation (see `truncate_for_log`): truncating a
    secret-bearing value first can cut a redaction marker in half and let an
    unredacted prefix through.

    Args:
        message: The message to truncate
        max_length: Maximum length of the returned string (default: 200)

    Returns
    -------
        Truncated, redacted string representation of the message
    """
    return truncate_for_log(message, max_length)


# Subscription callback type
SubscriptionCallback = Callable[[str, str, str, str, dict, Any], None]


class ConnectionManager:
    """Manages WebSocket connections for the MessageBus."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.agent_rpc_methods: dict[str, list[str]] = {}  # Store RPC methods for each agent
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.prefix_subscriptions: dict[str, dict[str, list[tuple[str, SubscriptionCallback]]]] = {}
        self.regex_subscriptions: dict[str, list[tuple[Pattern, str, SubscriptionCallback]]] = {}
        self.rpc_responses: dict[str, asyncio.Future] = {}
        self._status_task: asyncio.Task = None
        self._status_reporter_started = False
        self._no_connections_count = 0  # Track consecutive periods with no connections
        # Message bus monitoring
        self.known_topics: set[str] = set()  # Track all topics that have been published
        self.monitor_connections: dict[str, WebSocket] = {}  # WebSocket connections for monitors

    async def connect(self, websocket: WebSocket, identity: str):
        """Connect a client to the message bus."""
        # VOLTTRON compatibility: Only one agent per identity allowed
        if identity in self.active_connections:
            existing_ws = self.active_connections[identity]
            if existing_ws.client_state == WebSocketState.CONNECTED:
                _log.warning(f"Agent {identity} already connected - rejecting new connection")
                # Reject BEFORE accepting to prevent client from thinking it's connected
                await websocket.close(code=4000, reason=f"Agent {identity} already connected")
                return
            else:
                # Clean up stale connection
                _log.info(f"Replacing stale connection for agent {identity}")
                self.disconnect(identity)

        await websocket.accept()
        self.active_connections[identity] = websocket
        self.prefix_subscriptions[identity] = {}

        # Start the status reporter when the first connection is made
        if not self._status_reporter_started:
            self._start_status_reporter()

        _log.debug(f"Client {identity} connected")

    def disconnect(self, identity: str):
        """Disconnect a client from the message bus."""
        if identity in self.active_connections:
            del self.active_connections[identity]
        if identity in self.prefix_subscriptions:
            del self.prefix_subscriptions[identity]
        if identity in self.agent_rpc_methods:
            del self.agent_rpc_methods[identity]
        # Remove any regex subscriptions for this identity
        self.regex_subscriptions = {
            identity_key: subscriptions
            for identity_key, subscriptions in self.regex_subscriptions.items()
            if identity_key != identity
        }
        _log.debug(f"Client {identity} disconnected")

    async def send_message(self, identity: str, message: dict):
        """Send a message to a specific client."""
        if identity in self.active_connections:
            websocket = self.active_connections[identity]
            if websocket.client_state != WebSocketState.DISCONNECTED:
                _log.debug(f"Sending message to {identity}: {truncate_debug_message(message)}")
                await websocket.send_json(message)
            else:
                _log.debug(f"Cannot send message to {identity}, websocket is disconnected")
        else:
            _log.debug(f"Cannot send message to {identity}, client not found")

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        for _identity, websocket in self.active_connections.items():
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
        # Track this topic
        self.known_topics.add(topic)

        # Broadcast to message bus monitors
        import datetime

        monitor_data = {
            "type": "pubsub_message",
            "timestamp": datetime.datetime.now().isoformat(),
            "topic": topic,
            "sender": sender,
            "headers": headers,
            "message": message,
        }
        await self._broadcast_to_monitors(monitor_data)

        # Process prefix subscriptions
        for _identity, prefixes in self.prefix_subscriptions.items():
            for prefix, callbacks in prefixes.items():
                if topic.startswith(prefix):
                    for peer, callback in callbacks:
                        try:
                            callback(peer, sender, bus, topic, headers, message)
                        except Exception as e:
                            _log.error(f"Error in prefix subscription callback: {e}")

        # Process regex subscriptions
        for _identity, patterns in self.regex_subscriptions.items():
            for pattern, peer, callback in patterns:
                if pattern.match(topic):
                    try:
                        callback(peer, sender, bus, topic, headers, message)
                    except Exception as e:
                        _log.error(f"Error in regex subscription callback: {e}")

    def register_rpc_response_future(self, msg_id: str) -> asyncio.Future:
        """Register a future for an RPC response."""
        future = asyncio.get_event_loop().create_future()
        self.rpc_responses[msg_id] = future
        _log.debug(f"Registered RPC response future for msg_id {msg_id}")
        return future

    def set_rpc_response(self, msg_id: str, response: Any):
        """Set the result for an RPC response future."""
        if msg_id in self.rpc_responses:
            future = self.rpc_responses.pop(msg_id)
            if not future.done():
                _log.debug(f"Setting RPC response for msg_id {msg_id}: {truncate_debug_message(response)}")
                future.set_result(response)
            else:
                _log.debug(f"Future for msg_id {msg_id} was already done")
        else:
            _log.debug(f"No future found for msg_id {msg_id}")

    def set_rpc_error(self, msg_id: str, error: str):
        """Set an exception for an RPC response future."""
        if msg_id in self.rpc_responses:
            future = self.rpc_responses.pop(msg_id)
            if not future.done():
                _log.debug(f"Setting RPC error for msg_id {msg_id}: {truncate_debug_message(error)}")
                future.set_exception(Exception(error))
            else:
                _log.debug(f"Future for msg_id {msg_id} was already done")
        else:
            _log.debug(f"No future found for msg_id {msg_id}")

    def clear_rpc_response(self, msg_id: str):
        """Clear an RPC response future."""
        if msg_id in self.rpc_responses:
            future = self.rpc_responses.pop(msg_id)
            if not future.done():
                future.cancel()
            _log.debug(f"Cleared RPC response future for msg_id {msg_id}")

    async def handle_rpc(self, sender: str, peer: str, method: str, args: list, kwargs: dict, msg_id: str):
        """Handle RPC request between clients."""
        # Log at INFO level with full parameters for visibility
        _log.info(
            f"RPC request from {sender} to {peer}: {method}(args={redact_secrets(args)}, "
            f"kwargs={redact_secrets(kwargs)}) [msg_id: {msg_id}]"
        )
        _log.debug(
            f"RPC request from {sender} to {peer}: {method}({truncate_debug_message(args)}, "
            f"{truncate_debug_message(kwargs)}) [msg_id: {msg_id}]"
        )

        if peer not in self.active_connections:
            _log.debug(f"RPC target {peer} not found")
            await self.send_message(
                sender,
                {
                    "type": "rpc_error",
                    "msg_id": msg_id,
                    "error": f"Peer {peer} not found",
                },
            )
            return

        # Create a message for the RPC call
        rpc_message = {
            "type": "rpc_request",
            "sender": sender,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "msg_id": msg_id,
        }

        # Register a future for the response
        future = self.register_rpc_response_future(msg_id)

        # Send to the target peer
        _log.debug(f"Sending RPC request to {peer}")
        await self.send_message(peer, rpc_message)

        # Wait for response with timeout
        try:
            _log.debug(f"Waiting for RPC response for msg_id {msg_id}")
            response = await asyncio.wait_for(future, 30.0)  # 30 second timeout for BACnet operations
            _log.info(f"RPC response from {peer} to {sender}: {method} returned {redact_secrets(response)}")
            _log.debug(f"Received RPC response for msg_id {msg_id}: {truncate_debug_message(response)}")
            await self.send_message(sender, {"type": "rpc_response", "msg_id": msg_id, "result": response})
        except asyncio.TimeoutError:
            _log.warning(
                f"RPC request from {sender} to {peer}.{method}(args={redact_secrets(args)}, "
                f"kwargs={redact_secrets(kwargs)}) timed out after 30s [msg_id: {msg_id}]"
            )
            _log.debug(f"RPC request timed out for msg_id {msg_id}")
            await self.send_message(
                sender,
                {
                    "type": "rpc_error",
                    "msg_id": msg_id,
                    "error": "RPC request timed out",
                },
            )
            self.clear_rpc_response(msg_id)
        except Exception as e:
            # Handle exceptions from RPC method execution. Redacted here, not
            # just for the log: this error text is also sent on to the
            # requesting client as this RPC's result.
            error = redact_known_secret_values(str(e), args, kwargs)
            _log.error(
                f"RPC request from {sender} to {peer}.{method}(args={redact_secrets(args)}, "
                f"kwargs={redact_secrets(kwargs)}) failed: {error} [msg_id: {msg_id}]"
            )
            await self.send_message(sender, {"type": "rpc_error", "msg_id": msg_id, "error": error})
            self.clear_rpc_response(msg_id)

    def _start_status_reporter(self):
        """Start the background task to report connection status every 60s."""
        if not self._status_reporter_started:
            try:
                self._status_task = asyncio.create_task(self._status_reporter_loop())
                self._status_reporter_started = True
                _log.info("Connection status reporter started")
            except RuntimeError as e:
                # Handle case where no event loop is running
                _log.error(f"Cannot start status reporter: {e}")
                _log.info("Status reporter will be started when the first connection is made")

    async def _status_reporter_loop(self):
        """Background loop to print active connections every 60 seconds."""
        while True:
            try:
                await asyncio.sleep(60)
                active_identities = list(self.active_connections.keys())
                if active_identities:
                    # Reset counter when we have active connections
                    self._no_connections_count = 0
                    count = len(active_identities)
                    identities_str = ", ".join(active_identities)
                    _log.info(f"Active connections ({count}): {identities_str}")
                else:
                    # Increment counter for consecutive periods with no connections
                    self._no_connections_count += 1
                    # Print "no active connections" only after 10 minutes (10 periods of 60 seconds)
                    if self._no_connections_count >= 10:
                        _log.info("No active connections")
                        # Reset counter after printing to avoid repeated messages
                        self._no_connections_count = 0
            except asyncio.CancelledError:
                _log.info("Connection status reporter stopped")
                break
            except Exception as e:
                _log.error(f"Error in status reporter: {e}")

    def stop_status_reporter(self):
        """Stop the status reporter task."""
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
            _log.info("Connection status reporter task cancelled")

    async def startup(self):
        """Initialize the connection manager when the application starts."""
        self._start_status_reporter()

    async def shutdown(self):
        """Clean up the connection manager when the application shuts down."""
        self.stop_status_reporter()
        # Cancel any remaining futures
        for future in self.rpc_responses.values():
            if not future.done():
                future.cancel()
        self.rpc_responses.clear()

    # Message bus monitoring methods
    async def connect_monitor(self, websocket: WebSocket, monitor_id: str):
        """Connect a message bus monitor client."""
        await websocket.accept()
        self.monitor_connections[monitor_id] = websocket
        _log.info(f"Message bus monitor {monitor_id} connected")

    def disconnect_monitor(self, monitor_id: str):
        """Disconnect a message bus monitor client."""
        if monitor_id in self.monitor_connections:
            del self.monitor_connections[monitor_id]
            _log.info(f"Message bus monitor {monitor_id} disconnected")

    async def _broadcast_to_monitors(self, data: dict):
        """Broadcast pub/sub message to all connected monitors."""
        disconnected = []
        for monitor_id, websocket in self.monitor_connections.items():
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json(data)
                else:
                    disconnected.append(monitor_id)
            except Exception as e:
                _log.error(f"Error broadcasting to monitor {monitor_id}: {e}")
                disconnected.append(monitor_id)

        # Clean up disconnected monitors
        for monitor_id in disconnected:
            self.disconnect_monitor(monitor_id)

    def get_known_topics(self) -> list[str]:
        """Get list of all known topics (sorted)."""
        return sorted(self.known_topics)
