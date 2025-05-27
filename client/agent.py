# agent.py

from datetime import datetime, timedelta
import gevent
from gevent import monkey
from gevent.event import AsyncResult
# Patch standard library to work with gevent
monkey.patch_all()

import json
import uuid
import websocket
from typing import Dict, Any, Optional, Callable, List
import ssl

import time
import heapq
from typing import Optional, Callable, Any
import numbers

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
                print(f"Agent {self._agent.identity} exported RPC method: {method_name} (from decorator)")
    
    def call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call to another agent, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        msg_id = str(uuid.uuid4())
        async_result = AsyncResult()
        self._agent.rpc_responses[msg_id] = async_result
        
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
                async_result.set_exception(TimeoutError(f"RPC call timed out"))
    
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
                    print(f"DEBUG: Agent {self._agent.identity} method {method_name} result: {result}")
                    async_result.set(result)
                except Exception as e:
                    error = str(e)
                    print(f"DEBUG: Agent {self._agent.identity} method {method_name} error: {error}")
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
    
    def publish(self, topic: str, message: Any, headers: Optional[Dict] = None, bus: str = ""):
        """Publish a message to a topic, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        if headers is None:
            headers = {}
        
        # Create an AsyncResult to track the publish operation
        async_result = AsyncResult()
        
        try:
            self._agent.websocket.send(json.dumps({
                "type": "publish",
                "bus": bus,
                "topic": topic,
                "headers": headers,
                "message": message
            }))
            
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
                import traceback
                traceback.print_exc()
                # Return None when there's an error
                return None
        
        return adapter
    
    def subscribe(self, prefix: str, callback: Optional[Callable] = None):
        """Subscribe to a topic prefix, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        subscription_id = str(uuid.uuid4())
        
        # Store the original callback
        actual_callback = callback or (lambda msg: print(f"Subscription callback for {prefix}: {msg}"))
        
        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(actual_callback)
        
        self._subscriptions[prefix] = adapted_callback
        
        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()
        
        try:
            self._agent.websocket.send(json.dumps({
                "type": "subscribe",
                "prefix": prefix,
                "id": subscription_id
            }))
            
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
        actual_callback = callback or (lambda msg: print(f"Subscription callback for {pattern}: {msg}"))
        
        # Wrap the callback with our adapter
        adapted_callback = self._callback_adapter(actual_callback)
        
        self._subscriptions[pattern] = adapted_callback
        
        # Create an AsyncResult to track the subscription operation
        async_result = AsyncResult()
        
        try:
            self._agent.websocket.send(json.dumps({
                "type": "subscribe",
                "pattern": pattern,
                "id": subscription_id
            }))
            
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
                    import traceback
                    traceback.print_exc()


class Config:
    """Config subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._config = {}
    
    def set(self, key: str, value: Any):
        """Set a configuration value."""
        self._config[key] = value
        # Return AsyncResult for API consistency
        result = AsyncResult()
        result.set(True)
        return result
    
    def get(self, key: str, default=None):
        """Get a configuration value."""
        value = self._config.get(key, default)
        # Return AsyncResult for API consistency
        result = AsyncResult()
        result.set(value)
        return result
    
    def delete(self, key: str):
        """Delete a configuration value."""
        if key in self._config:
            del self._config[key]
            success = True
        else:
            success = False
        # Return AsyncResult for API consistency
        result = AsyncResult()
        result.set(success)
        return result
    
    def list(self):
        """List all configuration keys."""
        keys = list(self._config.keys())
        # Return AsyncResult for API consistency
        result = AsyncResult()
        result.set(keys)
        return result


class VIP:
    """VIP subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self.rpc = RPC(agent)
        self.pubsub = PubSub(agent)
    
    def send_message(self, peer: str, subsystem: str, args: list = None):
        """Send a VIP message, returning an AsyncResult."""
        if not self._agent.connected:
            raise ConnectionError("Agent not connected")
        
        if args is None:
            args = []
        
        msg_id = str(uuid.uuid4())
        async_result = AsyncResult()
        
        try:
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
            async_result.set(msg_id)  # Return the message ID
        except Exception as e:
            print(f"Error sending VIP message: {e}")
            async_result.set_exception(e)
        
        return async_result


