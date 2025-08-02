"""
Test the version and health endpoints of the FastAPI message bus
"""

import pytest
from fastapi.testclient import TestClient
from aems.server.fastapi_message_bus import FastAPIMessageBus


class TestVersionEndpoints:
    """Test version and health endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        server = FastAPIMessageBus()
        return TestClient(server.app)

    def test_version_endpoint(self, client):
        """Test the /version endpoint returns correct information."""
        response = client.get("/version")

        assert response.status_code == 200
        data = response.json()

        assert "version" in data
        assert "service" in data
        assert "status" in data
        assert data["service"] == "aems-server"
        assert data["status"] == "running"
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_health_endpoint(self, client):
        """Test the /health endpoint returns correct information."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "version" in data
        assert "active_connections" in data
        assert "service" in data
        assert data["status"] == "healthy"
        assert data["service"] == "aems-server"
        assert isinstance(data["active_connections"], int)
        assert data["active_connections"] >= 0

    def test_invalid_endpoint(self, client):
        """Test that invalid endpoints return 404."""
        response = client.get("/invalid")
        assert response.status_code == 404
