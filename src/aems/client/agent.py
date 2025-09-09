from __future__ import annotations

import ast
import contextlib
import copy
import heapq
import json
import logging
import numbers
import os
import ssl
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pprint import pformat
from typing import Any

import gevent
import httpx
import websocket
from gevent import monkey
from gevent.event import AsyncResult

from aems.client import dualmethod

# Use volttron-core JSON-RPC utilities for compatibility
try:
    from volttron.utils.jsonrpc import RemoteError, exception_from_json

    VOLTTRON_JSONRPC_AVAILABLE = True
except ImportError:
    # Fallback to our custom implementation
    from .jsonrpc import RemoteError, exception_from_json

    VOLTTRON_JSONRPC_AVAILABLE = False

# Patch standard library to work with gevent
monkey.patch_all()

# Set up logging for the agent module
_log = logging.getLogger(__name__)

SIZE_OUTPUT = 100


def get_smaller_print(data, in_str_full_value: str | None = None):
    if isinstance(data, str):
        if in_str_full_value and in_str_full_value in data:
            return data

        return data[:SIZE_OUTPUT] + "..." if len(data) > SIZE_OUTPUT else data
    else:
        if isinstance(data, dict | list):
            return get_smaller_print(json.dumps(data, default=str))
    return data


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
            method.rpc_exported = True
            method.rpc_name = None  # Use the method's name
            return method

        # Handle the case where decorator is used with parentheses
        def decorator(f):
            f.rpc_exported = True
            f.rpc_name = name
            return f

        return decorator

    def export_method(self, method_name: str, method: Callable):
        """Programmatically export an RPC method that can be called remotely."""
        self._exported_methods[method_name] = method
        _log.debug(f"Agent {self._agent.identity} exported RPC method: {method_name}")
        return method  # Return the method for chaining

    def _register_decorated_methods(self, agent):
        """Find and register methods decorated with @RPC.export."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "rpc_exported"):
                # Use the custom name if provided, otherwise use the method's name
                method_name = attr.rpc_name or attr_name
                self._exported_methods[method_name] = attr
                _log.debug(f"Agent {self._agent.identity} exported RPC method: {method_name} (from decorator)")

    def call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another agent, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        new_args = []
        for arg in args:
            if type(arg).__name__ == "Topic":
                new_args.append(str(arg))
            else:
                new_args.append(arg)
        msg_id = str(uuid.uuid4())
        async_result = AsyncResult()
        self._agent.rpc_responses[msg_id] = async_result

        _log.debug(f"Agent {self._agent.identity} making RPC call to {peer}.{method} with msg_id {msg_id}")

        self._agent.websocket.send(
            json.dumps(
                {
                    "type": "rpc",
                    "peer": peer,
                    "method": method,
                    "args": new_args,
                    "kwargs": kwargs,
                    "msg_id": msg_id,
                }
            )
        )

        _log.debug(
            f"Agent {self._agent.identity} sent RPC call to {peer}: method={method}, args={args}, kwargs={kwargs}"
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
        # First check if this is a locally exported method (even if it has dots in the name like config.update)
        if method_name in self._exported_methods:
            # Handle as a local method - jump to the local method execution logic
            # which properly uses greenlets to prevent blocking
            pass  # Fall through to local method handling below

        # Parse method name for potential remote calls (only if not a local method)
        elif "." in method_name:
            parts = method_name.split(".")
            # This is a remote call to another agent
            target = parts[0]
            actual_method = ".".join(parts[1:])
            _log.debug(f"Remote call detected: {target}.{actual_method}")

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
                _log.debug(error_msg)
                async_result = AsyncResult()
                async_result.set_exception(Exception(error_msg))
                return async_result

        # This is a local method call (or falls through from local method check above)
        async_result = AsyncResult()

        if method_name in self._exported_methods:
            # Execute RPC methods in greenlets to prevent blocking nested RPC calls
            method = self._exported_methods[method_name]
            _log.debug(f"Agent {self._agent.identity} executing method {method_name}")

            def execute_method():
                try:
                    result = method(*args, **kwargs)
                    _log.debug(f"Agent {self._agent.identity} method {method_name} result: {result}")
                    async_result.set(result)
                except Exception as e:
                    error = str(e)
                    _log.error(f"Agent {self._agent.identity} method {method_name} error: {error}")
                    async_result.set_exception(e)

            # Spawn the method execution in a greenlet to prevent blocking
            gevent.spawn(execute_method)
        else:
            error = f"Method {method_name} not found or not exported"
            _log.debug(error)
            async_result.set_exception(Exception(error))

        return async_result


class PubSub:
    """PubSub subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self._subscriptions = {}

    def publish(self, peer: str, topic: str, message: Any, headers: dict | None = None, bus: str = ""):
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
                _log.info(f"Agent {self._agent.identity} published to {topic}: default update sent")
            else:
                _log.info(f"Agent {self._agent.identity} published to {topic}: {get_smaller_print(message)}")
            async_result.set(True)  # Success
        except Exception as e:
            _log.error(f"Error publishing message: {e}")
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
                _log.error(f"Error in subscription callback: {e}")
                traceback.print_exc()
                # Return None when there's an error
                return None

        return adapter

    def subscribe(self, peer_or_prefix, prefix=None, callback: Callable | None = None, **kwargs):
        """Subscribe to a topic prefix, returning an AsyncResult.

        Args:
            peer_or_prefix: Either the peer (VOLTTRON API) or prefix (legacy API)
            prefix: The topic prefix (VOLTTRON API) or callback (legacy API)
            callback: Optional callback function to handle messages
            **kwargs: Additional keyword arguments
        """
        # Handle both API signatures:
        # 1. VOLTTRON API: subscribe(peer, prefix, callback)
        # 2. Legacy API: subscribe(prefix, callback)
        if callback is None and prefix is not None and callable(prefix):
            # Legacy API: subscribe(prefix, callback) where callback is in prefix parameter
            actual_prefix = peer_or_prefix
            actual_callback = prefix
        elif prefix is None:
            # Legacy API: subscribe(prefix, callback) where callback is None/default
            actual_prefix = peer_or_prefix
            actual_callback = callback
        else:
            # VOLTTRON API: subscribe(peer, prefix, callback)
            actual_prefix = prefix
            actual_callback = callback
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        subscription_id = str(uuid.uuid4())

        # Store the original callback
        final_callback = actual_callback or (lambda msg: _log.info(f"Subscription callback for {actual_prefix}: {msg}"))

        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(final_callback)

        self._subscriptions[actual_prefix] = adapted_callback

        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()

        try:
            # Send subscription message
            self._agent.websocket.send(
                json.dumps({"type": "subscribe", "prefix": actual_prefix, "id": subscription_id})
            )

            _log.info(f"Agent {self._agent.identity} subscribed to prefix: {actual_prefix}")
            async_result.set(subscription_id)  # Return the subscription ID
        except Exception as e:
            _log.error(f"Error subscribing to topic: {e}")
            async_result.set_exception(e)

        return async_result

    def subscribe_regex(self, peer_or_pattern, pattern=None, callback: Callable | None = None):
        """Subscribe to a topic pattern, returning an AsyncResult.

        Args:
            peer_or_pattern: Either the peer (VOLTTRON API) or pattern (legacy API)
            pattern: The regex pattern (VOLTTRON API) or callback (legacy API)
            callback: Optional callback function to handle messages
        """
        # Handle both API signatures:
        # 1. VOLTTRON API: subscribe_regex(peer, pattern, callback)
        # 2. Legacy API: subscribe_regex(pattern, callback)
        if callback is None and pattern is not None and callable(pattern):
            # Legacy API: subscribe_regex(pattern, callback) where callback is in pattern parameter
            actual_pattern = peer_or_pattern
            actual_callback = pattern
        elif pattern is None:
            # Legacy API: subscribe_regex(pattern, callback) where callback is None/default
            actual_pattern = peer_or_pattern
            actual_callback = callback
        else:
            # VOLTTRON API: subscribe_regex(peer, pattern, callback)
            actual_pattern = pattern
            actual_callback = callback
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")

        subscription_id = str(uuid.uuid4())

        # Store the original callback
        final_callback = actual_callback or (
            lambda msg: _log.info(f"Subscription callback for {actual_pattern}: {msg}")
        )

        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(final_callback)

        self._subscriptions[actual_pattern] = adapted_callback

        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()

        try:
            # Send subscription message with pattern
            self._agent.websocket.send(
                json.dumps({"type": "subscribe", "pattern": actual_pattern, "id": subscription_id})
            )

            _log.info(f"Agent {self._agent.identity} subscribed to pattern: {actual_pattern}")
            async_result.set(subscription_id)  # Return the subscription ID
        except Exception as e:
            _log.error(f"Error subscribing to pattern: {e}")
            async_result.set_exception(e)

        return async_result

    def get_subscriptions(self):
        """Get all active subscriptions."""
        return list(self._subscriptions.keys())

    def handle_message(self, data: dict):
        """Handle an incoming pubsub message."""
        topic = data.get("topic", "")

        # Find matching subscriptions and call their callbacks
        for prefix, callback in self._subscriptions.items():
            if topic.startswith(prefix) or prefix in ["", "*", "all"]:
                try:
                    callback(data)
                except Exception as e:
                    _log.error(f"Error calling subscription callback: {e}")
                    traceback.print_exc()


