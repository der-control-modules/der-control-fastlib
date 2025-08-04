"""
Test module for authentication endpoint functionality.
"""

import pytest
import httpx
import jwt
from datetime import datetime, timedelta


class TestAuthentication:
    """Test cases for the /authenticate endpoint."""

    def test_successful_authentication(self, message_bus):
        """Test successful authentication with valid credentials."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data
        auth_data = {'username': 'testuser', 'password': 'testpass'}

        # Make request
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check response structure
        assert 'status' in data
        assert 'message' in data
        assert 'username' in data
        assert 'token' in data
        assert 'refresh_token' in data

        assert data['status'] == 'success'
        assert data['username'] == 'testuser'
        assert data['message'] == 'Authentication successful'

        # Verify tokens are present and non-empty
        assert data['token']
        assert data['refresh_token']

    def test_jwt_token_validation(self, message_bus):
        """Test that JWT tokens are properly formatted and contain correct data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data
        auth_data = {'username': 'testuser', 'password': 'testpass'}

        # Make request
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        assert response.status_code == 200
        data = response.json()

        # JWT configuration (should match server configuration)
        secret_key = "your-secret-key-change-in-production"
        algorithm = "HS256"

        # Decode and validate access token
        access_payload = jwt.decode(data['token'], secret_key, algorithms=[algorithm])
        refresh_payload = jwt.decode(data['refresh_token'], secret_key, algorithms=[algorithm])

        # Verify access token payload
        assert access_payload['sub'] == 'testuser'
        assert access_payload['type'] == 'access'
        assert 'iat' in access_payload
        assert 'exp' in access_payload

        # Verify refresh token payload
        assert refresh_payload['sub'] == 'testuser'
        assert refresh_payload['type'] == 'refresh'
        assert 'iat' in refresh_payload
        assert 'exp' in refresh_payload

        # Verify token expiration times (approximately)
        now = datetime.utcnow()
        access_exp = datetime.utcfromtimestamp(access_payload['exp'])
        refresh_exp = datetime.utcfromtimestamp(refresh_payload['exp'])

        # Access token should expire in about 1 hour (with some tolerance)
        access_duration = access_exp - now
        assert timedelta(minutes=55) <= access_duration <= timedelta(minutes=65)

        # Refresh token should expire in about 7 days (with some tolerance)
        refresh_duration = refresh_exp - now
        assert timedelta(days=6, hours=23) <= refresh_duration <= timedelta(days=7, hours=1)

    def test_missing_username(self, message_bus):
        """Test authentication with missing username field."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data with missing username
        auth_data = {'password': 'testpass'}

        # Make request
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        # Verify error response
        assert response.status_code == 400
        data = response.json()
        assert 'detail' in data
        assert 'Missing required fields' in data['detail']

    def test_missing_password(self, message_bus):
        """Test authentication with missing password field."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data with missing password
        auth_data = {'username': 'testuser'}

        # Make request
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        # Verify error response
        assert response.status_code == 400
        data = response.json()
        assert 'detail' in data
        assert 'Missing required fields' in data['detail']

    def test_empty_credentials(self, message_bus):
        """Test authentication with empty username and password."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data with empty credentials
        auth_data = {'username': '', 'password': ''}

        # Make request
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        # Verify error response
        assert response.status_code == 401
        data = response.json()
        assert 'detail' in data
        assert 'Invalid credentials' in data['detail']

    def test_malformed_json(self, message_bus):
        """Test authentication with malformed JSON data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Make request with invalid JSON
        response = httpx.post(
            f'{base_url}/authenticate',
            content="invalid-json",
            headers={'Content-Type': 'application/json'},
            timeout=10.0
        )

        # Verify error response
        assert response.status_code == 422  # FastAPI returns 422 for invalid JSON

    def test_non_json_content_type(self, message_bus):
        """Test authentication with non-JSON content type."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Make request with form data instead of JSON
        response = httpx.post(
            f'{base_url}/authenticate',
            data={'username': 'testuser', 'password': 'testpass'},
            timeout=10.0
        )

        # Verify error response (should expect JSON)
        assert response.status_code == 422  # FastAPI returns 422 for wrong content type

    @pytest.mark.parametrize("username,password,expected_status", [
        ("user1", "pass1", 200),
        ("admin", "secret", 200),
        ("test@example.com", "password123", 200),
        ("", "password", 401),
        ("username", "", 401),
        ("", "", 401),
    ])
    def test_various_credentials(self, message_bus, username, password, expected_status):
        """Test authentication with various credential combinations."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        auth_data = {'username': username, 'password': password}
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)

        assert response.status_code == expected_status

        if expected_status == 200:
            data = response.json()
            assert data['status'] == 'success'
            assert data['username'] == username
            assert 'token' in data
            assert 'refresh_token' in data
