from __future__ import annotations

import heapq
import json
import logging
import numbers
import ssl
import time
import traceback
import uuid
import websocket
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List

import gevent
import httpx
from gevent import monkey
from gevent.event import AsyncResult

from aems.client import dualmethod

# Use volttron-core JSON-RPC utilities for compatibility
try:
    from volttron.utils.jsonrpc import exception_from_json, Error, RemoteError, MethodNotFound
    VOLTTRON_JSONRPC_AVAILABLE = True
except ImportError:
    # Fallback to our custom implementation
    from .jsonrpc import exception_from_json, Error, RemoteError, MethodNotFound
    VOLTTRON_JSONRPC_AVAILABLE = False

# Patch standard library to work with gevent
monkey.patch_all()


class RPC:
    """RPC subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self._exported_methods = {}

        # Register any methods decorated with @RPC.export
        self._register_decorated_methods(agent)

    @staticmethod
    def export(method=None, name=None):
        """
        Decorator to mark a method as remotely accessible.

        Usage:
            @RPC.export
            def my_method(self, arg1, arg2):
                pass

            @RPC.export(name='custom_name')
            def my_method(self, arg1, arg2):
                pass
        """
        # Handle the case where decorator is used without parentheses
        if callable(method):
            setattr(method, "rpc_exported", True)
            setattr(method, "rpc_name", None)  # Use the method's name
            return method

        # Handle the case where decorator is used with parentheses
        def decorator(f):
            setattr(f, "rpc_exported", True)
            setattr(f, "rpc_name", name)
            return f

        return decorator

    def export_method(self, method_name: str, method: Callable):
        """Programmatically export an RPC method that can be called remotely."""
        self._exported_methods[method_name] = method
        print(f"Agent {self._agent.identity} exported RPC method: {method_name}")
        return method  # Return the method for chaining

    def _register_decorated_methods(self, agent):
        """Find and register methods decorated with @RPC.export."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "rpc_exported"):
                # Use the custom name if provided, otherwise use the method's name
                method_name = getattr(attr, "rpc_name") or attr_name
                self._exported_methods[method_name] = attr
                print(
                    f"Agent {self._agent.identity} exported RPC method: "
                    f"{method_name} (from decorator)"
                )

    def call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another agent, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        msg_id = str(uuid.uuid4())
        async_result = AsyncResult()
        self._agent.rpc_responses[msg_id] = async_result

        print(
            f"DEBUG: Agent {self._agent.identity} making RPC call to "
            f"{peer}.{method} with msg_id {msg_id}"
        )

        self._agent.websocket.send(
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
            f"Agent {self._agent.identity} sent RPC call to {peer}: "
            f"method={method}, args={args}, kwargs={kwargs}"
        )

        # Spawn a timeout watcher
        gevent.spawn(self._watch_timeout, msg_id, async_result, 10)  # 10 second timeout

        return async_result

    def _watch_timeout(self, msg_id: str, async_result: AsyncResult, timeout: int):
        """Watch for timeout on an AsyncResult."""
        # Wait for the timeout
        gevent.sleep(timeout)

        # If the response hasn't been set yet, set an error
        if msg_id in self._agent.rpc_responses:
            del self._agent.rpc_responses[msg_id]
            if not async_result.ready():
                async_result.set_exception(TimeoutError("RPC call timed out"))

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
                # Get an AsyncResult for the remote call
                async_result = self.call(target, actual_method, *args, **kwargs)

                # Create a new async result to track the response back to the original sender
                final_result = AsyncResult()

                # Spawn a greenlet to wait for the result and relay it
                def relay_result():
                    try:
                        # Wait for the result from the remote agent
                        result = async_result.get(timeout=10)
                        # Return it to the requestor
                        final_result.set(result)
                    except Exception as e:
                        final_result.set_exception(e)

                gevent.spawn(relay_result)
                return final_result
            except Exception as e:
                error_msg = f"Remote call error: {str(e)}"
                print(f"DEBUG: {error_msg}")
                async_result = AsyncResult()
                async_result.set_exception(Exception(error_msg))
                return async_result
        else:
            # This is a local method call
            async_result = AsyncResult()

            if method_name in self._exported_methods:
                try:
                    method = self._exported_methods[method_name]
                    print(f"DEBUG: Agent {self._agent.identity} executing method {method_name}")
                    result = method(*args, **kwargs)
                    print(
                        f"DEBUG: Agent {self._agent.identity} method {method_name} result: {result}"
                    )
                    async_result.set(result)
                except Exception as e:
                    error = str(e)
                    print(
                        f"DEBUG: Agent {self._agent.identity} method {method_name} error: {error}"
                    )
                    async_result.set_exception(e)
            else:
                error = f"Method {method_name} not found or not exported"
                print(f"DEBUG: {error}")
                async_result.set_exception(Exception(error))

            return async_result