class VIP:
    """VIP subsystem for the Agent."""

    def __init__(self, agent):
        self._agent = agent
        self.rpc = RPC(agent)
        self.pubsub = PubSub(agent)
        # Use the agent's existing config instance instead of creating a new one
        self.config = agent.config
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

            _log.debug(f"Agent {self._agent.identity} sent VIP message to {peer}: subsystem={subsystem}, args={args}")
            async_result.set(msg_id)  # Return the message ID
        except Exception as e:
            _log.error(f"Error sending VIP message: {e}")
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
        self._handlers = {event: [] for event in self._signals}
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

        Returns
        -------
            The name of the scheduled event
        """
        # Convert datetime (object or string) to cron expression if needed
        if isinstance(interval_or_cron, datetime):
            # Convert datetime object to cron expression: minute hour day month dayofweek
            cron_expr = (
                f"{interval_or_cron.minute} {interval_or_cron.hour} {interval_or_cron.day} {interval_or_cron.month} *"
            )
            interval_or_cron = cron_expr
        elif isinstance(interval_or_cron, str) and not self._is_cron_expression(interval_or_cron):
            # Try to parse as datetime string
            try:
                # Parse common datetime string formats
                dt = self._parse_datetime_string(interval_or_cron)
                cron_expr = f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"
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
        return self._scheduler.cancel()

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
            method.event_name = event_name
            return method

        return decorator

    @dualmethod
    def periodic(self, interval_or_cron: int | str, function):
        """Schedule a periodic function and return a greenlet that can be killed."""
        if isinstance(interval_or_cron, numbers.Number):
            # For interval-based tasks, create a dedicated greenlet
            def periodic_wrapper():
                while True:
                    try:
                        function()
                        gevent.sleep(interval_or_cron)
                    except gevent.GreenletExit:
                        break
                    except Exception as e:
                        _log.error(f"Error in periodic task: {e}")
                        gevent.sleep(interval_or_cron)

            greenlet = gevent.spawn(periodic_wrapper)
            return greenlet
        else:
            # For cron expressions, fall back to scheduler
            # Note: This won't return a killable greenlet, but cron is not implemented yet
            self._scheduler.schedule(function, interval_or_cron)
            return None

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
            method.periodic = True
            method.interval_or_cron = interval_or_cron
            return method

        return decorator

    def _register_periodic_methods(self, agent):
        """Find and register methods decorated with @Core.periodic."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "periodic") and hasattr(attr, "interval_or_cron"):
                interval_or_cron = attr.interval_or_cron
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
                event_name = attr.event_name
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
                    _log.error(f"Error in {event_name} handler: {e}")


class ConfigCallback:
    """A callback for configuration changes."""

    def __init__(self, callback: Callable, actions: list[str] = None):
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
                _log.error(f"Error in config callback for {config_name}: {e}")
                import traceback

                traceback.print_exc()


