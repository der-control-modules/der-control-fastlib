#!/usr/bin/env python3
"""
Simple test script for the RPC endpoint
"""

import json
import time

import httpx

from aems.server.fastapi_message_bus import FastAPIMessageBus


def test_rpc_endpoint():
    """Test the RPC endpoint functionality"""

    # Start the server
    print("Starting FastAPI server...")
    bus = FastAPIMessageBus(host="127.0.0.1", port=8893)
    bus.start()
    time.sleep(2)

    try:
        base_url = f"http://{bus.host}:{bus.port}"

        # Test valid JSON-RPC 2.0 format with non-connected agent
        print("\n1. Testing valid JSON-RPC format with non-connected agent...")
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "test_agent",
            "method": "get_status",
            "params": {
                "authentication": "bearer_token_123",
                "data": {"request_id": "12345", "timestamp": "2025-08-04T12:00:00Z"},
            },
        }

        response = httpx.post(f"{base_url}/rpc", json=rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test missing jsonrpc field
        print("\n2. Testing missing jsonrpc field...")
        invalid_rpc_data = {"id": "test_agent", "method": "get_status", "params": {"data": "test"}}
        response = httpx.post(f"{base_url}/rpc", json=invalid_rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code} (Expected: 400)")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test missing id field
        print("\n3. Testing missing id field...")
        invalid_rpc_data = {"jsonrpc": "2.0", "method": "get_status", "params": {"data": "test"}}
        response = httpx.post(f"{base_url}/rpc", json=invalid_rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code} (Expected: 400)")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test missing method field
        print("\n4. Testing missing method field...")
        invalid_rpc_data = {"jsonrpc": "2.0", "id": "test_agent", "params": {"data": "test"}}
        response = httpx.post(f"{base_url}/rpc", json=invalid_rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code} (Expected: 400)")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test with minimal valid JSON-RPC (no params)
        print("\n5. Testing minimal valid JSON-RPC...")
        minimal_rpc_data = {"jsonrpc": "2.0", "id": "another_agent", "method": "ping"}
        response = httpx.post(f"{base_url}/rpc", json=minimal_rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code} (Expected: 404 - agent not connected)")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test with authentication but no data
        print("\n6. Testing with authentication but no data...")
        auth_only_rpc_data = {
            "jsonrpc": "2.0",
            "id": "auth_agent",
            "method": "authenticate",
            "params": {"authentication": "secret_token_456"},
        }
        response = httpx.post(f"{base_url}/rpc", json=auth_only_rpc_data, timeout=10.0)
        print(f"Status Code: {response.status_code} (Expected: 404 - agent not connected)")
        print(f"Response: {json.dumps(response.json(), indent=2)}")

        # Test malformed JSON
        print("\n7. Testing malformed JSON...")
        try:
            response = httpx.post(
                f"{base_url}/rpc",
                content="invalid-json",
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
            print(f"Status Code: {response.status_code} (Expected: 422)")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"Expected error with malformed JSON: {e}")

        print("\n✅ All RPC endpoint tests completed!")

    finally:
        # Stop the server
        print("\nStopping server...")
        bus.stop()


if __name__ == "__main__":
    test_rpc_endpoint()