class PubSub:
    """PubSub subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self._subscriptions = {}

    def publish(
        self, peer: str, topic: str, message: Any, headers: Optional[Dict] = None, bus: str = ""
    ):
        """Publish a message to a topic, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        if headers is None:
            headers = {}

        # Create an AsyncResult to track the publish operation
        async_result = AsyncResult()

        try:
            # If this is a default_config then we need to not worry about the message itself being
            # sent as it will be updated by the config store.
            if topic == "config/config":
                message = {}

            self._agent.websocket.send(
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

            if topic == "config/config":
                print(f"Agent {self._agent.identity} published to {topic}: default update sent")
            else:
                print(f"Agent {self._agent.identity} published to {topic}: {message}")
            async_result.set(True)  # Success
        except Exception as e:
            print(f"Error publishing message: {e}")
            async_result.set_exception(e)

        return async_result

    def _callback_adapter(self, callback):
        """
        Create an adapter that inspects a callback's signature and calls it with appropriate parameters.
        Supports both modern (message object) and original VOLTTRON-style callbacks.
        """

        def adapter(message):
            try:
                # First try the traditional style with 6 parameters
                # Extract needed fields from the message
                peer = message.get("peer", "")
                sender = message.get("sender", "")
                bus = message.get("bus", "")
                topic = message.get("topic", "")
                headers = message.get("headers", {})
                msg_data = message.get("message", {})

                try:
                    # Try calling with traditional parameters
                    return callback(peer, sender, bus, topic, headers, msg_data)
                except TypeError as e:
                    if "missing" in str(e) or "takes" in str(e) or "arguments" in str(e):
                        # If we get a TypeError about missing or too many arguments,
                        # it's likely not the traditional style
                        # Try the modern style with just the message
                        return callback(message)
                    else:
                        # Some other TypeError, re-raise it
                        raise
            except Exception as e:
                print(f"Error in subscription callback: {e}")
                traceback.print_exc()
                # Return None when there's an error
                return None

        return adapter

    def subscribe(self, prefix: str, callback: Optional[Callable] = None, **kwargs):
        """Subscribe to a topic prefix, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        subscription_id = str(uuid.uuid4())

        # Store the original callback
        actual_callback = callback or (
            lambda msg: print(f"Subscription callback for {prefix}: {msg}")
        )

        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(actual_callback)

        self._subscriptions[prefix] = adapted_callback

        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()

        try:
            self._agent.websocket.send(
                json.dumps({"type": "subscribe", "prefix": prefix, "id": subscription_id})
            )

            print(f"Agent {self._agent.identity} subscribed to prefix: {prefix}")
            async_result.set(subscription_id)  # Return the subscription ID
        except Exception as e:
            print(f"Error subscribing to topic: {e}")
            async_result.set_exception(e)

        return async_result

    def subscribe_regex(self, pattern: str, callback: Optional[Callable] = None):
        """Subscribe to a topic pattern, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        subscription_id = str(uuid.uuid4())

        # Store the original callback
        actual_callback = callback or (
            lambda msg: print(f"Subscription callback for {pattern}: {msg}")
        )

        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(actual_callback)

        self._subscriptions[pattern] = adapted_callback

        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()

        try:
            self._agent.websocket.send(
                json.dumps({"type": "subscribe", "pattern": pattern, "id": subscription_id})
            )

            print(f"Agent {self._agent.identity} subscribed to pattern: {pattern}")
            async_result.set(subscription_id)  # Return the subscription ID
        except Exception as e:
            print(f"Error subscribing to pattern: {e}")
            async_result.set_exception(e)

        return async_result

    def get_subscriptions(self):
        """Get all active subscriptions."""
        return list(self._subscriptions.keys())

    def handle_message(self, data: Dict):
        """Handle an incoming pubsub message."""
        topic = data.get("topic", "")

        # Find matching subscriptions and call their callbacks
        for prefix, callback in self._subscriptions.items():
            if topic.startswith(prefix) or prefix in ["", "*", "all"]:
                try:
                    callback(data)
                except Exception as e:
                    print(f"Error calling subscription callback: {e}")
                    traceback.print_exc()


class VIP:
    """VIP subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self.rpc = RPC(agent)
        self.pubsub = PubSub(agent)
        self.config = Config(agent)
        self.peerlist = Peerlist(agent)

    def send_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        if args is None:
            args = []

        msg_id = str(uuid.uuid4())
        async_result = AsyncResult()

        try:
            self._agent.websocket.send(
                json.dumps(
                    {
                        "type": "vip",
                        "message": {
                            "peer": peer,
                            "user": self._agent.identity,
                            "subsystem": subsystem,
                            "msg_id": msg_id,
                            "args": args,
                        },
                    }
                )
            )

            print(
                f"Agent {self._agent.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}"
            )
            async_result.set(msg_id)  # Return the message ID
        except Exception as e:
            print(f"Error sending VIP message: {e}")
            async_result.set_exception(e)

        return async_result


class Core:
    """Core functionality for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self.onstart = Signal("onstart")
        self.onstop = Signal("onstop")
        self.onfinish = Signal("onfinish")
        self.onconnected = Signal("onconnected")
        self.ondisconnected = Signal("ondisconnected")
        self.onconfigure = Signal("onconfigure")

        self._signals = {
            "onstart": self.onstart,
            "onstop": self.onstop,
            "onconfigure": self.onconfigure,
            "onfinish": self.onfinish,
            "onconnected": self.onconnected,
            "ondisconnected": self.ondisconnected,
        }
        self._handlers = {event: [] for event in self._signals.keys()}
        self._scheduler = Scheduler(agent)

        # Register any methods decorated with @Core.receiver
        self._register_decorated_methods(agent)

        # Register any methods decorated with @Core.periodic
        self._register_periodic_methods(agent)

    def schedule(self, interval_or_cron, function, *args, **kwargs):
        """
        Schedule a periodic function.

        Args:
            function: The function to call
            interval_or_cron: Either a number of seconds (interval), a cron expression,
                             a datetime object, or a datetime string
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The name of the scheduled event
        """
        # Convert datetime (object or string) to cron expression if needed
        if isinstance(interval_or_cron, datetime):
            # Convert datetime object to cron expression: minute hour day month dayofweek
            cron_expr = (
                f"{interval_or_cron.minute} {interval_or_cron.hour} "
                f"{interval_or_cron.day} {interval_or_cron.month} *"
            )
            interval_or_cron = cron_expr
        elif isinstance(interval_or_cron, str) and not self._is_cron_expression(interval_or_cron):
            # Try to parse as datetime string
            try:
                # Parse common datetime string formats
                dt = self._parse_datetime_string(interval_or_cron)
                cron_expr = f"{dt.minute} {dt.hour} " f"{dt.day} {dt.month} *"
                interval_or_cron = cron_expr
            except ValueError:
                # If parsing fails, assume it's already a cron expression
                pass

        return self._scheduler.schedule(function, interval_or_cron, args, kwargs)

    def _is_cron_expression(self, expr):
        """Check if a string looks like a cron expression."""
        parts = expr.strip().split()
        return len(parts) == 5

    def _parse_datetime_string(self, dt_str):
        """Parse a datetime string into a datetime object."""
        # Common datetime formats to try
        formats = [
            "%Y-%m-%d %H:%M:%S",  # 2025-12-25 14:30:00
            "%Y-%m-%d %H:%M",  # 2025-12-25 14:30
            "%Y-%m-%dT%H:%M:%S",  # 2025-12-25T14:30:00 (ISO format)
            "%Y-%m-%dT%H:%M",  # 2025-12-25T14:30
            "%m/%d/%Y %H:%M:%S",  # 12/25/2025 14:30:00
            "%m/%d/%Y %H:%M",  # 12/25/2025 14:30
            "%d-%m-%Y %H:%M:%S",  # 25-12-2025 14:30:00
            "%d-%m-%Y %H:%M",  # 25-12-2025 14:30
        ]

        for fmt in formats:
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue

        # If none of the formats work, raise an error
        raise ValueError(f"Unable to parse datetime string: {dt_str}")

    def cancel(self, name):
        """Cancel a scheduled event."""
        return self._scheduler.cancel(name)

    def update_interval(self, name, interval):
        """Update the interval of a scheduled event."""
        return self._scheduler.update_interval(name, interval)

    def update_cron(self, name, cron_expression):
        """Update the cron expression of a scheduled event."""
        return self._scheduler.update_cron(name, cron_expression)

    def list_events(self):
        """List all scheduled events."""
        return self._scheduler.list_events()

    @staticmethod
    def receiver(event_name):
        """
        Decorator to register a method as a handler for a specific event.

        Usage:
            @Core.receiver('onstart')
            def my_onstart_handler(self, sender, **kwargs):
                # do something when agent starts
        """

        def decorator(method):
            setattr(method, "event_name", event_name)
            return method

        return decorator

    @dualmethod
    def periodic(self, interval_or_cron: int | str, function):
        self._scheduler.schedule(function, interval_or_cron)

    @periodic.classmethod
    def periodic(cls, interval_or_cron):
        """
        Decorator to register a method to run periodically.

        Usage:
            @Core.periodic(30)  # Run every 30 seconds
            def my_periodic_task(self):
                # do something periodically

            @Core.periodic("*/5 * * * *")  # Run every 5 minutes (cron expression)
            def my_cron_task(self):
                # do something based on a cron schedule
        """

        def decorator(method):
            setattr(method, "periodic", True)
            setattr(method, "interval_or_cron", interval_or_cron)
            return method

        return decorator

    def _register_periodic_methods(self, agent):
        """Find and register methods decorated with @Core.periodic."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "periodic") and hasattr(attr, "interval_or_cron"):
                interval_or_cron = getattr(attr, "interval_or_cron")
                self._scheduler.schedule(attr, interval_or_cron)

    def stop(self):
        """Stop the agent, returning an AsyncResult."""
        async_result = AsyncResult()
        try:
            self.fire_event("onstop", self)

            # Stop the scheduler
            self._scheduler.stop()

            self._agent.disconnect()
            async_result.set(True)
        except Exception as e:
            async_result.set_exception(e)
        finally:
            self.fire_event("onfinish", self)
        return async_result

    def start_periodic_tasks(self):
        """Start running periodic tasks."""
        self._scheduler.start()

    def identity(self):
        """Get the agent's identity."""
        return self._agent.identity

    def start(self):
        """Start the agent core services."""
        # Note: onstart event is fired by Agent.connect() during connection lifecycle
        # This method is kept for backwards compatibility but no longer fires onstart
        pass

    def _register_decorated_methods(self, agent):
        """Find and register methods decorated with @Core.receiver."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "event_name"):
                event_name = getattr(attr, "event_name")
                # Register with the signal to avoid double firing through _handlers
                if event_name in self._signals:
                    self._signals[event_name].connect(attr)

    def fire_event(self, event_name, sender=None, **kwargs):
        """Fire an event by calling all registered handlers."""
        if event_name in self._signals:
            # Fire the signal - this calls @Core.receiver decorated methods
            self._signals[event_name].fire(sender, **kwargs)

        # Also call manually registered handlers (for backwards compatibility)
        if event_name in self._handlers:
            for handler in self._handlers[event_name]:
                try:
                    gevent.spawn(handler, sender, **kwargs)
                except Exception as e:
                    print(f"Error in {event_name} handler: {e}")


class ConfigCallback:
    """A callback for configuration changes."""

    def __init__(self, callback: Callable, actions: List[str] = None):
        self.callback = callback
        self.actions = actions or ["NEW", "UPDATE", "DELETE"]
        self.is_default = False  # Indicates if this is a default config callback

    def __hash__(self):
        """Hash based on the callback function and actions."""
        return hash(self.callback)

    def __call__(self, config_name: str, action: str, value: Any):
        """Call the callback with the config name, action, and value."""
        if action in self.actions:
            try:
                self.callback(config_name, action, value)
            except Exception as e:
                print(f"Error in config callback for {config_name}: {e}")


class Config:
    """
    ConfigStore subsystem for the Agent.

    This subsystem follows VOLTTRON's config store pattern:
    1. Configurations are stored on the server (centralized)
    2. Agents can push configs to the store and retrieve them
    3. Agents can watch for config changes
    """

    def __init__(self, agent: Agent):
        self._agent = agent
        self._config_callbacks: dict[str, list[ConfigCallback]] = {}
        self._default_configs = {}
        self._pending_subscriptions = []
        self._server_configs = {}
        self._connected = False
        self._watched_configs = set()
        self._new_default_configs_sent = False
        # Connect to relevant agent signals
        self._agent.core.onconnected.connect(self._on_connection_established)
        self._agent.core.onconfigure.connect(self._on_configure)

    def get(self, config_name: str) -> dict:
        """
        Get a configuration from the config store.
        Merges default config with server config, with server values taking precedence.
        """
        # Start with default config as the base
        merged_config = {}
        if config_name in self._default_configs:
            default_config = self._default_configs[config_name]
            if isinstance(default_config, dict):
                merged_config = default_config.copy()
            else:
                # If default is not a dict, use it as is
                merged_config = default_config

        # Check if we have a cached server version
        if config_name in self._server_configs:
            server_config = self._server_configs[config_name]
            if isinstance(merged_config, dict) and isinstance(server_config, dict):
                # Merge dictionaries - server config overrides defaults
                merged_config.update(server_config)
            else:
                # If either is not a dict, server config completely overrides
                merged_config = server_config

            async_result = AsyncResult()
            async_result.set(merged_config)
            return async_result.value

        # Try to get from the server (don't return early just because we have defaults)
        async_result = AsyncResult()

        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"

        # Use gevent to make the HTTP request asynchronously
        def fetch_config():
            try:
                with httpx.Client() as client:
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        server_config = data["data"]
                        self._server_configs[config_name] = server_config  # Cache the result

                        # Merge with defaults if both are dicts
                        final_config = merged_config
                        if isinstance(merged_config, dict) and isinstance(server_config, dict):
                            final_config = merged_config.copy()
                            final_config.update(server_config)
                        elif server_config is not None:
                            # Server config overrides if it's not None
                            final_config = server_config

                        async_result.set(final_config)
                    else:
                        # If server request fails, return defaults if available
                        async_result.set(merged_config if merged_config else {})
            except Exception:
                # If server request fails, return defaults if available
                async_result.set(merged_config if merged_config else {})

        result = gevent.spawn(fetch_config).get(timeout=5)
        if result is None:
            return merged_config if merged_config else {}

        return result

    def set(self, config_name: str, config_data: Any):
        """Set a configuration in the config store."""
        async_result = AsyncResult()

        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"

        # Use gevent to make the HTTP request asynchronously
        def store_config():
            try:
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.put(request_url, json=config_data)
                    if response.status_code == 200:
                        # Update local cache when store succeeds
                        self._server_configs[config_name] = config_data
                        async_result.set(True)
                    else:
                        async_result.set_exception(
                            Exception(f"Failed to store config: {response.text}")
                        )
            except Exception as e:
                async_result.set_exception(e)

        gevent.spawn(store_config)
        return async_result

    def delete(self, config_name: str):
        """Delete a configuration from the config store."""
        async_result = AsyncResult()

        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"

        # Use gevent to make the HTTP request asynchronously
        def delete_config():
            try:
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.delete(request_url)
                    if response.status_code == 200:
                        async_result.set(True)
                    else:
                        async_result.set_exception(
                            Exception(f"Failed to delete config: {response.text}")
                        )
            except Exception as e:
                async_result.set_exception(e)

        gevent.spawn(delete_config)
        return async_result

    def list(self):
        """List all configurations for this agent."""
        async_result = AsyncResult()

        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/list?agent_id={self._agent.identity}"

        # Use gevent to make the HTTP request asynchronously
        def list_configs():
            try:
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        if self._agent.identity in data["data"]:
                            async_result.set(data["data"][self._agent.identity])
                        else:
                            async_result.set([])  # No configs for this agent
                    else:
                        async_result.set_exception(
                            Exception(f"Failed to list configs: {response.text}")
                        )
            except Exception as e:
                async_result.set_exception(e)

        gevent.spawn(list_configs)
        return async_result

    def subscribe(self, callback, actions=None, pattern=None, config_name=None):
        """
        Subscribe to configuration changes.
        Args:
            callback: Function to call when matching changes occur
            actions: List of action types to subscribe to ('NEW', 'UPDATE', 'DELETE')
            pattern: Pattern to match against config names
            config_name: Specific config name to subscribe to (takes precedence over pattern)
        """
        if actions is None:
            actions = ["NEW", "UPDATE", "DELETE"]

        # If a specific config_name is provided, use that directly
        if pattern:
            # Register this callback for the specific config
            # TODO: pattern should allow a regular expression or wildcard matching, but is not at present
            if pattern not in self._config_callbacks:
                self._config_callbacks[pattern] = []

            # Add the callback if not already registered
            if callback not in self._config_callbacks[pattern]:
                self._config_callbacks[pattern].append(ConfigCallback(callback, actions))
                print(f"Registered callback for config: {pattern}")

            # If we already have this config (default or server), notify immediately
            if pattern in self._default_configs:
                value = self._default_configs[pattern]
                try:
                    callback(pattern, value)
                    print(f"Called callback with existing default config: {pattern}")
                except Exception as e:
                    print(f"Error calling callback for default {pattern}: {e}")
            elif pattern in self._server_configs:
                value = self._server_configs[pattern]
                try:
                    callback(pattern, value)
                    print(f"Called callback with existing server config: {pattern}")
                except Exception as e:
                    print(f"Error calling callback for server config {pattern}: {e}")

            # Return some identifier for this subscription
            return f"{pattern}:{len(self._config_callbacks[pattern])}"

        # For pattern-based subscriptions, use the old mechanism with the server
        subscription = {"callback": callback, "actions": actions, "pattern": pattern}

        if self._connected:
            return self._setup_subscription(subscription)

        self._pending_subscriptions.append(subscription)
        return len(self._pending_subscriptions)

    def unsubscribe(self, subscription_id):
        """Remove a configuration subscription."""
        return self._agent.vip.pubsub.unsubscribe(subscription_id)

    def handle_update(self, config_name):
        """Handle a configuration update notification from the server."""
        if config_name in self._config_callbacks:
            # Clear server cache for this config to force a fresh fetch
            if config_name in self._server_configs:
                del self._server_configs[config_name]

            # Get the updated merged config
            try:
                merged_config = self.get(config_name)

                # Get just the server part for caching
                # (The get() method will have already cached the server config)

                # Call all callbacks with the merged config
                for callback in self._config_callbacks[config_name]:
                    try:
                        callback(config_name, merged_config)
                    except Exception as e:
                        print(f"Error in config update callback: {e}")
            except Exception as e:
                print(f"Error fetching updated config {config_name}: {e}")

    def set_default(self, name, value):
        """
        Set a local default configuration value.
        Does not send to the server, just stores locally.
        """
        print(f"Setting default config: {name}")
        self._default_configs[name] = value

        # Notify any callbacks registered for this config name
        # if name in self._config_callbacks:
        #     for callback in self._config_callbacks[name]:
        #         try:
        #             callback(name, value)
        #             print(f"Notified callback about default config: {name}")
        #         except Exception as e:
        #             print(f"Error in config callback for {name}: {e}")

        return value

    def _on_configure(self, sender, **kwargs):
        """Called when the agent receives its configuration."""
        print(f"Agent {self._agent.identity} received configuration")
        configs = kwargs.get("configs", [])

        if not self._new_default_configs_sent:
            # Send all default configs to the server
            for name, value in self._default_configs.items():
                try:
                    for callback in self._config_callbacks.get(name, []):
                        callback(name, "NEW", value)
                    # self._send_default_config(name, "NEW", value)
                    print(f"Sent default config to server: {name} = {value}")
                except Exception as e:
                    print(f"Error sending default config {name}: {e}")

            self._new_default_configs_sent = True

        # for cfg in self._default_configs.values():
        #     if cfg.name in self._config_callbacks:
        #         for callback in self._config_callbacks[cfg.name]:
        #             callback(cfg.name, "NEW", cfg.value)

        # Process configs from server if available
        for cfg in configs:

            config_name = cfg.get("name")

            # Get the config from server
            config_data = self.get(config_name)
            # .get(timeout=5)
            self._server_configs[config_name] = config_data

            # Notify callbacks
            if config_name in self._config_callbacks:
                for callback in self._config_callbacks[config_name]:
                    try:
                        print(f"Calling callback for config: {config_name}")

                        callback(config_name, "NEW", config_data)
                    except Exception as e:
                        print(f"Error in config callback for {config_name}: {e}")

    def _on_connection_established(self, sender, **kwargs):
        """Called when connection to the server is established."""
        print(f"Agent {self._agent.identity} connected to server")
        self._connected = True

        # Set up all pending subscriptions with the server
        for subscription in self._pending_subscriptions:
            self._setup_subscription(subscription)

        self._pending_subscriptions = []

    def _setup_subscription(self, subscription):
        """Set up a pattern-based subscription with the server."""
        # Extract subscription details
        callback = subscription["callback"]
        actions = subscription["actions"]
        pattern = subscription["pattern"]

        # Register with the agent's pubsub system
        def handler(peer, sender, bus, topic, headers, message):
            if not isinstance(message, dict):
                message = json.loads(message)

            action = message.get("action", "NEW")
            if action in actions:
                config_name = message.get("name")
                config_value = message.get("value")

                # Update our server config cache
                if config_name and config_value is not None:
                    if action != "DELETE":
                        self._server_configs[config_name] = config_value
                    elif config_name in self._server_configs:
                        del self._server_configs[config_name]

                # Call the callback
                try:
                    callback(config_name, action, config_value)
                except Exception as e:
                    print(f"Error in config subscription callback: {e}")

        # Subscribe using the agent's VIP connection
        topic = f"config/{pattern}" if pattern else "config/*"
        return self._agent.vip.pubsub.subscribe(peer="pubsub", prefix=topic, callback=handler)

    def _send_default_config(self, name, value):
        """
        Send a default config to the server.

        Args:
            name: Configuration name
            value: Default value
        """
        # Send a message to the server to set the default config
        # This would need to match your server's API for setting defaults
        self._agent.vip.rpc.call("config.store", "set_default", name, value).get()


class CronTimer:
    """
    A timer that executes periodically based on a cron schedule.
    This is a simplified version of VOLTTRON's cron schedule parser.
    """

    def __init__(self, cron_pattern):
        """Initialize a cron timer with a cron pattern."""
        self.cron_pattern = cron_pattern

        # Parse the cron pattern
        self.minutes, self.hours, self.days_of_month, self.months, self.days_of_week = (
            self._parse_pattern(cron_pattern)
        )

    def _parse_pattern(self, pattern):
        """Parse a cron pattern into its components."""
        if pattern is None:
            raise ValueError("Cron pattern cannot be None")

        parts = pattern.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron pattern must have 5 components, got {len(parts)}: {pattern}")

        minutes = self._parse_component(parts[0], 0, 59)
        hours = self._parse_component(parts[1], 0, 23)
        days_of_month = self._parse_component(parts[2], 1, 31)

        # Parse months (by name or number)
        months = set()
        for month in self._parse_component(parts[3], 1, 12, is_months=True):
            if isinstance(month, str):
                month_num = self._month_name_to_number(month)
                months.add(month_num)
            else:
                months.add(month)

        # Parse days of week (by name or number, 0 or 7 = Sunday)
        days_of_week = set()
        for day in self._parse_component(parts[4], 0, 7, is_dow=True):
            if isinstance(day, str):
                day_num = self._day_name_to_number(day)
                days_of_week.add(
                    day_num if day_num < 7 else 0
                )  # Convert 7 to 0 (both represent Sunday)
            else:
                days_of_week.add(day if day < 7 else 0)  # Convert 7 to 0

        return minutes, hours, days_of_month, months, days_of_week

    def _parse_component(self, component, min_val, max_val, is_months=False, is_dow=False):
        """
        Parse a component of a cron pattern.

        Args:
            component: The component to parse (e.g., "1,2,3", "*/5", "1-5", etc.)
            min_val: The minimum valid value
            max_val: The maximum valid value
            is_months: Whether this component represents months
            is_dow: Whether this component represents days of week

        Returns:
            A set of values for the component
        """
        if component == "*":
            return set(range(min_val, max_val + 1))

        values = set()

        for part in component.split(","):
            if part == "*":
                values.update(range(min_val, max_val + 1))
                continue

            # Handle */n (every n units)
            if "/" in part:
                base, step = part.split("/", 1)
                if base == "*":
                    base_range = range(min_val, max_val + 1)
                else:
                    if "-" in base:
                        base_min, base_max = base.split("-", 1)
                        if is_months:
                            base_min = self._parse_month(base_min)
                            base_max = self._parse_month(base_max)
                        elif is_dow:
                            base_min = self._parse_dow(base_min)
                            base_max = self._parse_dow(base_max)
                        else:
                            base_min = int(base_min)
                            base_max = int(base_max)
                        base_range = range(base_min, base_max + 1)
                    else:
                        if is_months:
                            base = self._parse_month(base)
                        elif is_dow:
                            base = self._parse_dow(base)
                        else:
                            base = int(base)
                        base_range = range(base, max_val + 1)

                step = int(step)
                values.update(range(base_range[0], base_range[-1] + 1, step))
                continue

            # Handle ranges (e.g., 1-5)
            if "-" in part:
                start, end = part.split("-", 1)
                if is_months:
                    start = self._parse_month(start)
                    end = self._parse_month(end)
                elif is_dow:
                    start = self._parse_dow(start)
                    end = self._parse_dow(end)
                else:
                    start = int(start)
                    end = int(end)
                values.update(range(start, end + 1))
                continue

            # Handle single values
            if is_months:
                values.add(self._parse_month(part))
            elif is_dow:
                values.add(self._parse_dow(part))
            else:
                values.add(int(part))

        return values

    def _parse_month(self, month):
        """Parse a month name or number."""
        try:
            return int(month)
        except ValueError:
            return month.lower()

    def _parse_dow(self, dow):
        """Parse a day of week name or number."""
        try:
            return int(dow)
        except ValueError:
            return dow.lower()

    def _month_name_to_number(self, name):
        """Convert a month name to its corresponding number (1-12)."""
        name = name.lower()
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        for abbr, num in months.items():
            if name.startswith(abbr):
                return num
        raise ValueError(f"Invalid month name: {name}")

    def _day_name_to_number(self, name):
        """Convert a day of week name to its corresponding number (0-6, 0=Sunday)."""
        name = name.lower()
        days = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
        for abbr, num in days.items():
            if name.startswith(abbr):
                return num
        raise ValueError(f"Invalid day of week name: {name}")

    def get_next(self, now=None):
        """
        Get the next time this cron schedule should run.

        Args:
            now: The reference time (defaults to current time)

        Returns:
            The next scheduled time as a datetime object
        """
        if now is None:
            now = datetime.now()

        # Start from the next minute
        next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Check up to 1000 minutes ahead to avoid infinite loops
        for _ in range(10000):
            # Check if this time matches the schedule
            if (
                next_time.month in self.months
                and next_time.day in self.days_of_month
                and next_time.hour in self.hours
                and next_time.minute in self.minutes
                and next_time.weekday() in self.days_of_week
            ):
                return next_time

            # Increment to the next minute
            next_time += timedelta(minutes=1)

        # If we get here, we couldn't find a match within the limit
        raise ValueError("Could not find next scheduled time within reasonable limits")


class Peerlist:
    """Peerlist subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self._connected_peers = set()

    def add_peer(self, peer: str):
        """Add a peer to the list."""
        self._connected_peers.add(peer)
        print(f"Peer added: {peer}")

    def remove_peer(self, peer: str):
        """Remove a peer from the list."""
        self._connected_peers.discard(peer)
        print(f"Peer removed: {peer}")

    def list_peers(self) -> list | AsyncResult:
        """List all connected peers."""
        async_result = AsyncResult()
        async_result.set(list(self._connected_peers))
        return async_result

    def __call__(self) -> list | AsyncResult:
        """Return the list of connected peers."""
        return self.list_peers()


class ScheduledEvent:
    """A scheduled periodic event."""

    def __init__(self, function, interval_or_cron, args=None, kwargs=None, name=None):
        """
        Initialize a scheduled event.

        Args:
            function: The function to call
            interval_or_cron: Either a number of seconds (interval) or a cron expression
            args: Positional arguments to pass to the function
            kwargs: Keyword arguments to pass to the function
            name: Name of the event (defaults to function name)
        """
        self.function = function
        self.args = args or []
        self.kwargs = kwargs or {}
        self.name = name or function.__name__
        self.running = True
        self.periodic = True
        self.greenlet = None

        # Check if we have a cron expression or an interval
        self.is_cron = isinstance(interval_or_cron, str)

        if self.is_cron:
            # Cron schedule
            self.cron_expression = interval_or_cron
            self.cron_timer = CronTimer(interval_or_cron)
            next_time = self.cron_timer.get_next()
            self.next_time = time.mktime(next_time.timetuple())
        else:
            # Interval schedule
            self.interval = interval_or_cron
            self.next_time = time.time() + interval_or_cron

    def __lt__(self, other):
        """Compare based on next scheduled time."""
        return self.next_time < other.next_time

    def compute_next_time(self):
        """Compute the next execution time."""
        if self.is_cron:
            next_time = self.cron_timer.get_next(datetime.fromtimestamp(time.time()))
            self.next_time = time.mktime(next_time.timetuple())
        else:
            self.next_time = time.time() + self.interval

    def __str__(self):
        if self.is_cron:
            return f"ScheduledEvent({self.name}, cron='{self.cron_expression}', next_at={datetime.fromtimestamp(self.next_time)})"
        else:
            return f"ScheduledEvent({self.name}, interval={self.interval}, next_at={datetime.fromtimestamp(self.next_time)})"


class Scheduler:
    """Scheduler for periodic tasks."""

    def __init__(self, agent):
        """Initialize the scheduler."""
        self._agent = agent
        self._event_queue = []  # Priority queue of scheduled events
        self._events = {}  # Map of event names to event objects
        self._scheduler_greenlet = None
        self._stop_event = gevent.event.Event()

    def start(self):
        """Start the scheduler."""
        if self._scheduler_greenlet is None or self._scheduler_greenlet.dead:
            self._stop_event.clear()
            self._scheduler_greenlet = gevent.spawn(self._scheduler_loop)

    def stop(self):
        """Stop the scheduler."""
        if self._scheduler_greenlet:
            self._stop_event.set()
            self._scheduler_greenlet.join(timeout=2)
            self._scheduler_greenlet = None

    @staticmethod
    def cron(cronstring: str) -> str:
        data = cronstring.split()
        assert len(data) == 5, "Invalid cron string"
        invalid = False
        for d in data:
            if d == "*":
                pass
            else:
                result = eval(d)
                if not isinstance(result, (int, float)):
                    invalid = True

        if invalid:
            raise AssertionError("Invalid cron string")

        return cronstring

    def schedule(self, function, interval_or_cron, args=None, kwargs=None, name=None):
        """
        Schedule a periodic function.

        Args:
            function: The function to call
            interval_or_cron: Either a number of seconds (interval) or a cron expression
            args: Positional arguments to pass to the function
            kwargs: Keyword arguments to pass to the function
            name: Name of the event (defaults to function name)

        Returns:
            The name of the scheduled event
        """
        if isinstance(interval_or_cron, numbers.Number):
            if interval_or_cron <= 0:
                raise ValueError("Interval must be a positive number")
        elif not isinstance(interval_or_cron, str):
            raise ValueError(
                "Schedule must be either a positive number (interval) or a cron expression"
            )

        name = name or function.__name__

        # Create a new event
        event = ScheduledEvent(function, interval_or_cron, args, kwargs, name)

        # Add to the event queue and map
        self._events[name] = event
        heapq.heappush(self._event_queue, event)

        return name

    def cancel(self, name):
        """Cancel a scheduled event."""
        if name in self._events:
            event = self._events.pop(name)
            event.running = False
            # Note: The event may still be in the queue, but we'll skip it
            # when it comes up in the scheduler loop
            return True
        return False

    def update_interval(self, name, interval):
        """Update the interval of a scheduled event."""
        if name in self._events:
            event = self._events[name]

            if event.is_cron:
                raise ValueError("Cannot update interval for a cron-based event")

            if not isinstance(interval, numbers.Number) or interval <= 0:
                raise ValueError("Interval must be a positive number")

            event.interval = interval
            event.next_time = time.time() + interval

            # Rebuild the queue to maintain the heap property
            self._rebuild_queue()
            return True
        return False

    def update_cron(self, name, cron_expression):
        """Update the cron expression of a scheduled event."""
        if name in self._events:
            event = self._events[name]

            if not event.is_cron:
                raise ValueError("Cannot update cron for an interval-based event")

            try:
                from croniter import croniter

                # Validate cron expression
                if not croniter.is_valid(cron_expression):
                    raise ValueError(f"Invalid cron expression: {cron_expression}")

                event.cron_expression = cron_expression
                event.iter = croniter(cron_expression, datetime.now())
                event.next_time = event.iter.get_next(float)

                # Rebuild the queue to maintain the heap property
                self._rebuild_queue()
                return True
            except ImportError:
                raise ImportError("croniter package is required for cron schedules.")
        return False

    def list_events(self):
        """List all scheduled events."""
        return {
            name: {
                "interval" if not event.is_cron else "cron": (
                    event.interval if not event.is_cron else event.cron_expression
                ),
                "next_time": datetime.fromtimestamp(event.next_time).isoformat(),
                "running": event.running,
            }
            for name, event in self._events.items()
        }

    def _rebuild_queue(self):
        """Rebuild the event queue."""
        events = list(self._events.values())
        self._event_queue = []
        for event in events:
            if event.running:
                heapq.heappush(self._event_queue, event)

    def _scheduler_loop(self):
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            now = time.time()

            # Process events that are due
            while self._event_queue and self._event_queue[0].next_time <= now:
                event = heapq.heappop(self._event_queue)

                # Skip if event was cancelled
                if event.name not in self._events or not event.running:
                    continue

                # Execute the function in a new greenlet
                try:
                    gevent.spawn(event.function, *event.args, **event.kwargs)
                except Exception as e:
                    print(f"Error spawning periodic task {event.name}: {e}")

                # Compute the next execution time
                event.compute_next_time()

                # Reschedule the event
                heapq.heappush(self._event_queue, event)

            # Sleep until the next event or a short timeout
            sleep_time = 0.1  # Default sleep time
            if self._event_queue:
                next_time = self._event_queue[0].next_time
                sleep_time = max(0, min(next_time - time.time(), 0.5))

            gevent.sleep(sleep_time)


class Signal:
    """A simple signal/slot implementation for event handling."""

    def __init__(self, name):
        self.name = name
        self._handlers = []

    def connect(self, handler):
        """Connect a handler function to this signal."""
        if handler not in self._handlers:
            self._handlers.append(handler)
        return handler  # Return handler for potential chaining

    def disconnect(self, handler):
        """Disconnect a handler function from this signal."""
        if handler in self._handlers:
            self._handlers.remove(handler)
        return handler

    def fire(self, sender, **kwargs):
        """Fire the signal, calling all connected handlers."""
        for handler in self._handlers[
            :
        ]:  # Copy to avoid issues if handlers are added/removed during iteration
            try:
                gevent.spawn(handler, sender, **kwargs)
            except Exception as e:
                print(f"Error in {self.name} handler: {e}")


class Agent:
    """A gevent-based agent that connects to the VOLTTRON MessageBus."""

    def __init__(
        self,
        identity: str,
        host: str = "127.0.0.1",
        port: int = 8000,
        config_path: str = None,
        **kwargs,
    ):
        self._logger = logging.getLogger("Agent")
        self.identity = identity
        self._host = host
        self._port = port
        self.config_path = config_path
        self.websocket_url = f"ws://{host}:{port}/ws/{identity}"
        self.websocket = None
        self.connected = False
        self.received_messages = []
        self._listener_greenlet = None
        self.rpc_responses = {}  # Maps message IDs to AsyncResults
        self._stop_event = gevent.event.Event()  # type: ignore

        # Create subsystems
        self.core = Core(self)
        self.config = Config(self)  # Initialize config before VIP

        # Initialize VIP with all subsystems
        self.vip = VIP(self)

        # Register any methods decorated with @Core.receiver or @RPC.export
        self.core._register_decorated_methods(self)
        self.vip.rpc._register_decorated_methods(self)

    def connect(self):
        """Connect to the message bus."""
        # Enable trace for debugging if needed
        # websocket.enableTrace(True)

        # Create a WebSocketApp
        self.websocket = websocket.WebSocketApp(
            self.websocket_url,
            on_message=self.__on_ws_message__,
            on_error=self.__on_ws_error__,
            on_close=self.__on_ws_close__,
            on_open=self.__on_ws_open__,
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
                raise ConnectionError(f"Connection timeout for agent {self.identity}")

        print(f"Agent {self.identity} connected")

        # Fire the onconnected event with self as sender
        self.core.fire_event("onconnected", sender=self)

        # After onconnected but before onstart, load configurations
        self._load_configs()

        # Fire the onstart event with self as sender
        self.core.fire_event("onstart", sender=self)

        # Start periodic tasks
        self.core.start_periodic_tasks()

    def _load_configs(self):
        """Load configurations and trigger the onconfigure event."""
        try:
            # List available configurations for this agent
            configs = self.config.list().get(timeout=5)

            # If a config_path was provided and no configs are found,
            # try to load the configuration from the file
            if self.config_path and not configs and self.connected:
                self._load_config_from_path()

            # Fire the onconfigure event
            self.core.fire_event("onconfigure", sender=self, configs=configs)

        except Exception as e:
            print(f"Error loading configurations: {e}")

    def _load_config_from_path(self):
        """
        Load configuration from the specified config_path and push it to the server's config store.

        This follows the VOLTTRON pattern where local file configs are pushed to the
        platform's config store, and then agents retrieve them from there.
        """
        import os
        import json

        if not self.config_path or not os.path.exists(self.config_path):
            return

        try:
            self._logger.debug(f"Loading configuration from file: {self.config_path}")
            with open(self.config_path, "r") as f:
                import yaml

                config_data = yaml.safe_load(f)
                print("After loaind configuration from path")

                if self.config_path.endswith(".json"):
                    config_data = json.load(f)
                elif self.config_path.endswith((".yml", ".yaml")):
                    import yaml

                    config_data = yaml.safe_load(f)
                else:
                    print(f"Unsupported config file format: {self.config_path}")
                    return

                # Push the config to the server's config store
                self.config.set("config", config_data).get(timeout=5)
                print(f"Pushed configuration from {self.config_path} to the server's config store")

                # We don't need to store the config locally here, as we'll retrieve it
                # from the server during the onconfigure phase
        except Exception as e:
            print(f"Error loading configuration from {self.config_path}: {e}")
            # TODO: Implement proper health status tracking
            # self.health.set_status(Status.WARNING, f"Config load error: {e}")

    def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            # Fire the onstop event with self as sender
            self.core.fire_event("onstop", sender=self)

            self.websocket.close()
            # Wait for the close to complete
            if self._listener_greenlet:
                self._listener_greenlet.join(timeout=1)

            self.connected = False
            print(f"Agent {self.identity} disconnected")

            # Fire the ondisconnected event with self as sender
            self.core.fire_event("ondisconnected", sender=self)

    def __on_ws_open__(self, ws):
        """Callback when WebSocket connection is opened."""
        self.connected = True
        print(f"DEBUG: Agent {self.identity} websocket connection opened")

    def __on_ws_message__(self, ws, message):
        """Internal callback when a WebSocket message is received."""
        try:
            data = json.loads(message)
            self.received_messages.append(data)
            print(f"Agent {self.identity} received data.")

            # Handle different message types
            msg_type = data.get("type")

            if msg_type == "pubsub":
                # Handle pubsub messages
                self.vip.pubsub.handle_message(data)
            elif msg_type == "config_update":
                # Handle config update notifications
                config_name = data.get("config_name")
                if config_name:
                    self.config.handle_update(config_name)

            elif msg_type == "config_delete":
                # Handle config delete notifications
                config_name = data.get("config_name")
                if config_name and config_name in self.config._config_callbacks:
                    # Notify callbacks with None to indicate deletion
                    for callback in self.config._config_callbacks[config_name]:
                        try:
                            callback(config_name, None)
                        except Exception as e:
                            print(f"Error in config delete callback: {e}")
            elif msg_type == "rpc_request":
                # Handle RPC request
                print(f"DEBUG: Agent {self.identity} received RPC request: {data}")
                sender = data.get("sender")
                method_name = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                msg_id = data.get("msg_id")

                # Process the RPC request - returns an AsyncResult
                async_result = self.vip.rpc.handle_request(
                    sender, method_name, args, kwargs, msg_id
                )

                # Wait for the result and send the response
                def send_response():
                    try:
                        # Wait for the result (with timeout)
                        result = async_result.get(timeout=10)
                        # Send successful response
                        print(f"DEBUG: Agent {self.identity} sending RPC response: {result}")
                        self.websocket.send(
                            json.dumps({"type": "rpc_response", "msg_id": msg_id, "result": result})
                        )
                    except Exception as e:
                        # Send error response
                        error = str(e)
                        print(f"DEBUG: Agent {self.identity} sending RPC error response: {error}")
                        self.websocket.send(
                            json.dumps({"type": "rpc_error", "msg_id": msg_id, "error": error})
                        )

                # Spawn a greenlet to process the response asynchronously
                gevent.spawn(send_response)

            elif msg_type == "rpc_response":
                # Handle RPC response
                msg_id = data.get("msg_id")
                result = data.get("result")
                print(
                    f"DEBUG: Agent {self.identity} received RPC response for msg_id {msg_id}: {result}"
                )
                if msg_id in self.rpc_responses:
                    # Get the AsyncResult for this message ID and set its result
                    async_result = self.rpc_responses.pop(msg_id)
                    async_result.set(result)
                else:
                    print(f"DEBUG: No pending RPC request found for msg_id {msg_id}")

            elif msg_type == "rpc_error":
                # Handle RPC error
                msg_id = data.get("msg_id")
                error = data.get("error", "Unknown RPC error")
                print(
                    f"DEBUG: Agent {self.identity} received RPC error for msg_id {msg_id}: {error}"
                )
                if msg_id in self.rpc_responses:
                    # Get the AsyncResult for this message ID and set the exception
                    async_result = self.rpc_responses.pop(msg_id)
                    # Create proper volttron-core compatible exception
                    if isinstance(error, dict) and "code" in error:
                        # JSON-RPC style error with code, message, data
                        exception = exception_from_json(
                            error.get("code", -32603),
                            error.get("message", "Internal Error"),
                            error.get("data")
                        )
                    else:
                        # Simple string error - use our fallback or basic Exception
                        try:
                            exception = RemoteError(str(error))
                        except Exception:
                            # Fallback if RemoteError has issues
                            exception = Exception(f"Remote error: {error}")
                    async_result.set_exception(exception)
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
                        # Get the AsyncResult and set its value
                        async_result = self.rpc_responses.pop(msg_id)
                        async_result.set(args[0])  # Assuming first arg is result
                elif subsystem == "rpc_error":
                    # This is an RPC error via VIP
                    msg_id = message.get("msg_id")
                    args = message.get("args", [])
                    if msg_id in self.rpc_responses and args:
                        # Get the AsyncResult and set the exception
                        async_result = self.rpc_responses.pop(msg_id)
                        # Create proper volttron-core compatible exception
                        error_data = args[0]  # Assuming first arg is error
                        if isinstance(error_data, dict) and "code" in error_data:
                            # JSON-RPC style error
                            exception = exception_from_json(
                                error_data.get("code", -32603),
                                error_data.get("message", "Internal Error"),
                                error_data.get("data")
                            )
                        else:
                            # Simple error - use fallback if RemoteError has issues
                            try:
                                exception = RemoteError(str(error_data))
                            except Exception:
                                # Fallback if RemoteError has issues
                                exception = Exception(f"Remote error: {error_data}")
                        async_result.set_exception(exception)

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

            # Process the RPC request - returns an AsyncResult
            async_result = self.vip.rpc.handle_request(peer, method_name, method_args, {}, msg_id)

            # Wait for the result and send the response via VIP
            def send_vip_response():
                try:
                    # Wait for the result (with timeout)
                    result = async_result.get(timeout=10)
                    # Send successful response via VIP
                    self.vip.send_message(
                        peer=peer, subsystem="rpc_response", args=[result, msg_id]
                    )
                except Exception as e:
                    # Send error response via VIP
                    self.vip.send_message(peer=peer, subsystem="rpc_error", args=[str(e), msg_id])

            # Spawn a greenlet to process the response asynchronously
            gevent.spawn(send_vip_response)

    def __on_ws_error__(self, ws, error):
        """Callback when an error occurs."""
        print(f"Agent {self.identity} error: {error}")

    def __on_ws_close__(self, ws, close_status_code, close_msg):
        """Callback when the connection is closed."""
        self.connected = False
        print(f"Agent {self.identity} connection closed: {close_status_code} {close_msg}")

    def get_received_messages(self):
        """Get all received messages."""
        return self.received_messages

    def clear_received_messages(self):
        """Clear the received messages list."""
        self.received_messages = []

    def run(self):
        """Run the agent and return exit code when finished."""
        try:
            # Connect to the message bus
            print(f"Starting agent: {self.identity}")
            self.connect()

            # Keep the agent running until stopped
            print(f"Agent {self.identity} running. Press Ctrl+C to stop.")
            while not self._stop_event.is_set():
                gevent.sleep(1.0)  # Sleep to avoid busy waiting

            return 0  # Success

        except KeyboardInterrupt:
            print(f"\nKeyboard interrupt received, stopping agent: {self.identity}")
        except Exception as e:
            print(f"Error running agent {self.identity}: {e}")
            import traceback

            traceback.print_exc()
            return 1  # Error
        finally:
            # Ensure proper shutdown
            try:
                self.core.stop().get(timeout=5)
                print(f"Agent {self.identity} stopped cleanly")
            except Exception as e:
                print(f"Error stopping agent {self.identity}: {e}")
                return 1  # Error

    def stop(self):
        """Signal the agent to stop."""
        self._stop_event.set()


# src/aems/client/agent.py - Updated run_agent function


def run_agent(agent_class, config_path=None, identity=None, **kwargs):
    """
    Run an agent from the command line.

    Args:
        agent_class: The Agent class to instantiate
        config_path: Path to the agent's configuration file (JSON, YAML, etc.)
        identity: Agent identity, if None will be derived from agent class name
        **kwargs: Additional keyword arguments to pass to the agent constructor

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    import argparse
    import os
    import json
    import yaml

    identity = identity or os.environ.get("AGENT_VIP_IDENTITY", None)

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Agent configuration file", default=config_path)
    parser.add_argument("--identity", help="Agent identity", default=identity)
    parser.add_argument("--host", help="Message bus host", default="127.0.0.1")
    parser.add_argument("--port", help="Message bus port", type=int, default=8000)
    parser.add_argument(
        "--volttron-home", help="VOLTTRON_HOME directory", default=os.environ.get("VOLTTRON_HOME")
    )

    args = parser.parse_args()

    # Set VOLTTRON_HOME environment variable if provided
    if args.volttron_home:
        os.environ["VOLTTRON_HOME"] = args.volttron_home
        print(f"Using VOLTTRON_HOME: {args.volttron_home}")

    # Use command line config path if provided, otherwise use the argument
    config_path = args.config or config_path
    agent_config = {}

    # Load the configuration file if it exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                try:
                    agent_config = yaml.safe_load(f)
                except ImportError:
                    try:
                        agent_config = json.load(f)
                    except json.JSONDecodeError:
                        print(
                            f"Error decoding JSON from {config_path}. Ensure it is a valid JSON file."
                        )

                    print(f"Unsupported config file format: {config_path}")
        except Exception as e:
            print(f"Error loading configuration from {config_path}: {e}")
            return 1

    # Create the agent
    agent_identity = args.identity or identity or agent_class.__name__.lower()

    agent = agent_class(
        identity=agent_identity, host=args.host, port=args.port, config_path=config_path, **kwargs
    )

    # Set initial configuration if loaded from file
    if agent_config:
        # Store the config in the agent's config store
        if hasattr(agent, "config") and hasattr(agent.config, "set"):
            try:
                agent.config.set("config", agent_config).get(timeout=5)
                print(f"Loaded configuration from {config_path}")
            except Exception as e:
                print(f"Error storing initial configuration: {e}")

    # Run the agent
    try:
        run = agent.run
    except AttributeError:
        run = agent.core.run
    task = gevent.spawn(run)
    try:
        task.join()
    finally:
        task.kill()
