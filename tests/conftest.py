"""
Test configuration for pytest
"""
import pytest
import asyncio
import time
from aems.server.fastapi_message_bus import FastAPIMessageBus


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def message_bus():
    """Create and start a message bus for testing."""
    bus = FastAPIMessageBus(host="127.0.0.1", port=8888)
    
    try:
        # Start the server
        bus.start()
        
        # Give the server time to start
        time.sleep(2)
        
        # Verify server is running
        if not bus.is_running():
            raise RuntimeError("Failed to start message bus server for testing")
        
        print("Message bus server started for testing")
        
        yield bus
        
    finally:
        # Cleanup
        if bus.is_running():
            bus.stop()
            time.sleep(1)
        print("Message bus server stopped after testing")


@pytest.fixture
def test_port():
    """Return a test port number."""
    return 8888
