"""
Test module for authentication endpoint functionality.
"""

import pytest
import httpx
import jwt
from datetime import datetime, timedelta


class TestAuthentication:
    """Test cases for the /authenticate endpoint."""

    def test_authenticate_form_data_success(self, message_bus):
        """Test successful authentication with form data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Test with form data (as used in the user's example)
        form_data = {'username': 'admin', 'password': 'admin'}
        
        response = httpx.post(
            f'{base_url}/authenticate',
            data=form_data,
            timeout=10.0
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Check response structure
        assert 'status' in data
        assert 'message' in data
        assert 'username' in data
        assert 'access_token' in data
        assert 'refresh_token' in data
        
        # Check response values
        assert data['status'] == 'success'
        assert data['message'] == 'Authentication successful'
        assert data['username'] == 'admin'
        
        # Verify tokens are present and non-empty
        assert data['access_token']
        assert data['refresh_token']
        
        # Verify tokens are valid JWT format (should have 3 parts separated by dots)
        assert len(data['access_token'].split('.')) == 3
        assert len(data['refresh_token'].split('.')) == 3

    def test_authenticate_json_data_success(self, message_bus):
        """Test successful authentication with JSON data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Test with JSON data
        auth_data = {'username': 'testuser', 'password': 'testpass'}
        
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)
        
        assert response.status_code == 200
        data = response.json()
        
        # Check response structure
        assert 'status' in data
        assert 'message' in data
        assert 'username' in data
        assert 'access_token' in data
        assert 'refresh_token' in data
        
        # Check response values
        assert data['status'] == 'success'
        assert data['message'] == 'Authentication successful'
        assert data['username'] == 'testuser'
        
        # Verify tokens are present and non-empty
        assert data['access_token']
        assert data['refresh_token']
        
        # Verify tokens are valid JWT format (should have 3 parts separated by dots)
        assert len(data['access_token'].split('.')) == 3
        assert len(data['refresh_token'].split('.')) == 3

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
        access_payload = jwt.decode(data['access_token'], secret_key, algorithms=[algorithm])
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

    def test_authenticate_missing_username_json(self, message_bus):
        """Test authentication with missing username in JSON data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Missing username
        auth_data = {'password': 'testpass'}
        
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)
        
        assert response.status_code == 400
        data = response.json()
        assert 'Missing required fields' in data['detail']

    def test_authenticate_missing_username_form(self, message_bus):
        """Test authentication with missing username in form data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Missing username
        form_data = {'password': 'testpass'}
        
        response = httpx.post(f'{base_url}/authenticate', data=form_data, timeout=10.0)
        
        assert response.status_code == 400
        data = response.json()
        assert 'Missing required fields' in data['detail']

    def test_authenticate_missing_password_json(self, message_bus):
        """Test authentication with missing password in JSON data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Missing password
        auth_data = {'username': 'testuser'}
        
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)
        
        assert response.status_code == 400
        data = response.json()
        assert 'Missing required fields' in data['detail']

    def test_authenticate_missing_password_form(self, message_bus):
        """Test authentication with missing password in form data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Missing password
        form_data = {'username': 'testuser'}
        
        response = httpx.post(f'{base_url}/authenticate', data=form_data, timeout=10.0)
        
        assert response.status_code == 400
        data = response.json()
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

    def test_authenticate_empty_credentials_json(self, message_bus):
        """Test authentication with empty credentials in JSON data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Empty credentials
        auth_data = {'username': '', 'password': ''}
        
        response = httpx.post(f'{base_url}/authenticate', json=auth_data, timeout=10.0)
        
        assert response.status_code == 401
        data = response.json()
        assert 'Invalid credentials' in data['detail']

    def test_authenticate_empty_credentials_form(self, message_bus):
        """Test authentication with empty credentials in form data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Empty credentials
        form_data = {'username': '', 'password': ''}
        
        response = httpx.post(f'{base_url}/authenticate', data=form_data, timeout=10.0)
        
        assert response.status_code == 401
        data = response.json()
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
            assert 'access_token' in data
            assert 'refresh_token' in data

    def test_authenticate_user_example(self, message_bus):
        """Test authentication exactly as in the user's example."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'
        
        # Test exactly as the user's example using httpx.post with data parameter
        response = httpx.post(
            f'{base_url}/authenticate',
            data={'username': 'admin', 'password': 'admin'},
            timeout=10.0
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Should be able to extract access_token like in user's example
        access_token = data['access_token']  # This should work exactly as user expects
        assert access_token
        assert len(access_token.split('.')) == 3  # Valid JWT format
        
        # Verify full response structure
        assert data['status'] == 'success'
        assert data['username'] == 'admin'
        assert 'refresh_token' in data
