"""
Test configuration for pytest
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from .utils import (
    MessageBusManager,
    create_connected_test_agent,
    create_test_agent,
    get_random_open_port,
    get_test_port,
)


@pytest.fixture(autouse=True)
def _no_real_docker_daemon(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Points DOCKER_HOST at a socket that does not exist, so a bug that reaches the real
    # docker CLI fails to connect instead of touching a real stack. Matches any
    # test_docker*.py module (not just this one); uses the process tempdir, not
    # tmp_path, because a unix socket path is capped at 108 bytes.
    if not Path(request.module.__file__).name.startswith("test_docker"):
        return
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tempfile.gettempdir()}/derhost-test-no-daemon.sock")


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def message_bus():
    """Create and start a message bus for testing with isolated config store."""
    with MessageBusManager() as manager:
        bus, port = manager.start_bus()
        yield bus


@pytest.fixture(scope="function")
def message_bus_manager_fixture():
    """Provide a MessageBusManager for advanced test scenarios."""
    with MessageBusManager() as manager:
        yield manager


@pytest.fixture
def test_port():
    """Return the test port number from environment variable or default."""
    return get_test_port()


@pytest.fixture
def random_port():
    """Get a random open port for testing without environment variable side effects."""
    return get_random_open_port()


@pytest.fixture
def agent_factory():
    """Factory function for creating test agents."""

    def _create_agent(identity: str, agent_class=None, **kwargs):
        return create_test_agent(identity, agent_class, **kwargs)

    return _create_agent


@pytest.fixture
def connected_agent_factory():
    """Factory function for creating connected test agents."""

    def _create_connected_agent(identity: str, agent_class=None, **kwargs):
        return create_connected_test_agent(identity, agent_class, **kwargs)

    return _create_connected_agent
