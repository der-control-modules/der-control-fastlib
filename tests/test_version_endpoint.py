#!/usr/bin/env python3
"""
Test script to verify the version endpoint works
"""
from fastapi.testclient import TestClient

from aems.server.fastapi_message_bus import FastAPIMessageBus


def test_version_endpoint():
    """Test the version and health endpoints."""
    # Create test client
    server = FastAPIMessageBus()
    client = TestClient(server.app)

    # Test version endpoint
    response = client.get("/version")
    assert response.status_code == 200

    data = response.json()
    assert "version" in data
    assert "service" in data
    assert "status" in data
    assert data["service"] == "aems-server"
    assert data["status"] == "running"

    print(f"✅ Version endpoint test passed: {data}")

    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "active_connections" in data
    assert "service" in data
    assert data["status"] == "healthy"
    assert data["service"] == "aems-server"

    print(f"✅ Health endpoint test passed: {data}")


if __name__ == "__main__":
    test_version_endpoint()
    print("All tests passed!")