class Core:
    """Core functionality for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._handlers = {
            'onstart': [],
            'onstop': [],
            'onconnected': [],
            'ondisconnected': [],
            'onconfigure': []
        }
        self._scheduler = Scheduler(agent)
        
        # Register any methods decorated with @Core.receiver
        self._register_decorated_methods(agent)

        # Register any methods decorated with @Core.periodic
        self._register_periodic_methods(agent)

    def schedule(self, function, interval_or_cron, *args, **kwargs):
        """
        Schedule a periodic function.
        
        Args:
            function: The function to call
            interval_or_cron: Either a number of seconds (interval) or a cron expression
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
            
        Returns:
            The name of the scheduled event
        """
        return self._scheduler.schedule(function, interval_or_cron, args, kwargs)
    
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
    
    
    
    def _register_decorated_methods(self, agent):
        """Find and register methods decorated with @Core.receiver."""
        for attr_name in dir(agent):
            attr = getattr(agent, attr_name)
            if callable(attr) and hasattr(attr, "event_name"):
                event_name = getattr(attr, "event_name")
                if event_name in self._handlers:
                    self._handlers[event_name].append(attr)
    @staticmethod
    def periodic(interval_or_cron):
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
            # Stop the scheduler
            self._scheduler.stop()

            self._agent.disconnect()
            async_result.set(True)
        except Exception as e:
            async_result.set_exception(e)
        return async_result
    
    def start_periodic_tasks(self):
        """Start running periodic tasks."""
        self._scheduler.start()
    
    def identity(self):
        """Get the agent's identity."""
        return self._agent.identity
    
    def onstart(self, callback):
        """Register a callback to be executed when the agent starts."""
        self._handlers['onstart'].append(callback)
        return callback  # Return the callback for use as a decorator
    
    def onstop(self, callback):
        """Register a callback to be executed when the agent stops."""
        self._handlers['onstop'].append(callback)
        return callback  # Return the callback for use as a decorator
    
    def fire_event(self, event_name, sender=None, **kwargs):
        """Fire an event by calling all registered handlers."""
        if event_name in self._handlers:
            for handler in self._handlers[event_name]:
                try:
                    # Pass sender and any kwargs to the handler
                    handler(sender=sender, **kwargs)
                except Exception as e:
                    print(f"Error in {event_name} handler: {e}")