class Config:
    """
    ConfigStore subsystem for the Agent.

    This subsystem follows VOLTTRON's config store pattern:
    1. Configurations are stored on the server (centralized)
    2. Agents can push configs to the store and retrieve them
    3. Agents can watch for config changes

    When updating or deleting configurations, the system can optionally trigger callbacks
    to subscribers immediately, instead of waiting for a server notification.
    """

    def __init__(self, agent: Agent):
        self._agent = agent
        self._config_callbacks: dict[str, list[ConfigCallback]] = {}
        self._default_configs = {}
        self._pending_subscriptions = []
        self._connected = False
        self._watched_configs = set()
        self._initial_connection_processed = False
        self._config_cache = {}  # Comprehensive cache of all config entries (default + server)
        self._debug_task = None  # For periodic debug output
        self._callback_depth = 0  # Track callback depth for reentrancy protection
        # No longer need to track pending updates - server handles this properly
        self._last_config_msg_ids = {}  # Track message IDs to prevent duplicate callbacks

        _log.info(f"ConfigStore created for agent {agent.identity}")

        # Connect to relevant agent signals
        self._agent.core.onconnected.connect(self._on_connection_established)
        self._agent.core.onconfigure.connect(self._on_update_from_server)  # Renamed method

    def _execute_callbacks_safely(self, config_name: str, action: str, config_value: Any):
        """
        Execute callbacks with reentrancy protection to prevent infinite loops.

        Args:
            config_name: Name of the configuration
            action: Action type (UPDATE, DELETE, etc.)
            config_value: The configuration value
        """
        # Allow callbacks but prevent deep recursion
        if self._callback_depth > 5:  # Prevent deep recursion chains
            _log.warning(
                f"Deep callback recursion detected: Skipping callbacks for config {config_name} during {action} (depth: {self._callback_depth})"
            )
            return

        # VOLTTRON-style pattern matching for callbacks
        import fnmatch

        callbacks_to_execute = []

        for pattern, callbacks in self._config_callbacks.items():
            if fnmatch.fnmatchcase(config_name, pattern):
                callbacks_to_execute.extend(callbacks)

        if not callbacks_to_execute:
            return

        try:
            self._callback_depth += 1
            for config_callback in callbacks_to_execute:
                try:
                    # Use the ConfigCallback's __call__ method which handles action filtering
                    _log.info(f"Calling callback for config {config_name} with {action} action")
                    config_callback(config_name, action, config_value)
                except Exception as e:
                    _log.error(f"Error in config update callback: {e}")
        finally:
            self._callback_depth -= 1

    def _has_server_config(self, config_name: str) -> bool:
        """
        Determine if a configuration exists on the server (not just default).
        Returns True if the configuration exists in the cache but not in defaults,
        or if it exists in both but has been modified from the default.
        """
        # If it's in the cache but not in defaults, it must be from server
        if config_name in self._config_cache and config_name not in self._default_configs:
            return True

        # If it's in both, check if they're different
        if config_name in self._config_cache and config_name in self._default_configs:
            default_value = self._default_configs[config_name]
            cached_value = self._config_cache[config_name]

            # If both are dictionaries, they might have been merged
            if isinstance(default_value, dict) and isinstance(cached_value, dict):
                # Check if cache has keys not in default
                return any(key not in default_value or cached_value[key] != default_value[key] for key in cached_value)
            else:
                # For non-dict values, simply compare them
                return default_value != cached_value

        return False

    def get(self, config_name: str) -> dict:
        """
        Get a configuration from the config store.
        Returns a deep copy of locally cached data if available.
        Merges default config with server config, with server values taking precedence.
        Always returns a deep copy to prevent inadvertent modifications to cached data.
        Raises KeyError if the config is not found in the cache.
        """
        # Check if we have the config in our comprehensive cache
        if config_name in self._config_cache:
            # Return a deep copy to prevent modification of cached data
            return copy.deepcopy(self._config_cache[config_name])

        # If not in cache, build it from default and server configs
        merged_config = {}
        if config_name in self._default_configs:
            default_config = self._default_configs[config_name]
            if isinstance(default_config, dict):
                # Use deepcopy to ensure we don't modify the original
                merged_config = copy.deepcopy(default_config)
            else:
                # If default is not a dict, use it as is (primitive values are immutable)
                merged_config = default_config

            # Since there's no server config, use only the default
            self._config_cache[config_name] = merged_config
            return copy.deepcopy(merged_config)

        # If we get here, there's no default and no cached server config
        raise KeyError(f"Configuration '{config_name}' not found in cache")

    def set(self, config_name: str, config_data: Any, send_update: bool = True):
        """
        Set a configuration in the config store.

        Args:
            config_name: Name of the configuration
            config_data: Configuration data to store
            send_update: Whether to call subscribed callbacks (default: True)
        """
        async_result = AsyncResult()

        # Prepare request
        request_url = (
            f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
        )

        # No need to track pending updates anymore - server handles this properly
        # Server will not send RPC notification when send_update=False for self-updates

        # Use gevent to make the HTTP request asynchronously
        def store_config():
            try:
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    # Include requesting agent identity for access control and send_update flag
                    params = {"requesting_agent": self._agent.identity, "send_update": send_update}
                    response = client.put(request_url, json=config_data, params=params)
                    if response.status_code == 200:
                        # Update comprehensive cache
                        merged_config = {}
                        if config_name in self._default_configs:
                            default_config = self._default_configs[config_name]
                            if isinstance(default_config, dict) and isinstance(config_data, dict):
                                merged_config = copy.deepcopy(default_config)
                                merged_config.update(copy.deepcopy(config_data))
                            else:
                                merged_config = copy.deepcopy(config_data)
                        else:
                            merged_config = copy.deepcopy(config_data)

                        self._config_cache[config_name] = merged_config

                        # Trigger callbacks if requested
                        # Note: When send_update=False, we don't execute local callbacks here,
                        # but the server will still send an RPC notification that will trigger callbacks
                        if send_update:
                            # Execute callbacks locally for immediate notification
                            self._execute_callbacks_safely(config_name, "UPDATE", merged_config)

                        async_result.set(True)
                    else:
                        # No cleanup needed since we're not tracking pending updates
                        async_result.set_exception(Exception(f"Failed to store config: {response.text}"))
            except Exception as e:
                # No cleanup needed since we're not tracking pending updates
                async_result.set_exception(e)

        gevent.spawn(store_config)
        return async_result

    def delete(self, config_name: str, send_update: bool = True):
        """
        Delete a configuration from the config store.

        Args:
            config_name: Name of the configuration to delete
            send_update: Whether to call subscribed callbacks (default: True)
        """
        async_result = AsyncResult()

        # Prepare request
        request_url = (
            f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
        )

        # No need to track pending updates anymore - server handles this properly
        # Server will not send RPC notification when send_update=False for self-updates

        # Use gevent to make the HTTP request asynchronously
        def delete_config():
            try:
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    # Include requesting agent identity for access control and send_update flag
                    params = {"requesting_agent": self._agent.identity, "send_update": send_update}
                    response = client.delete(request_url, params=params)
                    if response.status_code == 200:
                        # Remove from comprehensive cache or update it to use only default values
                        if config_name in self._default_configs:
                            # Update to use default values only
                            self._config_cache[config_name] = self._default_configs[config_name]
                        elif config_name in self._config_cache:
                            # No default, so remove completely
                            del self._config_cache[config_name]

                        # Trigger callbacks if requested
                        if send_update:
                            # Execute callbacks locally for immediate notification
                            self._execute_callbacks_safely(config_name, "DELETE", None)

                        async_result.set(True)
                    else:
                        # No cleanup needed since we're not tracking pending updates
                        async_result.set_exception(Exception(f"Failed to delete config: {response.text}"))
            except Exception as e:
                # No cleanup needed since we're not tracking pending updates
                async_result.set_exception(e)

        gevent.spawn(delete_config)
        return async_result

    def list(self):
        """
        List all configurations for this agent.
        Returns the keys from our comprehensive cache which contains both server configs and defaults.
        """
        # Combine keys from both server configs and default configs
        config_names = set(self._config_cache.keys())

        # Convert to list and sort for consistent output
        config_list = sorted(config_names)

        # Create and set the result immediately
        async_result = AsyncResult()
        async_result.set(config_list)

        return async_result

    def subscribe(self, callback, actions=None, pattern=None, config_name=None):
        """
        Subscribe to configuration changes.
        Args:
            callback: Function to call when matching changes occur
            actions: List of action types to subscribe to ('NEW', 'UPDATE', 'DELETE')
            pattern: Pattern to match against config names
            config_name: Specific config name to subscribe to (takes precedence over pattern).
        """
        if actions is None:
            actions = ["NEW", "UPDATE", "DELETE"]

        # If a specific config_name is provided, use that directly
        target_config = config_name if config_name else pattern

        if target_config:
            # Register this callback for the specific config
            # TODO: pattern should allow a regular expression or wildcard matching, but is not at present
            if target_config not in self._config_callbacks:
                self._config_callbacks[target_config] = []

                # Note: VOLTTRON uses direct RPC calls (config.update) for config notifications,
                # not pubsub. External changes trigger RPC calls from the platform to agents.

            # Add the callback if not already registered
            if callback not in self._config_callbacks[target_config]:
                self._config_callbacks[target_config].append(ConfigCallback(callback, actions))
                _log.info(
                    f"Registered callback for config: {target_config} (total callbacks: {len(self._config_callbacks)})"
                )

            # If we already have this config, notify immediately
            if target_config in self._config_cache:
                value = self._config_cache[target_config]
                try:
                    callback(target_config, value)
                    _log.debug(f"Called callback with existing config: {target_config}")
                except Exception as e:
                    _log.error(f"Error calling callback for config {target_config}: {e}")

            # Return some identifier for this subscription
            return f"{target_config}:{len(self._config_callbacks[target_config])}"

        # For pattern-based subscriptions, use the old mechanism with the server
        subscription = {"callback": callback, "actions": actions, "pattern": pattern}

        if self._connected:
            return self._setup_subscription(subscription)

        self._pending_subscriptions.append(subscription)
        return len(self._pending_subscriptions)

    @RPC.export(name="config.update")
    def config_update(self, action: str, config_name: str, contents=None, trigger_callback: bool = True, _msg_id=None):
        """
        Handle config update notifications from the platform (VOLTTRON-style RPC).

        This method is called by the platform config store service when configurations
        are modified externally (via vctl config, REST API, etc.).

        Args:
            action: The action type ("NEW", "UPDATE", "DELETE")
            config_name: Name of the configuration
            contents: The new configuration contents (None for DELETE)
            trigger_callback: Whether to trigger registered callbacks
            _msg_id: Internal message ID for deduplication
        """
        _log.info(f"Agent {self._agent.identity} received config update: {config_name} ({action})")

        # No need to check for pending updates - server handles this properly now
        # If we receive an RPC notification, it means we should process it

        # Handle the case where contents might be a JSON string from file watcher
        if contents is not None and isinstance(contents, str):
            try:
                import json

                contents = json.loads(contents)
            except (json.JSONDecodeError, ValueError):
                # If it's not valid JSON, keep it as a string
                pass

        if not trigger_callback:
            # Just update cache without triggering callbacks
            if action == "DELETE":
                if config_name in self._config_cache:
                    # Check if we have a default to fall back to
                    if config_name in self._default_configs:
                        self._config_cache[config_name] = copy.deepcopy(self._default_configs[config_name])
                    else:
                        del self._config_cache[config_name]
            else:
                # Merge with defaults
                if config_name in self._default_configs:
                    default_config = self._default_configs[config_name]
                    if isinstance(default_config, dict) and isinstance(contents, dict):
                        merged_config = copy.deepcopy(default_config)
                        merged_config.update(copy.deepcopy(contents))
                        self._config_cache[config_name] = merged_config
                    else:
                        self._config_cache[config_name] = copy.deepcopy(contents)
                else:
                    self._config_cache[config_name] = copy.deepcopy(contents)
            return

        # Process callbacks
        if config_name in self._config_callbacks:
            if action == "DELETE":
                # Handle deletion - either remove or revert to default
                if config_name in self._config_cache:
                    if config_name in self._default_configs:
                        # Revert to default
                        self._config_cache[config_name] = copy.deepcopy(self._default_configs[config_name])
                        action = "UPDATE"  # Changed to default
                        contents = self._config_cache[config_name]
                    else:
                        # Remove entirely
                        del self._config_cache[config_name]
                        contents = None
            else:
                # Update/New - merge with defaults
                if config_name in self._default_configs:
                    default_config = self._default_configs[config_name]
                    if isinstance(default_config, dict) and isinstance(contents, dict):
                        merged_config = copy.deepcopy(default_config)
                        merged_config.update(copy.deepcopy(contents))
                        self._config_cache[config_name] = merged_config
                        contents = merged_config
                    else:
                        self._config_cache[config_name] = copy.deepcopy(contents)
                else:
                    self._config_cache[config_name] = copy.deepcopy(contents)

            # Execute callbacks
            self._execute_callbacks_safely(config_name, action, contents)

    def _handle_config_pubsub_message(self, message_data):
        """Handle config update notifications from pubsub (external changes like vctl config)."""
        try:
            # Extract data from VIP message structure
            vip_data = message_data.get("data", message_data)  # Handle both direct and VIP wrapped
            topic = vip_data.get("topic", "")
            payload = vip_data.get("message", {})

            # Extract config name from topic: "config/agent_id/config_name"
            topic_parts = topic.split("/")
            if len(topic_parts) >= 3:
                config_name = topic_parts[2]
                action = payload.get("action", "UPDATE")
                value = payload.get("value")

                _log.info(f"Agent {self._agent.identity} received pubsub config notification: {config_name} ({action})")

                # Update cache and trigger callbacks
                if action == "DELETE":
                    # Remove from cache or revert to default
                    if config_name in self._config_cache:
                        if config_name in self._default_configs:
                            # Restore to default
                            self._config_cache[config_name] = copy.deepcopy(self._default_configs[config_name])
                            action = "UPDATE"  # Reverted to default
                            value = self._config_cache[config_name]
                        else:
                            # Remove entirely
                            del self._config_cache[config_name]
                            value = None
                else:
                    # Update/New - refresh from server
                    if value is not None:
                        # Parse JSON string if needed
                        if isinstance(value, str):
                            try:
                                import json

                                value = json.loads(value)
                            except (json.JSONDecodeError, ValueError):
                                # If it's not valid JSON, keep as string
                                pass

                        # Merge with defaults like the set() method does
                        merged_config = {}
                        if config_name in self._default_configs:
                            default_config = self._default_configs[config_name]
                            if isinstance(default_config, dict) and isinstance(value, dict):
                                merged_config = copy.deepcopy(default_config)
                                merged_config.update(copy.deepcopy(value))
                            else:
                                merged_config = copy.deepcopy(value)
                        else:
                            merged_config = copy.deepcopy(value)

                        self._config_cache[config_name] = merged_config
                        # Update value to be the merged config for callback
                        value = merged_config
                    else:
                        # Fetch from server if value not provided
                        try:
                            value = self.get(config_name)
                            self._config_cache[config_name] = value
                        except KeyError:
                            _log.warning(f"Could not fetch updated config {config_name} from server")
                            return

                # Trigger callbacks
                if config_name in self._config_callbacks:
                    _log.info(
                        f"Triggering {len(self._config_callbacks[config_name])} pubsub callbacks for {config_name}"
                    )
                    for config_callback in self._config_callbacks[config_name]:
                        try:
                            config_callback(config_name, action, value)
                        except Exception as e:
                            _log.error(f"Error in config pubsub callback: {e}")

        except Exception as e:
            _log.error(f"Error handling config pubsub message: {e}")

    def unsubscribe(self, subscription_id):
        """Remove a configuration subscription."""
        return self._agent.vip.pubsub.unsubscribe(subscription_id)

    def handle_update(self, config_name):
        """Handle a configuration update notification from the server."""
        # No need to check for pending updates - server handles this properly now
        # If we receive a notification, it means we should process it

        # ALWAYS fetch and update the cache, regardless of callbacks
        try:
            request_url = (
                f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
            )
            with httpx.Client() as client:
                response = client.get(request_url)
                if response.status_code == 200:
                    data = response.json()
                    server_config = data["data"]

                    # Update the comprehensive cache
                    if config_name in self._default_configs:
                        default_config = self._default_configs[config_name]
                        if isinstance(default_config, dict) and isinstance(server_config, dict):
                            # Merge dictionaries - server config overrides defaults
                            merged_config = copy.deepcopy(default_config)
                            merged_config.update(copy.deepcopy(server_config))
                            self._config_cache[config_name] = merged_config
                        else:
                            # If either is not a dict, server config completely overrides
                            self._config_cache[config_name] = copy.deepcopy(server_config)
                    else:
                        # No default, use server config directly
                        self._config_cache[config_name] = copy.deepcopy(server_config)

                    # Log at INFO level when an agent updates its config cache
                    _log.info(f"Agent {self._agent.identity} updated config cache: {config_name}")

                    # Now check if there are callbacks to notify
                    if config_name in self._config_callbacks:
                        _log.info(
                            f"Triggering {len(self._config_callbacks[config_name])} callbacks for config {config_name}"
                        )
                        self._execute_callbacks_safely(config_name, "UPDATE", self._config_cache[config_name])
                    else:
                        _log.info(f"No callbacks registered for config {config_name}")
                        _log.info(f"Available callbacks: {list(self._config_callbacks.keys())}")
                elif response.status_code == 404:
                    # Config was deleted on server
                    _log.info(f"Config {config_name} not found on server (deleted)")
                    # Remove from cache or revert to default
                    if config_name in self._default_configs:
                        self._config_cache[config_name] = copy.deepcopy(self._default_configs[config_name])
                        action = "UPDATE"  # Reverted to default
                    else:
                        if config_name in self._config_cache:
                            del self._config_cache[config_name]
                        action = "DELETE"

                    # Notify callbacks about the deletion/reversion
                    value = self._config_cache.get(config_name, None)
                    self._execute_callbacks_safely(config_name, action, value)
                else:
                    _log.error(f"Failed to fetch updated config {config_name}: {response.status_code}")
        except Exception as e:
            _log.error(f"Error handling config update for {config_name}: {e}")

    def set_default(self, name, value):
        """
        Set a local default configuration value.
        Does not send to the server, just stores locally.
        """
        _log.debug(f"Setting default config: {name}")
        self._default_configs[name] = value

        # Update the comprehensive cache
        if self._has_server_config(name):
            # If we have a server config, preserve the existing merged config
            # The server config takes precedence for overlapping keys
            pass
        else:
            # No server config, use default directly
            self._config_cache[name] = copy.deepcopy(value)

        return value

    def _on_update_from_server(self, sender, **kwargs):
        """
        Called when the agent receives configuration updates from the server.
        This method handles synchronizing the local config cache with the server.
        """
        _log.info(f"Agent {self._agent.identity} received configuration update from server")
        configs = kwargs.get("configs", [])

        if not self._initial_connection_processed:
            # Mark that we've processed the initial connection
            self._initial_connection_processed = True
            _log.debug(f"Initial connection established for agent {self._agent.identity}")

            # Only trigger callbacks for configs that exist in the server store
            # Defaults alone should not trigger callbacks
            # This prevents duplicate greenlet creation in the Manager agent

        # Process configs from server if available
        for cfg in configs:
            # cfg is the config name string, not a dictionary
            config_name = cfg if isinstance(cfg, str) else cfg.get("name")

            # Fetch the config directly from the server instead of using get()
            try:
                request_url = (
                    f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
                )
                with httpx.Client() as client:
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        config_data = data["data"]

                        # Update our comprehensive cache
                        if config_name in self._default_configs:
                            default_config = self._default_configs[config_name]
                            if isinstance(default_config, dict) and isinstance(config_data, dict):
                                # Merge dictionaries - server config overrides defaults
                                merged_config = copy.deepcopy(default_config)
                                merged_config.update(copy.deepcopy(config_data))
                                self._config_cache[config_name] = merged_config
                            else:
                                # If either is not a dict, server config completely overrides
                                self._config_cache[config_name] = copy.deepcopy(config_data)
                                _log.info(f"Updated config {config_name} from server")
                        else:
                            # No default, use server config directly
                            self._config_cache[config_name] = copy.deepcopy(config_data)
                            _log.info(f"Using server config for {config_name}")

                        # Notify callbacks
                        if config_name in self._config_callbacks:
                            for callback in self._config_callbacks[config_name]:
                                try:
                                    _log.info(f"Calling callback for config: {config_name}")
                                    callback(config_name, "UPDATE", self._config_cache[config_name])
                                except Exception as e:
                                    _log.error(f"Error in config callback for {config_name}: {e}")
                    else:
                        _log.error(f"Failed to fetch config {config_name}: {response.status_code}")
            except Exception as e:
                _log.error(f"Error processing config update for {config_name}: {e}")

    def _on_connection_established(self, sender, **kwargs):
        """Called when connection to the server is established."""
        _log.info(f"Agent {self._agent.identity} connected to server")
        self._connected = True

        # Clear the cache to ensure fresh data
        self._config_cache = {}
        # Keep defaults separately
        for name, value in self._default_configs.items():
            if not self._has_server_config(name):
                self._config_cache[name] = copy.deepcopy(value)

        # Note: We only use direct WebSocket config_update messages now,
        # not pubsub notifications, to avoid race conditions

        # Fetch all configurations for this agent's identity from the server
        # Wait for completion to ensure configs are loaded before proceeding
        fetch_greenlet = self._fetch_all_server_configs()
        if fetch_greenlet:
            # Increase timeout to ensure all configs are loaded
            # This is a critical step for agent initialization
            fetch_greenlet.join(timeout=10)  # Wait up to 10 seconds for configs to load

        # Set up periodic debug output (will only run if explicitly enabled)
        self._start_periodic_debug()

        # Set up all pending subscriptions with the server
        for subscription in self._pending_subscriptions:
            self._setup_subscription(subscription)

            # Only fetch current config values if agent has onconfigure handler
            has_onconfigure_handler = (
                hasattr(self._agent, "onconfigure") and len(self._agent.core.onconfigure._handlers) > 0
            )
            if has_onconfigure_handler:
                self._fetch_config_for_subscription(subscription)

        self._pending_subscriptions = []

    def _fetch_all_server_configs(self):
        """
        Fetch all configurations for this agent from the server and update the cache.
        This is critical for ensuring the internal cache matches the persisted files.
        """

        def fetch_configs():
            try:
                _log.debug(f"Fetching all server configs for agent {self._agent.identity}")

                # Request the list of available configs for this agent
                request_url = (
                    f"http://{self._agent._host}:{self._agent._port}/config-store/list?agent_id={self._agent.identity}"
                )
                with httpx.Client() as client:
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        if self._agent.identity in data["data"]:
                            config_entries = data["data"][self._agent.identity]
                            _log.info(f"Found {len(config_entries)} configs for agent {self._agent.identity}")

                            # Fetch each individual configuration synchronously
                            fetch_greenlets = []
                            for config_entry in config_entries:
                                # Extract just the name from the config entry
                                if isinstance(config_entry, dict) and "name" in config_entry:
                                    config_name = config_entry["name"]
                                    greenlet = self._fetch_single_config(config_name)
                                    if greenlet:
                                        fetch_greenlets.append(greenlet)
                                else:
                                    # Fallback if for some reason we got a string instead of a dict
                                    greenlet = self._fetch_single_config(config_entry)
                                    if greenlet:
                                        fetch_greenlets.append(greenlet)

                            # Wait for all configs to be fetched
                            gevent.joinall(fetch_greenlets, timeout=10)
                        else:
                            _log.info(f"No configs found for agent {self._agent.identity}")
                    else:
                        _log.error(f"Failed to list configs: {response.status_code} - {response.text}")
            except Exception as e:
                _log.error(f"Error fetching all configs: {e}")

        # Run and return the greenlet so caller can wait for it
        return gevent.spawn(fetch_configs)

    def _fetch_config_for_subscription(self, subscription):
        """Fetch current config values from server for a subscription pattern."""
        pattern = subscription["pattern"]

        # If pattern is a specific config name, fetch it directly
        if pattern and "*" not in pattern:
            self._fetch_single_config(pattern)
        else:
            # For wildcard patterns, we'd need to list all configs and filter
            # This is more complex, so for now we'll handle specific config names
            _log.warning(f"Wildcard patterns not yet implemented for initial fetch: {pattern}")

    def _fetch_single_config(self, config_name):
        """Fetch a single config from the server and cache it."""

        def fetch_config():
            try:
                request_url = (
                    f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
                )
                with httpx.Client() as client:
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        server_config = data["data"]

                        # Update the comprehensive cache
                        if config_name in self._default_configs:
                            default_config = self._default_configs[config_name]
                            if isinstance(default_config, dict) and isinstance(server_config, dict):
                                # Merge dictionaries - server config overrides defaults
                                merged_config = copy.deepcopy(default_config)
                                merged_config.update(copy.deepcopy(server_config))
                                self._config_cache[config_name] = merged_config
                                _log.info(f"Updated merged config {config_name} from server")
                            else:
                                # If either is not a dict, server config completely overrides
                                self._config_cache[config_name] = copy.deepcopy(server_config)
                            _log.info(f"Updated config {config_name} from server")
                            _log.info(
                                f"Fetched config {config_name} from server: {pformat(self._config_cache[config_name])}"
                            )
                        else:
                            # No default, use server config directly
                            _log.info(f"Using server config for {config_name}")
                            _log.info(f"Fetched config {config_name} from server: {pformat(server_config)}")
                            self._config_cache[config_name] = copy.deepcopy(server_config)
                    elif response.status_code != 404:  # 404 is expected for non-existent configs
                        _log.warning(f"Failed to fetch config {config_name}: HTTP {response.status_code}")
            except Exception as e:
                _log.error(f"Failed to fetch config {config_name}: {e}")

        # Run the fetch and return the greenlet so caller can wait for it
        return gevent.spawn(fetch_config)

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

                # Handle different actions
                if action == "DELETE":
                    # For DELETE, remove from config cache if not a default
                    if config_name in self._config_cache and config_name not in self._default_configs:
                        del self._config_cache[config_name]
                    elif config_name in self._config_cache and config_name in self._default_configs:
                        # Reset to default value
                        self._config_cache[config_name] = copy.deepcopy(self._default_configs[config_name])
                else:
                    # For NEW or UPDATE, update the cache with server value
                    if config_name in self._default_configs and config_value is not None:
                        default_config = self._default_configs[config_name]
                        if isinstance(default_config, dict) and isinstance(config_value, dict):
                            # Merge dictionaries - server config overrides defaults
                            merged_config = copy.deepcopy(default_config)
                            merged_config.update(copy.deepcopy(config_value))
                            self._config_cache[config_name] = merged_config
                        else:
                            # For non-dict values, server completely overrides defaults
                            self._config_cache[config_name] = copy.deepcopy(config_value)
                    elif config_value is not None:
                        # No default, use server value directly
                        self._config_cache[config_name] = copy.deepcopy(config_value)

                # Use the value from our cache for the callback
                merged_config = self._config_cache.get(config_name, config_value)

                # Call the callback with the merged config
                try:
                    callback(config_name, action, merged_config)
                except Exception as e:
                    _log.error(f"Error in config subscription callback: {e}")

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

    def _start_periodic_debug(self):
        """Start periodic debug output of the entire config store contents."""
        # Only run periodic debug if log level is DEBUG
        if not _log.isEnabledFor(logging.DEBUG):
            return

        if self._debug_task is not None:
            # Cancel any existing task
            self._debug_task.kill(block=False)

        # Start a new task that logs the config store every minute
        self._debug_task = gevent.spawn(self._periodic_debug_log)
        _log.info(f"Started periodic config store debug logging for agent {self._agent.identity}")

    def _periodic_debug_log(self):
        """Periodically log the entire contents of the config store."""
        try:
            while self._connected:
                # Log the entire config store contents
                self._log_config_store_contents()
                # Wait for 1 minute before logging again
                gevent.sleep(60)
        except gevent.GreenletExit:
            _log.debug(f"Stopping periodic config store debug logging for agent {self._agent.identity}")
        except Exception as e:
            _log.error(f"Error in periodic config store debug logging: {e}")

    def _log_config_store_contents(self):
        """Log the entire contents of the config store."""
        if not self._connected or not _log.isEnabledFor(logging.DEBUG):
            return

        # Define a helper function to safely convert objects to JSON-serializable format
        def safe_json_dump(obj):
            class ConfigEncoder(json.JSONEncoder):
                def default(self, o):
                    # Handle specific types that might be in configurations

                    # Handle PosixPath objects from pathlib
                    if hasattr(o, "__fspath__") or hasattr(o, "resolve"):
                        return str(o)

                    # Handle datetime, date, and time objects
                    elif hasattr(o, "isoformat"):
                        return o.isoformat()

                    # Handle bytes and bytearrays
                    elif isinstance(o, bytes | bytearray):
                        try:
                            return o.decode("utf-8")
                        except UnicodeDecodeError:
                            return str(o)

                    # Handle sets
                    elif isinstance(o, set):
                        return list(o)

                    # Handle complex numbers
                    elif isinstance(o, complex):
                        return {"real": o.real, "imag": o.imag}

                    # Handle numpy arrays if present
                    elif str(type(o)).startswith("<class 'numpy."):
                        try:
                            return o.tolist()
                        except (AttributeError, ValueError, TypeError):
                            return str(o)

                    # Handle custom objects with __dict__
                    elif hasattr(o, "__dict__"):
                        return {
                            "_type": o.__class__.__name__,
                            "attributes": {k: v for k, v in o.__dict__.items() if not k.startswith("_")},
                        }

                    # Handle other iterables
                    elif hasattr(o, "__iter__") and not isinstance(o, str | dict | list):
                        return list(o)

                    # Default: convert to string
                    return str(o)

            try:
                # Use compact JSON output (no indents, no newlines)
                return json.dumps(obj, cls=ConfigEncoder, separators=(",", ":"))
            except Exception as e:
                return f"<Error serializing: {str(e)}>"

        try:
            # Simplified output in a single log message to reduce log volume
            _log.debug(
                f"CONFIG STORE ({self._agent.identity}): "
                f"{len(self._default_configs)} defaults, "
                f"{len(self._config_cache)} cached, "
                f"{len([n for n in self._config_cache if self._has_server_config(n)])} from server"
            )

            # Only log detailed contents if explicitly requested via environment variable
            _log.debug(f"CONFIG DEFAULTS: {', '.join(self._default_configs.keys())}")
            _log.debug(f"CACHED CONFIGS: {', '.join(self._config_cache.keys())}")
            _log.debug(f"SERVER CONFIGS: {', '.join([n for n in self._config_cache if self._has_server_config(n)])}")

            # Log full config contents if even more detail is requested
            if os.environ.get("AEMS_CONFIG_FULL_DETAIL", "").lower() in ("1", "true", "yes"):
                for name, value in self._config_cache.items():
                    _log.debug(f"CONFIG {name}: {safe_json_dump(value)}")
        except Exception as e:
            _log.error(f"Error logging config store contents: {e}")
            if _log.isEnabledFor(logging.DEBUG):
                import traceback

                _log.debug(traceback.format_exc())


