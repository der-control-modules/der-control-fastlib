"""
Test agent RPC (Remote Procedure Call) functionality using pytest
"""
import pytest
import gevent
from aems.client.agent import Agent


class TestAgentRPC:
    """Test agent RPC functionality."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus):
        """Set up test agents for RPC tests with the running message bus."""
        # Store the message bus reference
        self.message_bus = message_bus

        # Create agents with the correct port
        self.server_agent = Agent("rpc_server", port=8888)
        self.client_agent = Agent("rpc_client", port=8888)

        # Connect both agents
        self.server_agent.connect()
        self.client_agent.connect()

        # Wait for connections
        gevent.sleep(1)

        yield

        # Cleanup
        if hasattr(self, 'server_agent'):
            self.server_agent.disconnect()
        if hasattr(self, 'client_agent'):
            self.client_agent.disconnect()

    def test_basic_rpc_call(self):
        """Test basic RPC call functionality."""
        # Register an RPC method on the server
        def echo_method(message):
            return f"Echo: {message}"

        self.server_agent.vip.rpc.export_method("echo", echo_method)
        gevent.sleep(1)  # Wait for method registration

        # Make RPC call from client
        result = self.client_agent.vip.rpc.call("rpc_server", "echo", "Hello World")
        response = result.get(timeout=5)

        assert response == "Echo: Hello World", f"Expected 'Echo: Hello World', got '{response}'"

    def test_rpc_with_multiple_parameters(self):
        """Test RPC call with multiple parameters."""
        # Register method that takes multiple parameters
        def add_numbers(a, b, c=0):
            return a + b + c

        self.server_agent.vip.rpc.export_method("add", add_numbers)
        gevent.sleep(1)

        # Test with positional arguments
        result1 = self.client_agent.vip.rpc.call("rpc_server", "add", 10, 20)
        response1 = result1.get(timeout=5)
        assert response1 == 30, f"Expected 30, got {response1}"

        # Test with keyword arguments
        result2 = self.client_agent.vip.rpc.call("rpc_server", "add", 10, 20, c=5)
        response2 = result2.get(timeout=5)
        assert response2 == 35, f"Expected 35, got {response2}"

    def test_rpc_error_handling(self):
        """Test RPC error handling."""
        # Register method that can raise an exception
        def divide_numbers(a, b):
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b

        self.server_agent.vip.rpc.export_method("divide", divide_numbers)
        gevent.sleep(1)

        # Test successful call
        result1 = self.client_agent.vip.rpc.call("rpc_server", "divide", 10, 2)
        response1 = result1.get(timeout=5)
        assert response1 == 5.0, f"Expected 5.0, got {response1}"

        # Test error case
        result2 = self.client_agent.vip.rpc.call("rpc_server", "divide", 10, 0)
        try:
            result2.get(timeout=5)
            assert False, "Should have raised an exception"
        except Exception as e:
            assert "Cannot divide by zero" in str(e), f"Expected division error, got {e}"

    def test_rpc_nonexistent_method(self):
        """Test calling a non-existent RPC method."""
        # Try to call a method that doesn't exist
        result = self.client_agent.vip.rpc.call("rpc_server", "nonexistent_method", "arg")

        try:
            result.get(timeout=5)
            assert False, "Should have raised an exception for non-existent method"
        except Exception as e:
            # Should get some kind of method not found error
            assert "method" in str(e).lower() or "not found" in str(e).lower(), f"Expected method error, got {e}"

    def test_rpc_to_nonexistent_agent(self):
        """Test RPC call to a non-existent agent."""
        result = self.client_agent.vip.rpc.call("nonexistent_agent", "some_method", "arg")

        try:
            result.get(timeout=5)
            assert False, "Should have raised an exception for non-existent agent"
        except Exception as e:
            # Should get some kind of agent not found or timeout error
            assert any(word in str(e).lower() for word in ["timeout", "not found", "unreachable"]), f"Expected agent error, got {e}"

    def test_rpc_with_complex_data(self):
        """Test RPC with complex data structures."""
        # Register method that handles complex data
        def process_data(data):
            if isinstance(data, dict):
                return {k: v * 2 if isinstance(v, (int, float)) else v for k, v in data.items()}
            elif isinstance(data, list):
                return [x * 2 if isinstance(x, (int, float)) else x for x in data]
            else:
                return data

        self.server_agent.vip.rpc.export_method("process", process_data)
        gevent.sleep(1)

        # Test with dictionary
        test_dict = {"numbers": 5, "text": "hello", "float": 3.14}
        result1 = self.client_agent.vip.rpc.call("rpc_server", "process", test_dict)
        response1 = result1.get(timeout=5)
        expected1 = {"numbers": 10, "text": "hello", "float": 6.28}
        assert response1 == expected1, f"Expected {expected1}, got {response1}"

        # Test with list
        test_list = [1, "text", 2.5, 3]
        result2 = self.client_agent.vip.rpc.call("rpc_server", "process", test_list)
        response2 = result2.get(timeout=5)
        expected2 = [2, "text", 5.0, 6]
        assert response2 == expected2, f"Expected {expected2}, got {response2}"

    def test_bidirectional_rpc(self):
        """Test bidirectional RPC calls (both agents can call each other)."""
        # Register methods on both agents
        def server_method(value):
            return f"Server processed: {value}"

        def client_method(value):
            return f"Client processed: {value}"

        self.server_agent.vip.rpc.export_method("server_process", server_method)
        self.client_agent.vip.rpc.export_method("client_process", client_method)
        gevent.sleep(1)

        # Client calls server
        result1 = self.client_agent.vip.rpc.call("rpc_server", "server_process", "data1")
        response1 = result1.get(timeout=5)
        assert response1 == "Server processed: data1", f"Expected server response, got {response1}"

        # Server calls client
        result2 = self.server_agent.vip.rpc.call("rpc_client", "client_process", "data2")
        response2 = result2.get(timeout=5)
        assert response2 == "Client processed: data2", f"Expected client response, got {response2}"