class ConfigStore:
    """ConfigStore subsystem for the Agent."""
    
    def __init__(self, agent):
        self._agent = agent
        self._config_callbacks = {}
        # Initialize httpx client
        import httpx
        self._client = httpx.AsyncClient()
    
    def get(self, config_name: str):
        """Get a configuration from the config store."""
        async_result = AsyncResult()
        
        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
        
        # Use gevent to make the HTTP request asynchronously
        def fetch_config():
            try:
                import httpx
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        async_result.set(data["data"])
                    else:
                        async_result.set_exception(Exception(f"Failed to get config: {response.text}"))
            except Exception as e:
                async_result.set_exception(e)
        
        gevent.spawn(fetch_config)
        return async_result
    
    def set(self, config_name: str, config_data: Any):
        """Set a configuration in the config store."""
        async_result = AsyncResult()
        
        # Prepare request
        request_url = f"http://{self._agent._host}:{self._agent._port}/config-store/{self._agent.identity}/{config_name}"
        
        # Use gevent to make the HTTP request asynchronously
        def store_config():
            try:
                import httpx
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.put(request_url, json=config_data)
                    if response.status_code == 200:
                        async_result.set(True)
                    else:
                        async_result.set_exception(Exception(f"Failed to store config: {response.text}"))
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
                import httpx
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.delete(request_url)
                    if response.status_code == 200:
                        async_result.set(True)
                    else:
                        async_result.set_exception(Exception(f"Failed to delete config: {response.text}"))
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
                import httpx
                with httpx.Client() as client:  # Synchronous client for gevent compatibility
                    response = client.get(request_url)
                    if response.status_code == 200:
                        data = response.json()
                        if self._agent.identity in data["data"]:
                            async_result.set(data["data"][self._agent.identity])
                        else:
                            async_result.set([])  # No configs for this agent
                    else:
                        async_result.set_exception(Exception(f"Failed to list configs: {response.text}"))
            except Exception as e:
                async_result.set_exception(e)
        
        gevent.spawn(list_configs)
        return async_result
    
    def watch(self, config_name: str, callback: Callable):
        """Register a callback to be called when a configuration changes."""
        if config_name not in self._config_callbacks:
            self._config_callbacks[config_name] = []
        self._config_callbacks[config_name].append(callback)
    
    def unwatch(self, config_name: str, callback: Callable = None):
        """Unregister a callback for configuration changes."""
        if callback is None:
            # Remove all callbacks for this config
            if config_name in self._config_callbacks:
                del self._config_callbacks[config_name]
        else:
            # Remove specific callback
            if config_name in self._config_callbacks:
                self._config_callbacks[config_name] = [
                    cb for cb in self._config_callbacks[config_name] if cb != callback
                ]
                
    def handle_update(self, config_name: str):
        """Handle a configuration update notification."""
        if config_name in self._config_callbacks:
            # Get the updated config
            config_data = self.get(config_name).get()
            # Call all callbacks
            for callback in self._config_callbacks[config_name]:
                try:
                    callback(config_name, config_data)
                except Exception as e:
                    print(f"Error in config update callback: {e}")
    
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
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
        }
        for abbr, num in months.items():
            if name.startswith(abbr):
                return num
        raise ValueError(f"Invalid month name: {name}")
    
    def _day_name_to_number(self, name):
        """Convert a day of week name to its corresponding number (0-6, 0=Sunday)."""
        name = name.lower()
        days = {
            'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6
        }
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
        for _ in range(1000):
            # Check if this time matches the schedule
            if (next_time.month in self.months and
                next_time.day in self.days_of_month and
                next_time.hour in self.hours and
                next_time.minute in self.minutes and
                next_time.weekday() in self.days_of_week):
                return next_time
            
            # Increment to the next minute
            next_time += timedelta(minutes=1)
        
        # If we get here, we couldn't find a match within the limit
        raise ValueError("Could not find next scheduled time within reasonable limits")




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
            next_time = self.cron_timer.get_next(
                datetime.fromtimestamp(time.time())
            )
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
            raise ValueError("Schedule must be either a positive number (interval) or a cron expression")
        
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
        return {name: {
            "interval" if not event.is_cron else "cron": event.interval if not event.is_cron else event.cron_expression,
            "next_time": datetime.fromtimestamp(event.next_time).isoformat(),
            "running": event.running
        } for name, event in self._events.items()}
    
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