class CronTimer:
    """
    A timer that executes periodically based on a cron schedule.
    This is a simplified version of VOLTTRON's cron schedule parser.
    """

    def __init__(self, cron_pattern):
        """Initialize a cron timer with a cron pattern."""
        self.cron_pattern = cron_pattern

        # Parse the cron pattern
        self.minutes, self.hours, self.days_of_month, self.months, self.days_of_week = self._parse_pattern(cron_pattern)

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
                days_of_week.add(day_num if day_num < 7 else 0)  # Convert 7 to 0 (both represent Sunday)
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

        Returns
        -------
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

        Returns
        -------
            The next scheduled time as a datetime object
        """
        if now is None:
            now = datetime.now()

        # Start from the next minute
        next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Check up to 1000 minutes ahead to avoid infinite loops
        for _ in range(1000000):
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
        _log.debug(f"Peer added: {peer}")

    def remove_peer(self, peer: str):
        """Remove a peer from the list."""
        self._connected_peers.discard(peer)
        _log.debug(f"Peer removed: {peer}")

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
                try:
                    result = ast.literal_eval(d)
                    if not isinstance(result, int | float):
                        invalid = True
                except (ValueError, SyntaxError):
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

        Returns
        -------
            The name of the scheduled event
        """
        if isinstance(interval_or_cron, numbers.Number):
            if interval_or_cron <= 0:
                raise ValueError("Interval must be a positive number")
        elif not isinstance(interval_or_cron, str):
            raise ValueError("Schedule must be either a positive number (interval) or a cron expression")

        name = name or function.__name__

        # Create a new event
        event = ScheduledEvent(function, interval_or_cron, args, kwargs, name)

        # Add to the event queue and map
        self._events[name] = event
        heapq.heappush(self._event_queue, event)

        return self

    def cancel(self):
        """Cancel a scheduled event."""
        if self._scheduler_greenlet is not None:
            self._scheduler_greenlet.kill()
            self._scheduler_greenlet = None
            return True

        return False

        # if name in self._events:
        #     event = self._events.pop(name)
        #     event.running = False
        #     # Note: The event may still be in the queue, but we'll skip it
        #     # when it comes up in the scheduler loop
        #     return True
        # return False

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
                    _log.error(f"Error spawning periodic task {event.name}: {e}")

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
        for handler in self._handlers[:]:  # Copy to avoid issues if handlers are added/removed during iteration
            try:
                gevent.spawn(handler, sender, **kwargs)
            except Exception as e:
                _log.error(f"Error in {self.name} handler: {e}")


