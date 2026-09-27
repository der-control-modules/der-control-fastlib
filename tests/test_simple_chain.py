"""
Simple test to demonstrate and fix the chained RPC blocking issue.
"""

import logging
import time

import gevent
import pytest

_log = logging.getLogger(__name__)


class TestSimpleChain:
    """Test simple chained RPC to identify the blocking issue."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus_manager_fixture):
        """Set up test agents for chained RPC tests."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create agents for simple chain A->B
        self.agent_a = self.manager.create_connected_agent("agent_a")
        self.agent_b = self.manager.create_connected_agent("agent_b")
        self.external_client = self.manager.create_connected_agent("external_client")

        # Wait for connections
        gevent.sleep(1)

        yield

        # Cleanup
        for agent in [self.agent_a, self.agent_b, self.external_client]:
            if agent:
                agent.disconnect()

    def test_blocking_chained_rpc_is_now_fixed(self):
        """Verify that the chained RPC blocking issue has been resolved."""

        # Set up Agent B with a simple method
        def simple_method(value):
            _log.debug(f"Agent B executing simple_method with {value}")
            return f"Agent B processed: {value}"

        self.agent_b.vip.rpc.export_method("simple_method", simple_method)

        # Set up Agent A with what used to be a blocking nested call (but now works!)
        def formerly_blocking_chain_method(input_value):
            _log.debug(
                f"Agent A starting formerly_blocking_chain_method with {input_value}"
            )

            # This used to BLOCK the RPC handler greenlet, but now works correctly
            result = self.agent_a.vip.rpc.call("agent_b", "simple_method", input_value)
            processed_value = result.get(timeout=5)  # This now works without blocking!

            final_result = f"Agent A chained result: {processed_value}"
            _log.debug(
                f"Agent A completed formerly_blocking_chain_method: {final_result}"
            )
            return final_result

        self.agent_a.vip.rpc.export_method(
            "chain_method", formerly_blocking_chain_method
        )
        gevent.sleep(1)  # Wait for method registration

        # External client calls Agent A, which should chain to Agent B (now works!)
        _log.info("=== Testing FIXED chained RPC ===")
        start_time = time.time()

        result = self.external_client.vip.rpc.call(
            "agent_a", "chain_method", "test_input"
        )
        response = result.get(timeout=12)
        end_time = time.time()

        _log.info(
            f"FIXED chain completed in {end_time - start_time:.2f} seconds: {response}"
        )

        # Verify it works correctly now
        assert response == "Agent A chained result: Agent B processed: test_input"
        assert (
            end_time - start_time < 2
        ), f"Should complete quickly, took {end_time - start_time:.2f}s"

    def test_non_blocking_chained_rpc(self):
        """Demonstrate the fix using non-blocking chained RPC calls."""

        # Set up Agent B with a simple method
        def simple_method(value):
            _log.debug(f"Agent B executing simple_method with {value}")
            return f"Agent B processed: {value}"

        self.agent_b.vip.rpc.export_method("simple_method", simple_method)

        # Set up Agent A with automatic non-blocking (fixed in RPC subsystem)
        def automatic_chain_method(input_value):
            _log.debug(f"Agent A starting automatic_chain_method with {input_value}")

            # Now this should work automatically without manual gevent.spawn!
            # The RPC subsystem now handles this internally
            async_result = self.agent_a.vip.rpc.call(
                "agent_b", "simple_method", input_value
            )
            processed_value = async_result.get(timeout=5)

            final_result = f"Agent A chained result: {processed_value}"
            _log.debug(f"Agent A completed automatic_chain_method: {final_result}")
            return final_result

        self.agent_a.vip.rpc.export_method("chain_method", automatic_chain_method)
        gevent.sleep(1)  # Wait for method registration

        # External client calls Agent A, which should chain to Agent B
        _log.info("=== Testing NON-BLOCKING chained RPC ===")
        start_time = time.time()

        result = self.external_client.vip.rpc.call(
            "agent_a", "chain_method", "test_input"
        )
        response = result.get(timeout=12)
        end_time = time.time()

        _log.info(
            f"NON-BLOCKING chain completed in {end_time - start_time:.2f} seconds: {response}"
        )

        # Verify it worked
        assert "Agent A chained result: Agent B processed: test_input" == response
        assert (
            end_time - start_time < 5
        ), f"Should complete quickly, took {end_time - start_time:.2f}s"

    def test_concurrent_rpc_handling(self):
        """Verify that multiple concurrent RPC calls overlap rather than run one after another."""

        # Each call records its own [start, end) window so the test can check
        # for overlap directly, instead of inferring it from total wall time.
        call_windows = []

        # Set up Agent B with a method that has a delay
        def slow_method(value, delay=0.5):
            _log.debug(f"Agent B executing slow_method with {value}, delay={delay}")
            start = time.time()
            gevent.sleep(delay)  # Simulate processing time
            call_windows.append((start, time.time()))
            return f"Agent B slow result: {value}"

        self.agent_b.vip.rpc.export_method("slow_method", slow_method)

        # Concurrent method - make multiple calls and verify they all work
        def concurrent_method(values):
            greenlets = []

            for value in values:

                def make_call(v=value):
                    result = self.agent_a.vip.rpc.call("agent_b", "slow_method", v, 0.5)
                    return result.get(timeout=3)

                greenlet = gevent.spawn(make_call)
                greenlets.append(greenlet)

            gevent.joinall(greenlets, timeout=8)

            results = []
            for greenlet in greenlets:
                if greenlet.ready() and greenlet.successful():
                    results.append(greenlet.value)
                else:
                    results.append("ERROR")

            return results

        self.agent_a.vip.rpc.export_method("concurrent_method", concurrent_method)
        gevent.sleep(1)

        test_values = [
            "item1",
            "item2",
            "item3",
            "item4",
            "item5",
        ]  # Test with more items

        # Test concurrent calls
        _log.info("=== Testing CONCURRENT nested calls ===")
        start_time = time.time()
        result = self.external_client.vip.rpc.call(
            "agent_a", "concurrent_method", test_values
        )
        response = result.get(timeout=15)
        end_time = time.time()
        _log.info(
            f"Concurrent calls completed in {end_time - start_time:.2f} seconds: {response}"
        )

        # Verify all calls succeeded
        assert len(response) == 5, f"Expected 5 responses, got {len(response)}"
        for i, item in enumerate(test_values):
            expected = f"Agent B slow result: {item}"
            assert (
                response[i] == expected
            ), f"Item {i} mismatch: expected {expected}, got {response[i]}"

        # A serialised implementation takes the sum of the individual delays;
        # a concurrent one takes well under that. There is no lower bound
        # here: a lower bound close to the serial sum is exactly the
        # assumption that made this test pass for a serialised loop.
        serial_sum = 0.5 * len(test_values)
        assert end_time - start_time < serial_sum * 0.6, (
            f"Concurrent calls took {end_time - start_time:.2f}s, not well "
            f"under the serial sum of {serial_sum:.2f}s"
        )

        # Overlap is the direct evidence of concurrency: a serialised loop
        # produces windows that never overlap, whatever the total time is.
        assert len(call_windows) == len(test_values), (
            f"Expected {len(test_values)} call windows, got {len(call_windows)}"
        )
        overlapping = any(
            a_start < b_end and b_start < a_end
            for i, (a_start, a_end) in enumerate(call_windows)
            for j, (b_start, b_end) in enumerate(call_windows)
            if i != j
        )
        assert overlapping, f"No overlapping call windows: {call_windows}"

        _log.info(
            f"SUCCESS: {len(test_values)} concurrent RPC calls completed efficiently!"
        )
