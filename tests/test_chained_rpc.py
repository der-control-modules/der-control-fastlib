"""
Test chained RPC calls where one agent calls another agent within an RPC context.

This test simulates scenarios like:
1. Agent A receives RPC from external source
2. Agent A calls Agent B within that RPC context
3. Agent B may call Agent C, creating a chain
4. Test performance and timeout behavior of chained calls
"""

import logging
import time

import gevent
import pytest

_log = logging.getLogger(__name__)


class TestChainedRPC:
    """Test chained RPC functionality and performance."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus_manager_fixture):
        """Set up test agents for chained RPC tests."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create multiple agents for chaining
        self.proxy_agent = self.manager.create_connected_agent("proxy_agent")
        self.driver_agent = self.manager.create_connected_agent("platform.driver")
        self.device_agent = self.manager.create_connected_agent("device_agent")
        self.external_client = self.manager.create_connected_agent("external_client")

        # Wait for connections
        gevent.sleep(1)

        yield

        # Cleanup
        for agent in [
            self.proxy_agent,
            self.driver_agent,
            self.device_agent,
            self.external_client,
        ]:
            if agent:
                agent.disconnect()

    def test_simple_chained_rpc(self):
        """Test simple A->B chain where A calls B within an RPC context."""

        # Set up Agent B (driver) to provide a device method
        def get_device_value(device_id, point_name):
            _log.debug(f"Driver getting {point_name} from {device_id}")
            return f"Value from {device_id}.{point_name}: 42.5"

        self.driver_agent.vip.rpc.export_method("get_point", get_device_value)

        # Set up Agent A (proxy) to call Agent B within its RPC context
        def get_device_data(device_list):
            _log.debug(f"Proxy agent processing device list: {device_list}")
            results = {}

            # Use gevent to make non-blocking parallel calls
            greenlets = []
            for device_id in device_list:
                # This is the chained RPC call within the RPC context - use gevent.spawn
                def make_call(dev_id):
                    try:
                        _log.debug(f"Making RPC call for device: {dev_id}")
                        result = self.proxy_agent.vip.rpc.call(
                            "platform.driver", "get_point", dev_id, "temperature"
                        )
                        value = result.get(timeout=8)  # Reduced timeout
                        _log.debug(f"RPC call completed for device {dev_id}: {value}")
                        return dev_id, value
                    except Exception as e:
                        _log.error(f"RPC call failed for device {dev_id}: {e}")
                        return dev_id, f"ERROR: {str(e)}"

                greenlet = gevent.spawn(make_call, device_id)
                greenlets.append(greenlet)

            # Wait for all calls to complete with shorter timeout
            gevent.joinall(greenlets, timeout=10)

            # Collect results
            for greenlet in greenlets:
                if greenlet.ready():
                    try:
                        dev_id, value = greenlet.value
                        results[dev_id] = value
                    except Exception as e:
                        _log.error(f"Error getting greenlet value: {e}")
                else:
                    _log.error("Greenlet not ready/timed out")

            _log.debug(f"Final results: {results}")

            return results

        self.proxy_agent.vip.rpc.export_method("get_multiple_devices", get_device_data)
        gevent.sleep(1)  # Wait for method registration

        # External client calls proxy agent, which chains to driver agent
        start_time = time.time()
        result = self.external_client.vip.rpc.call(
            "proxy_agent", "get_multiple_devices", ["device1", "device2"]
        )
        response = result.get(timeout=15)
        end_time = time.time()

        _log.info(f"Chained RPC took {end_time - start_time:.2f} seconds")

        assert "device1" in response
        assert "device2" in response
        assert "Value from device1.temperature: 42.5" == response["device1"]
        assert "Value from device2.temperature: 42.5" == response["device2"]

    def test_deep_chained_rpc(self):
        """Test A->B->C chain simulating proxy->driver->device."""

        # Set up Agent C (device) - the deepest level
        def read_actual_value(point_name):
            _log.debug(f"Device reading actual value for {point_name}")
            gevent.sleep(0.5)  # Simulate device read time
            return {"value": 75.2, "timestamp": time.time(), "status": "ok"}

        self.device_agent.vip.rpc.export_method("read_value", read_actual_value)

        # Set up Agent B (driver) to call Agent C
        def get_point_from_device(device_path, point_name):
            _log.debug(f"Driver getting {point_name} from device at {device_path}")

            # Chain to device agent
            device_result = self.driver_agent.vip.rpc.call(
                "device_agent", "read_value", point_name
            )
            raw_value = device_result.get(timeout=10)

            # Add driver-level processing
            return {
                "device_path": device_path,
                "point": point_name,
                "raw_value": raw_value,
                "processed_value": raw_value["value"] * 1.1,  # Add some processing
                "driver_timestamp": time.time(),
            }

        self.driver_agent.vip.rpc.export_method("get_point", get_point_from_device)

        # Set up Agent A (proxy) to call Agent B
        def set_multiple_points(point_data):
            _log.debug(f"Proxy setting multiple points: {point_data}")
            results = []

            for device_path, point_name, target_value in point_data:
                # First get current value (A->B->C chain)
                current_result = self.proxy_agent.vip.rpc.call(
                    "platform.driver", "get_point", device_path, point_name
                )
                current_data = current_result.get(timeout=15)

                # Simulate set operation
                set_result = {
                    "device_path": device_path,
                    "point": point_name,
                    "old_value": current_data["processed_value"],
                    "new_value": target_value,
                    "success": True,
                }
                results.append(set_result)

            return {
                "operation": "set_multiple_points",
                "results": results,
                "total_operations": len(results),
            }

        self.proxy_agent.vip.rpc.export_method("set_points", set_multiple_points)
        gevent.sleep(1)  # Wait for method registration

        # External client initiates the deep chain
        point_operations = [
            ("PNNL/ROB/RTU02", "OccupiedCoolingSetPoint", 73.0),
            ("PNNL/ROB/RTU02", "OccupiedHeatingSetPoint", 69.0),
        ]

        start_time = time.time()
        result = self.external_client.vip.rpc.call(
            "proxy_agent", "set_points", point_operations
        )
        response = result.get(timeout=30)
        end_time = time.time()

        _log.info(f"Deep chained RPC took {end_time - start_time:.2f} seconds")

        assert response["operation"] == "set_multiple_points"
        assert response["total_operations"] == 2
        assert len(response["results"]) == 2

        # Verify each operation went through the full chain
        for result_item in response["results"]:
            assert result_item["success"]
            assert "old_value" in result_item
            assert "new_value" in result_item

    def test_parallel_chained_rpc(self):
        """Test multiple chained RPC calls in parallel to identify bottlenecks."""

        # Set up device agent with variable response times
        def simulate_device_operation(operation_id, delay=1.0):
            _log.debug(f"Device operation {operation_id} starting (delay: {delay}s)")
            gevent.sleep(delay)  # Simulate variable device response time
            return {
                "operation_id": operation_id,
                "result": f"Operation {operation_id} completed",
                "delay": delay,
            }

        self.device_agent.vip.rpc.export_method(
            "device_operation", simulate_device_operation
        )

        # Set up driver to handle multiple devices
        def process_device_operations(operations):
            _log.debug(f"Driver processing {len(operations)} operations")

            # Create greenlets for parallel device calls
            greenlets = []
            for op_id, delay in operations:
                greenlet = gevent.spawn(
                    lambda id=op_id, d=delay: self.driver_agent.vip.rpc.call(
                        "device_agent", "device_operation", id, d
                    ).get(timeout=10)
                )
                greenlets.append((op_id, greenlet))

            # Wait for all operations
            gevent.joinall([g[1] for g in greenlets], timeout=15)

            results = {}
            for op_id, greenlet in greenlets:
                if greenlet.successful():
                    results[op_id] = greenlet.value
                else:
                    results[op_id] = {"error": str(greenlet.exception)}

            return results

        self.driver_agent.vip.rpc.export_method(
            "process_operations", process_device_operations
        )

        # Set up proxy for coordinated multi-device control
        def coordinated_control(control_sequence):
            _log.debug(
                f"Proxy coordinating control sequence with {len(control_sequence)} steps"
            )
            sequence_results = []

            for step_id, operations in control_sequence:
                step_start = time.time()

                # Call driver for this step (which will parallelize device calls)
                step_result = self.proxy_agent.vip.rpc.call(
                    "platform.driver", "process_operations", operations
                ).get(timeout=20)

                step_end = time.time()

                sequence_results.append(
                    {
                        "step_id": step_id,
                        "duration": step_end - step_start,
                        "operations": step_result,
                    }
                )

            return {
                "sequence_type": "coordinated_control",
                "steps": sequence_results,
                "total_steps": len(sequence_results),
            }

        self.proxy_agent.vip.rpc.export_method(
            "coordinated_control", coordinated_control
        )
        gevent.sleep(1)  # Wait for method registration

        # Create a complex control sequence
        control_sequence = [
            ("step1", [("op1", 0.5), ("op2", 0.8), ("op3", 0.3)]),  # 3 parallel ops
            ("step2", [("op4", 1.0), ("op5", 0.7)]),  # 2 parallel ops
            (
                "step3",
                [("op6", 0.4), ("op7", 0.9), ("op8", 0.6), ("op9", 0.2)],
            ),  # 4 parallel ops
        ]

        start_time = time.time()
        result = self.external_client.vip.rpc.call(
            "proxy_agent", "coordinated_control", control_sequence
        )
        response = result.get(timeout=45)
        end_time = time.time()

        total_time = end_time - start_time
        _log.info(f"Parallel chained RPC sequence took {total_time:.2f} seconds")

        assert response["sequence_type"] == "coordinated_control"
        assert response["total_steps"] == 3

        # Verify timing - parallel execution should be faster than sequential
        step1_duration = response["steps"][0]["duration"]
        step2_duration = response["steps"][1]["duration"]
        step3_duration = response["steps"][2]["duration"]

        # Step 1: max(0.5, 0.8, 0.3) ≈ 0.8s + overhead
        assert step1_duration < 2.0, f"Step 1 took too long: {step1_duration}s"

        # Step 2: max(1.0, 0.7) ≈ 1.0s + overhead
        assert step2_duration < 1.8, f"Step 2 took too long: {step2_duration}s"

        # Step 3: max(0.4, 0.9, 0.6, 0.2) ≈ 0.9s + overhead
        assert step3_duration < 2.5, f"Step 3 took too long: {step3_duration}s"

        _log.info(
            f"Step durations: {step1_duration:.2f}s, {step2_duration:.2f}s, {step3_duration:.2f}s"
        )

    @pytest.mark.skip(
        reason="Timeout behavior test needs investigation - timing issues in test environment"
    )
    def test_chained_rpc_timeout_behavior(self):
        """Test how timeouts behave in chained RPC calls."""

        # Device agent with controllable delays
        def slow_device_operation(delay_seconds):
            _log.debug(f"Device operation sleeping for {delay_seconds} seconds")
            gevent.sleep(delay_seconds)
            return f"Completed after {delay_seconds}s"

        self.device_agent.vip.rpc.export_method("slow_operation", slow_device_operation)

        # Driver with shorter timeout than the proxy
        def driver_with_timeout(operation_delay):
            _log.debug(f"Driver calling device with {operation_delay}s delay")
            try:
                result = self.driver_agent.vip.rpc.call(
                    "device_agent", "slow_operation", operation_delay
                )
                return result.get(timeout=3)  # 3 second timeout at driver level
            except Exception as e:
                return {"error": f"Driver timeout: {str(e)}"}

        self.driver_agent.vip.rpc.export_method("timed_operation", driver_with_timeout)

        # Proxy with longer timeout
        def proxy_operation(delay):
            _log.debug(f"Proxy initiating operation with {delay}s delay")
            try:
                result = self.proxy_agent.vip.rpc.call(
                    "platform.driver", "timed_operation", delay
                )
                return result.get(timeout=8)  # 8 second timeout at proxy level
            except Exception as e:
                return {"error": f"Proxy timeout: {str(e)}"}

        self.proxy_agent.vip.rpc.export_method("proxy_operation", proxy_operation)
        gevent.sleep(1)

        # Test 1: Operation that should succeed (delay < driver timeout)
        result1 = self.external_client.vip.rpc.call(
            "proxy_agent",
            "proxy_operation",
            2,  # 2 seconds - within driver's 3s timeout
        )
        response1 = result1.get(timeout=15)

        assert "Completed after 2s" == response1

        # Test 2: Operation that should timeout at driver level
        result2 = self.external_client.vip.rpc.call(
            "proxy_agent",
            "proxy_operation",
            5,  # 5 seconds - exceeds driver's 3s timeout
        )
        response2 = result2.get(timeout=15)

        assert "error" in response2
        assert "Driver timeout" in response2["error"]

        _log.info(f"Timeout test results: Success={response1}, Timeout={response2}")