class Agent:
    """A gevent-based agent that connects to the VOLTTRON MessageBus."""
    
    def __init__(self, identity: str, host: str = "127.0.0.1", port: int = 8000):
        self.identity = identity
        self._host = host
        self._port = port
        self.websocket_url = f"ws://{host}:{port}/ws/{identity}"
        self.websocket = None
        self.connected = False
        self.received_messages = []
        self._listener_greenlet = None
        self.rpc_responses = {}  # Maps message IDs to AsyncResults
        self._stop_event = gevent.event.Event() # type: ignore
        
        # Create subsystems
        self.core = Core(self)
        self.config = ConfigStore(self)  # Initialize config before VIP
        
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
            on_open=self.__on_ws_open__
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
        
        # Fire the onconnected event with self as sender
        self.core.fire_event('onconnected', sender=self)

        # Trigger the onconfigure event - load config before starting
        self._load_configs()
        
        # Fire the onstart event with self as sender
        self.core.fire_event('onstart', sender=self)

        # Start periodic tasks
        self.core.start_periodic_tasks()

    def _load_configs(self):
        """Load configurations and trigger the onconfigure event."""
        try:
            # List available configurations for this agent
            configs = self.config.list().get(timeout=5)
            
            # Fire the onconfigure event
            self.core.fire_event('onconfigure', sender=self, configs=configs)
            
        except Exception as e:
            print(f"Error loading configurations: {e}")
    
    def disconnect(self):
        """Disconnect from the message bus."""
        if self.websocket and self.connected:
            # Fire the onstop event with self as sender
            self.core.fire_event('onstop', sender=self)
            
            self.websocket.close()
            # Wait for the close to complete
            if self._listener_greenlet:
                self._listener_greenlet.join(timeout=1)
            
            self.connected = False
            print(f"Agent {self.identity} disconnected")
            
            # Fire the ondisconnected event with self as sender
            self.core.fire_event('ondisconnected', sender=self)

    def __on_ws_open__(self, ws):
        """Callback when WebSocket connection is opened."""
        self.connected = True
        print(f"DEBUG: Agent {self.identity} websocket connection opened")
    
    def __on_ws_message__(self, ws, message):
        """Internal callback when a WebSocket message is received."""
        try:
            data = json.loads(message)
            self.received_messages.append(data)
            print(f"Agent {self.identity} received: {data}")
            
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
                async_result = self.vip.rpc.handle_request(sender, method_name, args, kwargs, msg_id)
                
                # Wait for the result and send the response
                def send_response():
                    try:
                        # Wait for the result (with timeout)
                        result = async_result.get(timeout=10)
                        # Send successful response
                        print(f"DEBUG: Agent {self.identity} sending RPC response: {result}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_response",
                            "msg_id": msg_id,
                            "result": result
                        }))
                    except Exception as e:
                        # Send error response
                        error = str(e)
                        print(f"DEBUG: Agent {self.identity} sending RPC error response: {error}")
                        self.websocket.send(json.dumps({
                            "type": "rpc_error",
                            "msg_id": msg_id,
                            "error": error
                        }))
                
                # Spawn a greenlet to process the response asynchronously
                gevent.spawn(send_response)
                
            elif msg_type == "rpc_response":
                # Handle RPC response
                msg_id = data.get("msg_id")
                result = data.get("result")
                print(f"DEBUG: Agent {self.identity} received RPC response for msg_id {msg_id}: {result}")
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
                print(f"DEBUG: Agent {self.identity} received RPC error for msg_id {msg_id}: {error}")
                if msg_id in self.rpc_responses:
                    # Get the AsyncResult for this message ID and set the exception
                    async_result = self.rpc_responses.pop(msg_id)
                    async_result.set_exception(Exception(error))
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
                        async_result.set_exception(Exception(args[0]))  # Assuming first arg is error message
            
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
                        peer=peer, 
                        subsystem="rpc_response",
                        args=[result, msg_id]
                    )
                except Exception as e:
                    # Send error response via VIP
                    self.vip.send_message(
                        peer=peer, 
                        subsystem="rpc_error",
                        args=[str(e), msg_id]
                    )
            
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


def run_agent(agent_class, identity=None, **kwargs):
    """Run an agent from the command line."""
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", help="Agent identity", default=identity)
    parser.add_argument("--host", help="Message bus host", default="127.0.0.1")
    parser.add_argument("--port", help="Message bus port", type=int, default=8000)
    
    args = parser.parse_args()
    
    # Create the agent
    agent_identity = args.identity or identity or agent_class.__name__.lower()
    agent = agent_class(identity=agent_identity, host=args.host, port=args.port, **kwargs)
    
    # Run the agent
    return agent.run()