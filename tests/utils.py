"""
Test utilities for AEMS testing framework.
"""

import os
import shutil
import socket
import tempfile
import time

from derhost.client.agent import Agent
from derhost.server.fastapi_message_bus import FastAPIMessageBus


def get_random_open_port() -> int:
    """Get a random open port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def is_port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a port is actually listening and accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            return result == 0
    except (TimeoutError, OSError):
        return False


class MessageBusManager:
    """Manager for test message bus instances."""

    def __init__(self):
        self.bus = None
        self.port = None
        self.temp_config_dir = None
        self.host = "127.0.0.1"

    def start_bus(
        self,
        port: int | None = None,
        host: str = "127.0.0.1",
        ws_ping_interval: float | None = None,
        ws_ping_timeout: float | None = None,
        max_rpcs_in_flight: int | None = None,
    ) -> tuple[FastAPIMessageBus, int]:
        """
        Start a message bus for testing.

        Args:
            port: Specific port to use, or None for random port
            host: Host to bind to (default: 127.0.0.1)
            ws_ping_interval: Forwarded to FastAPIMessageBus when given (#83:
                lets a test shorten the keepalive cycle instead of waiting on
                the production default).
            ws_ping_timeout: Forwarded to FastAPIMessageBus when given (#83).
            max_rpcs_in_flight: Forwarded to FastAPIMessageBus when given
                (#83), to test the per-connection RPC cap without waiting on
                the production default of 128.

        Returns:
            Tuple of (message_bus_instance, port_number)
        """
        if self.bus is not None:
            raise RuntimeError("Message bus is already running. Stop it first.")

        # Get port
        if port is None:
            port = get_random_open_port()

        self.port = port
        self.host = host

        # Export port as environment variable
        os.environ["AEMS_FASTAPI_TEST_PORT"] = str(port)

        # Create temporary config directory
        self.temp_config_dir = tempfile.mkdtemp(prefix="aems_test_config_")

        # Create and start the bus. Only forwarded when given, so every
        # existing caller keeps today's FastAPIMessageBus defaults unchanged.
        bus_kwargs = {}
        if ws_ping_interval is not None:
            bus_kwargs["ws_ping_interval"] = ws_ping_interval
        if ws_ping_timeout is not None:
            bus_kwargs["ws_ping_timeout"] = ws_ping_timeout
        if max_rpcs_in_flight is not None:
            bus_kwargs["max_rpcs_in_flight"] = max_rpcs_in_flight
        self.bus = FastAPIMessageBus(
            host=host, port=port, config_store_dir=self.temp_config_dir, **bus_kwargs
        )

        print(
            f"Starting test message bus on {host}:{port} with config store: {self.temp_config_dir}"
        )
        self.bus.start()

        # Wait for server to be ready - check that port is actually listening
        max_wait_time = 10  # seconds
        check_interval = 0.5  # seconds
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            time.sleep(check_interval)
            elapsed_time += check_interval

            if self.bus.is_running():
                if (
                    hasattr(self.bus, "_server_thread")
                    and self.bus._server_thread.is_alive()
                ):
                    # Additionally check that the port is actually listening
                    if is_port_listening(host, port, timeout=0.5):
                        print(
                            f"Test message bus started successfully after {elapsed_time} seconds"
                        )
                        return self.bus, port

        # If we get here, startup failed
        self.stop_bus()
        raise RuntimeError(f"Failed to start test message bus after {max_wait_time}s")

    def stop_bus(self):
        """Stop the message bus and clean up resources."""
        if self.bus and self.bus.is_running():
            self.bus.stop()
            time.sleep(1)

        # Clean up temp directory
        if self.temp_config_dir:
            try:
                shutil.rmtree(self.temp_config_dir)
                print(f"Cleaned up test config store: {self.temp_config_dir}")
            except Exception as e:
                print(
                    f"Failed to clean up test config store {self.temp_config_dir}: {e}"
                )

        # Clean up environment variable
        if "AEMS_FASTAPI_TEST_PORT" in os.environ:
            del os.environ["AEMS_FASTAPI_TEST_PORT"]

        # Reset state
        self.bus = None
        self.port = None
        self.temp_config_dir = None
        print("Test message bus stopped and cleaned up")

    def create_agent(self, identity: str, agent_class=None, **kwargs) -> Agent:
        """
        Create an agent connected to the test message bus.

        Args:
            identity: Agent identity
            agent_class: Agent class to instantiate (default: None, uses Agent)
            **kwargs: Additional arguments for Agent constructor

        Returns:
            Agent instance (not connected)
        """
        if self.bus is None or not self.bus.is_running():
            raise RuntimeError("Message bus is not running. Start it first.")

        # Use Agent class if none specified
        if agent_class is None:
            agent_class = Agent

        # Create agent with test bus connection info
        agent = agent_class(identity=identity, host=self.host, port=self.port, **kwargs)

        return agent

    def create_connected_agent(
        self, identity: str, agent_class=None, **kwargs
    ) -> Agent:
        """
        Create an agent and establish WebSocket connection to the test message bus.

        Args:
            identity: Agent identity
            agent_class: Agent class to instantiate (default: None, uses Agent)
            **kwargs: Additional arguments for Agent constructor

        Returns:
            Agent instance (connected and ready)
        """
        # Create the agent
        agent = self.create_agent(identity, agent_class, **kwargs)

        # Connect to the message bus
        print(f"Connecting test agent: {identity}")
        agent.connect()

        # Verify connection was established
        if not agent.connected:
            raise RuntimeError(
                f"Failed to connect agent {identity} to test message bus"
            )

        print(f"Test agent {identity} connected successfully")
        return agent

    def get_port(self) -> int:
        """Get the current message bus port."""
        if self.port is None:
            raise RuntimeError("Message bus is not started")
        return self.port

    def get_base_url(self) -> str:
        """Get the base URL for the message bus."""
        if self.port is None:
            raise RuntimeError("Message bus is not started")
        return f"http://{self.host}:{self.port}"

    def get_ws_url(self, identity: str) -> str:
        """Get the WebSocket URL for an agent identity."""
        if self.port is None:
            raise RuntimeError("Message bus is not started")
        return f"ws://{self.host}:{self.port}/ws/{identity}"

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - clean up resources."""
        self.stop_bus()


# Convenience functions for simple use cases
def start_test_message_bus(
    port: int | None = None, host: str = "127.0.0.1"
) -> tuple[FastAPIMessageBus, int]:
    """
    Start a test message bus (convenience function).

    Args:
        port: Specific port to use, or None for random port
        host: Host to bind to

    Returns:
        Tuple of (message_bus_instance, port_number)
    """
    manager = MessageBusManager()
    return manager.start_bus(port, host)


def create_test_agent(
    identity: str,
    agent_class=None,
    port: int | None = None,
    host: str = "127.0.0.1",
    **kwargs,
) -> Agent:
    """
    Create a test agent (convenience function).

    Args:
        identity: Agent identity
        agent_class: Agent class to instantiate (default: None, uses Agent)
        port: Port where message bus is running (uses AEMS_FASTAPI_TEST_PORT if None)
        host: Host where message bus is running
        **kwargs: Additional arguments for Agent constructor

    Returns:
        Agent instance (not connected)
    """
    if port is None:
        port = int(os.environ.get("AEMS_FASTAPI_TEST_PORT", 8888))

    # Use Agent class if none specified
    if agent_class is None:
        agent_class = Agent

    agent = agent_class(identity=identity, host=host, port=port, **kwargs)

    return agent


def create_connected_test_agent(
    identity: str,
    agent_class=None,
    port: int | None = None,
    host: str = "127.0.0.1",
    **kwargs,
) -> Agent:
    """
    Create a test agent and establish WebSocket connection (convenience function).

    Args:
        identity: Agent identity
        agent_class: Agent class to instantiate (default: None, uses Agent)
        port: Port where message bus is running (uses AEMS_FASTAPI_TEST_PORT if None)
        host: Host where message bus is running
        **kwargs: Additional arguments for Agent constructor

    Returns:
        Agent instance (connected and ready)
    """
    # Create the agent
    agent = create_test_agent(identity, agent_class, port, host, **kwargs)

    # Connect to the message bus
    print(f"Connecting test agent: {identity}")
    agent.connect()

    # Verify connection was established
    if not agent.connected:
        raise RuntimeError(f"Failed to connect agent {identity} to test message bus")

    print(f"Test agent {identity} connected successfully")
    return agent


def get_test_port() -> int:
    """Get the current test port from environment variable."""
    return int(os.environ.get("AEMS_FASTAPI_TEST_PORT", 8888))


def get_test_base_url(host: str = "127.0.0.1") -> str:
    """Get the base URL for the test message bus."""
    port = get_test_port()
    return f"http://{host}:{port}"


def get_test_ws_url(identity: str, host: str = "127.0.0.1") -> str:
    """Get the WebSocket URL for a test agent."""
    port = get_test_port()
    return f"ws://{host}:{port}/ws/{identity}"