class Agent:
    """A gevent-based agent that connects to the VOLTTRON MessageBus."""

    def __init__(
        self,
        identity: str,
        host: str = "127.0.0.1",
        port: int = 8000,
        config_path: str = None,
        auto_reconnect: bool = True,
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 0,  # 0 = infinite
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

        # Auto-reconnection settings
        self.auto_reconnect = auto_reconnect
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_attempts = 0
        self._reconnect_greenlet = None
        self._manual_disconnect = False

        # Create subsystems
        self.core = Core(self)
        self.config = Config(self)  # Initialize config before VIP

        # Initialize VIP with all subsystems
        self.vip = VIP(self)

        # Register any methods decorated with @Core.receiver or @RPC.export
        self.core._register_decorated_methods(self)
        self.vip.rpc._register_decorated_methods(self)

        # Also register config store RPC methods
        self.vip.rpc._register_decorated_methods(self.config)

    def connect(self):
        """Connect to the message bus."""
        self._manual_disconnect = False  # Reset the flag for fresh connections
        self._internal_connect()

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
            # If a config_path was provided, load it first
            if self.config_path and self.connected:
                self._load_config_from_path()
                # Give the server time to process the config
                gevent.sleep(0.5)

            # List available configurations for this agent
            configs = self.config.list().get(timeout=5)

            # Fire the onconfigure event with the loaded configs
            self.core.fire_event("onconfigure", sender=self, configs=configs)

        except Exception as e:
            _log.error(f"Error loading configurations: {e}")

    def _load_config_from_path(self):
        """
        Load configuration from the specified config_path and push it to the server's config store.

        This follows the VOLTTRON pattern where local file configs are pushed to the
        platform's config store, and then agents retrieve them from there.
        """
        import json
        import os

        if not self.config_path or not os.path.exists(self.config_path):
            return

        try:
            self._logger.debug(f"Loading configuration from file: {self.config_path}")

            # Load the configuration based on file extension
            with open(self.config_path) as f:
                if self.config_path.endswith(".json"):
                    config_data = json.load(f)
                elif self.config_path.endswith((".yml", ".yaml")):
                    import yaml

                    config_data = yaml.safe_load(f)
                else:
                    _log.error(f"Unsupported config file format: {self.config_path}")
                    return

            _log.info(f"Loaded configuration from {self.config_path}")

            # Push the config to the server's config store
            self.config.set("config", config_data).get(timeout=5)
            _log.info(f"Pushed configuration from {self.config_path} to the server's config store")

            # We don't need to store the config locally here, as we'll retrieve it
            # from the server during the onconfigure phase
        except Exception as e:
            _log.error(f"Error loading configuration from {self.config_path}: {e}")
            # TODO: Implement proper health status tracking
            # self.health.set_status(Status.WARNING, f"Config load error: {e}")

    def start(self):
        """Start the agent (alias for connect for VOLTTRON compatibility)."""
        return self.connect()

    def disconnect(self):
        """Disconnect from the message bus."""
        self._manual_disconnect = True  # Mark as intentional disconnect
        self._stop_reconnection()  # Stop any ongoing reconnection attempts

        if self.websocket and self.connected:
            # Fire the onstop event with self as sender
            self.core.fire_event("onstop", sender=self)

            self.websocket.close()
            # Wait for the close to complete
            if self._listener_greenlet:
                self._listener_greenlet.join(timeout=1)

            self.connected = False
            _log.info(f"Agent {self.identity} disconnected")

            # Fire the ondisconnected event with self as sender
            self.core.fire_event("ondisconnected", sender=self)

    def __on_ws_open__(self, ws):
        """Callback when WebSocket connection is opened."""
        self.connected = True
        _log.debug(f"Agent {self.identity} websocket connection opened")

    def __on_ws_message__(self, ws, message):
        """Internal callback when a WebSocket message is received."""
        try:
            small_msg = get_smaller_print(message, '"type":"rpc","method":"set_temperature_setpoints"')
            if '"type":"rpc","method":"set_temperature_setpoints"' in message:
                _log.debug(f"Agent {self.identity} received set_temperature_setpoints RPC call")

            data = json.loads(message)
            if "kwargs" in data and "authentication" in data["kwargs"]:
                del data["kwargs"]["authentication"]

            self.received_messages.append(data)

            _log.debug(f"Agent {self.identity} received data {small_msg}. ")

            # Handle different message types
            msg_type = data.get("type")
            _log.debug(f"Agent {self.identity} processing message type: {msg_type}")

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
                if config_name:
                    # No need to check for pending updates - server handles this properly now
                    _log.info(f"Processing config_delete for {config_name}")
                    # Remove from cache or revert to default
                    if config_name in self.config._config_cache:
                        if config_name in self.config._default_configs:
                            # Restore to default
                            self.config._config_cache[config_name] = copy.deepcopy(
                                self.config._default_configs[config_name]
                            )
                            action = "UPDATE"  # Reverted to default
                            value = self.config._config_cache[config_name]
                            _log.info(f"Config {config_name} reverted to default: {value}")
                        else:
                            # No default, remove completely
                            del self.config._config_cache[config_name]
                            action = "DELETE"
                            value = None
                            _log.info(f"Config {config_name} deleted completely")

                        # Notify callbacks about the deletion/reversion
                        if config_name in self.config._config_callbacks:
                            _log.info(
                                f"Triggering {len(self.config._config_callbacks[config_name])} delete callbacks for {config_name}"
                            )
                            for config_callback in self.config._config_callbacks[config_name]:
                                try:
                                    # Use the ConfigCallback's __call__ method which handles action filtering
                                    _log.info(f"Calling delete callback for {config_name} with {action}")
                                    config_callback(config_name, action, value)
                                except Exception as e:
                                    _log.error(f"Error in config delete callback: {e}")
                        else:
                            _log.info(f"No callbacks registered for deleted config {config_name}")
            elif msg_type in ("rpc_request", "rpc"):
                # Handle RPC request
                _log.debug(f"Agent {self.identity} received RPC request: {data}")
                if "authentication" in data:
                    _log.debug(f"data: {data}")
                sender = data.get("sender")
                method_name = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                msg_id = data.get("msg_id")

                # Process the RPC request - returns an AsyncResult
                async_result = self.vip.rpc.handle_request(sender, method_name, args, kwargs, msg_id)

                # Wait for the result and send the response
                def send_response():
                    try:
                        _log.debug(f"Agent {self.identity} waiting for RPC result for msg_id {msg_id}")
                        # Wait for the result (with timeout)
                        result = async_result.get(timeout=10)
                        # Send successful response
                        _log.debug(f"Agent {self.identity} sending RPC response for msg_id {msg_id}: {result}")
                        response_msg = {"type": "rpc_response", "msg_id": msg_id, "result": result}
                        self.websocket.send(json.dumps(response_msg))
                        _log.debug(f"Agent {self.identity} successfully sent RPC response for msg_id {msg_id}")
                    except Exception as e:
                        # Send error response
                        error = str(e)
                        _log.error(f"Agent {self.identity} RPC error for msg_id {msg_id}: {error}")
                        try:
                            error_msg = {"type": "rpc_error", "msg_id": msg_id, "error": error}
                            self.websocket.send(json.dumps(error_msg))
                            _log.debug(f"Agent {self.identity} sent RPC error response for msg_id {msg_id}")
                        except Exception as ws_error:
                            _log.error(f"Agent {self.identity} failed to send RPC error via websocket: {ws_error}")

                # Spawn a greenlet to process the response asynchronously
                try:
                    gevent.spawn(send_response)
                    _log.debug(f"Spawned RPC response handler for msg_id {msg_id}")
                except Exception as spawn_error:
                    _log.error(f"Failed to spawn RPC response handler for msg_id {msg_id}: {spawn_error}")
                    # Send error response immediately
                    try:
                        self.websocket.send(
                            json.dumps({"type": "rpc_error", "msg_id": msg_id, "error": "Failed to process response"})
                        )
                    except Exception as ws_error:
                        _log.error(f"Failed to send error response via websocket: {ws_error}")

            elif msg_type == "rpc_response":
                # Handle RPC response
                msg_id = data.get("msg_id")
                result = data.get("result")
                _log.debug(f"Agent {self.identity} received RPC response for msg_id {msg_id}: {result}")
                if msg_id in self.rpc_responses:
                    # Get the AsyncResult for this message ID and set its result
                    async_result = self.rpc_responses.pop(msg_id)
                    async_result.set(result)
                else:
                    _log.debug(f"No pending RPC request found for msg_id {msg_id}")

            elif msg_type == "rpc_error":
                # Handle RPC error
                msg_id = data.get("msg_id")
                error = data.get("error", "Unknown RPC error")
                _log.debug(f"Agent {self.identity} received RPC error for msg_id {msg_id}: {error}")
                if msg_id in self.rpc_responses:
                    # Get the AsyncResult for this message ID and set the exception
                    async_result = self.rpc_responses.pop(msg_id)
                    # Create proper volttron-core compatible exception
                    if isinstance(error, dict) and "code" in error:
                        # JSON-RPC style error with code, message, data
                        exception = exception_from_json(
                            error.get("code", -32603),
                            error.get("message", "Internal Error"),
                            error.get("data"),
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
                    _log.debug(f"No pending RPC request found for msg_id {msg_id}")

            elif msg_type == "ping":
                # Handle ping messages from the server
                ping_id = data.get("ping_id")
                timestamp = data.get("timestamp")
                _log.debug(f"Agent {self.identity} received ping {ping_id}")

                # Send pong response
                try:
                    pong_response = {
                        "type": "pong",
                        "ping_id": ping_id,
                        "original_timestamp": timestamp,
                        "response_timestamp": datetime.now().isoformat(),
                    }
                    self.websocket.send(json.dumps(pong_response))
                    _log.debug(f"Agent {self.identity} sent pong response for ping {ping_id}")
                except Exception as e:
                    _log.error(f"Agent {self.identity} failed to send pong response: {e}")

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
                elif subsystem == "pubsub":
                    # This is a pubsub message via VIP
                    _log.debug(f"Agent {self.identity} received VIP pubsub message: {message}")
                    # Extract the pubsub data from the VIP message
                    pubsub_data = message.get("data", {})
                    self.vip.pubsub.handle_message(pubsub_data)
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
                                error_data.get("data"),
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
            _log.error(f"Error processing message in agent {self.identity}: {e}")
            import traceback

            traceback.print_exc()

    def _handle_vip_rpc_request(self, message):
        """Handle an incoming RPC request via VIP."""
        peer = message.get("user", "")  # Sender identity
        data = message.get("data", {})
        msg_id = data.get("msg_id", message.get("msg_id", ""))

        _log.debug(f"Agent {self.identity} received VIP RPC request: {message}")

        # Handle new format: data contains {"method": "...", "args": [...], "kwargs": {...}}
        if "method" in data:
            method_name = data.get("method")
            method_args = data.get("args", [])
            method_kwargs = data.get("kwargs", {})

            # Process the RPC request - returns an AsyncResult
            async_result = self.vip.rpc.handle_request(peer, method_name, method_args, method_kwargs, msg_id)
        else:
            # Handle old format: {"args": [method_name, arg1, arg2, ...]}
            args = message.get("args", [])
            if len(args) >= 2:
                method_name = args[0]
                method_args = args[1:]

                # Process the RPC request - returns an AsyncResult
                async_result = self.vip.rpc.handle_request(peer, method_name, method_args, {}, msg_id)
            else:
                _log.error(f"Invalid RPC request format: {message}")
                return

        # Wait for the result and send the response via VIP
        def send_vip_response():
            try:
                # Wait for the result (with timeout)
                result = async_result.get(timeout=10)
                # Send successful response via VIP
                self.vip.send_message(peer=peer, subsystem="rpc_response", args=[result, msg_id])
            except Exception as e:
                # Send error response via VIP
                self.vip.send_message(peer=peer, subsystem="rpc_error", args=[str(e), msg_id])

        # Spawn a greenlet to process the response asynchronously
        gevent.spawn(send_vip_response)

    def __on_ws_error__(self, ws, error):
        """Callback when an error occurs."""
        _log.error(f"Agent {self.identity} error: {error}")

        # Some errors might not trigger close, so we need to handle reconnection here too
        if not self.connected and not self._manual_disconnect and self.auto_reconnect:
            self._start_reconnection()

    def __on_ws_close__(self, ws, close_status_code, close_msg):
        """Callback when the connection is closed."""
        self.connected = False
        _log.info(f"Agent {self.identity} connection closed: {close_status_code} {close_msg}")

        # Fire the ondisconnected event
        self.core.fire_event("ondisconnected", sender=self)

        # Start reconnection if not manually disconnected
        if not self._manual_disconnect and self.auto_reconnect:
            self._start_reconnection()

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
            _log.info(f"Starting agent: {self.identity}")
            self.connect()

            # Keep the agent running until stopped
            _log.info(f"Agent {self.identity} running. Press Ctrl+C to stop.")
            while not self._stop_event.is_set():
                gevent.sleep(1.0)  # Sleep to avoid busy waiting

            return 0  # Success

        except KeyboardInterrupt:
            _log.info(f"\nKeyboard interrupt received, stopping agent: {self.identity}")
        except Exception as e:
            _log.error(f"Error running agent {self.identity}: {e}")
            import traceback

            traceback.print_exc()
            return 1  # Error
        finally:
            # Ensure proper shutdown
            try:
                self.core.stop().get(timeout=5)
                _log.info(f"Agent {self.identity} stopped cleanly")
            except Exception as e:
                _log.error(f"Error stopping agent {self.identity}: {e}")
                return 1  # Error

    def _start_reconnection(self):
        """Start the reconnection process."""
        if self._reconnect_greenlet and not self._reconnect_greenlet.dead:
            # Reconnection already in progress
            return

        _log.info(f"Agent {self.identity} starting auto-reconnection")
        self._reconnect_greenlet = gevent.spawn(self._reconnection_loop)

    def _stop_reconnection(self):
        """Stop the reconnection process."""
        if self._reconnect_greenlet and not self._reconnect_greenlet.dead:
            _log.debug(f"Agent {self.identity} stopping reconnection")
            self._reconnect_greenlet.kill()
            self._reconnect_greenlet = None
        self._reconnect_attempts = 0

    def _reconnection_loop(self):
        """Main reconnection loop."""
        while not self._manual_disconnect and self.auto_reconnect:
            if self.connected:
                # Already connected, exit loop
                break

            # Check if we've exceeded max attempts
            if self.max_reconnect_attempts > 0 and self._reconnect_attempts >= self.max_reconnect_attempts:
                _log.error(f"Agent {self.identity} max reconnection attempts ({self.max_reconnect_attempts}) reached")
                break

            self._reconnect_attempts += 1
            _log.info(f"Agent {self.identity} reconnection attempt {self._reconnect_attempts}")

            try:
                # Reset manual disconnect flag for reconnection
                self._manual_disconnect = False

                # Clean up old connection
                if self.websocket:
                    with contextlib.suppress(Exception):
                        self.websocket.close()

                if self._listener_greenlet:
                    with contextlib.suppress(Exception):
                        self._listener_greenlet.join(timeout=1)

                # Attempt to reconnect
                self._internal_connect()

                # If we get here, connection was successful
                _log.info(f"Agent {self.identity} successfully reconnected after {self._reconnect_attempts} attempts")
                self._reconnect_attempts = 0

                # Fire reconnected event
                self.core.fire_event("onconnected", sender=self)

                # Reload configurations after reconnection
                self._load_configs()

                break

            except Exception as e:
                _log.warning(f"Agent {self.identity} reconnection attempt {self._reconnect_attempts} failed: {e}")

                # Wait before next attempt
                if not self._manual_disconnect:
                    gevent.sleep(self.reconnect_interval)
                else:
                    break

    def _internal_connect(self):
        """Internal connection method used by both connect() and reconnection."""
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

        _log.info(f"Agent {self.identity} connected")

    def stop(self):
        """Signal the agent to stop."""
        self._stop_reconnection()  # Stop reconnection first
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

    Returns
    -------
        Exit code (0 for success, non-zero for errors)
    """
    import argparse
    import json
    import os

    import yaml

    identity = identity or os.environ.get("AGENT_VIP_IDENTITY", None)

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Agent configuration file", default=config_path)
    parser.add_argument("--identity", help="Agent identity", default=identity)
    parser.add_argument("--host", help="Message bus host", default="127.0.0.1")
    parser.add_argument("--port", help="Message bus port", type=int, default=8000)
    parser.add_argument("--volttron-home", help="VOLTTRON_HOME directory", default=os.environ.get("VOLTTRON_HOME"))

    args = parser.parse_args()

    # Set VOLTTRON_HOME environment variable if provided
    if args.volttron_home:
        os.environ["VOLTTRON_HOME"] = args.volttron_home
        _log.info(f"Using VOLTTRON_HOME: {args.volttron_home}")

    # Use command line config path if provided, otherwise use the argument
    config_path = args.config or config_path
    agent_config = {}

    # Load the configuration file if it exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as f:
                try:
                    agent_config = yaml.safe_load(f)
                except ImportError:
                    try:
                        agent_config = json.load(f)
                    except json.JSONDecodeError:
                        _log.error(f"Error decoding JSON from {config_path}. Ensure it is a valid JSON file.")

                    _log.error(f"Unsupported config file format: {config_path}")
        except Exception as e:
            _log.error(f"Error loading configuration from {config_path}: {e}")
            return 1

    # Create the agent
    agent_identity = args.identity or identity or agent_class.__name__.lower()

    agent = agent_class(identity=agent_identity, host=args.host, port=args.port, config_path=config_path, **kwargs)

    # Set initial configuration if loaded from file
    if agent_config:
        # Store the config in the agent's config store
        if hasattr(agent, "config") and hasattr(agent.config, "set"):
            try:
                agent.config.set("config", agent_config).get(timeout=5)
                _log.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                _log.error(f"Error storing initial configuration: {e}")

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
