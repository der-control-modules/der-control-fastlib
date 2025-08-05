"""
Test module for the GS (RPC) endpoint functionality.
"""

import pytest
import httpx


class TestGSEndpoint:
    """Test cases for the /gs endpoint."""

    def test_gs_endpoint_invalid_jsonrpc(self, message_bus):
        """Test GS endpoint with invalid JSON-RPC format."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data without proper JSON-RPC format
        rpc_data = {
            'id': 'test_agent',
            'method': 'test_method',
            'params': {'data': 'test'}
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        assert response.status_code == 400
        data = response.json()
        assert 'Invalid JSON-RPC format' in data['detail']

    def test_gs_endpoint_missing_id(self, message_bus):
        """Test GS endpoint with missing agent ID."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data without agent ID
        rpc_data = {
            'jsonrpc': '2.0',
            'method': 'test_method',
            'params': {'data': 'test'}
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        assert response.status_code == 400
        data = response.json()
        assert 'Missing required field: \'id\'' in data['detail']

    def test_gs_endpoint_missing_method(self, message_bus):
        """Test GS endpoint with missing method."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data without method
        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'test_agent',
            'params': {'data': 'test'}
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        assert response.status_code == 400
        data = response.json()
        assert 'Missing required field: \'method\'' in data['detail']

    def test_gs_endpoint_agent_not_connected(self, message_bus):
        """Test GS endpoint with agent that is not connected."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data for non-connected agent
        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'non_existent_agent',
            'method': 'test_method',
            'params': {
                'authentication': 'test_auth',
                'data': {'key': 'value'}
            }
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        assert response.status_code == 404
        data = response.json()
        assert 'is not connected' in data['detail']

    def test_gs_endpoint_valid_format(self, message_bus):
        """Test GS endpoint with valid JSON-RPC format but no connected agent (should return 404)."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Test data with proper JSON-RPC 2.0 format
        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'test_agent',
            'method': 'get_status',
            'params': {
                'authentication': 'bearer_token_123',
                'data': {
                    'request_id': '12345',
                    'timestamp': '2025-08-04T12:00:00Z'
                }
            }
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'test_agent' in data['detail']
        assert 'is not connected' in data['detail']

    def test_gs_endpoint_minimal_request(self, message_bus):
        """Test GS endpoint with minimal valid JSON-RPC request."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        # Minimal valid JSON-RPC request (no params)
        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'minimal_agent',
            'method': 'ping'
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'minimal_agent' in data['detail']
        assert 'is not connected' in data['detail']

    def test_gs_endpoint_auth_only(self, message_bus):
        """Test GS endpoint with authentication but no data."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'auth_agent',
            'method': 'authenticate',
            'params': {
                'authentication': 'secret_token_456'
            }
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'auth_agent' in data['detail']
        assert 'is not connected' in data['detail']

    @pytest.mark.parametrize("method,params", [
        ("get_status", {"authentication": "token123", "data": {"key": "value"}}),
        ("set_config", {"authentication": None, "data": {"config": "test"}}),
        ("restart", {"authentication": "bearer_xyz", "data": {}}),
        ("ping", {}),  # No params
    ])
    def test_gs_endpoint_various_methods(self, message_bus, method, params):
        """Test GS endpoint with various method and parameter combinations."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'test_agent',
            'method': method,
            'params': params
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'test_agent' in data['detail']
        assert 'is not connected' in data['detail']

    def test_gs_endpoint_malformed_json(self, message_bus):
        """Test GS endpoint with malformed JSON."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        response = httpx.post(
            f'{base_url}/gs',
            content="invalid-json",
            headers={'Content-Type': 'application/json'},
            timeout=10.0
        )

        # Should return 400 for malformed JSON (our endpoint catches ValueError and returns 400)
        assert response.status_code == 400

    def test_gs_endpoint_empty_params(self, message_bus):
        """Test GS endpoint with empty params object."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'empty_params_agent',
            'method': 'simple_call',
            'params': {}
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'empty_params_agent' in data['detail']
        assert 'is not connected' in data['detail']

    def test_gs_endpoint_complex_data(self, message_bus):
        """Test GS endpoint with complex data structures."""
        base_url = f'http://{message_bus.host}:{message_bus.port}'

        complex_data = {
            'nested': {
                'array': [1, 2, 3],
                'object': {'key': 'value'},
                'boolean': True,
                'null_value': None
            },
            'timestamp': '2025-08-04T12:00:00Z',
            'config': {
                'settings': ['option1', 'option2'],
                'metadata': {'version': '1.0', 'author': 'test'}
            }
        }

        rpc_data = {
            'jsonrpc': '2.0',
            'id': 'complex_agent',
            'method': 'process_complex_data',
            'params': {
                'authentication': 'complex_auth_token',
                'data': complex_data
            }
        }

        response = httpx.post(f'{base_url}/gs', json=rpc_data, timeout=10.0)

        # Should return 404 since no agent is connected
        assert response.status_code == 404
        data = response.json()
        assert 'complex_agent' in data['detail']
        assert 'is not connected' in data['detail']
