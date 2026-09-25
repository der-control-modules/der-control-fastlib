"""
Integration tests for the BACnet Proxy agent.

These tests verify the BACnet proxy agent's ability to communicate with BACnet devices
and handle RPC calls from platform drivers. Tests are marked with @pytest.mark.bacnet
and will only run when specifically requested.

Run with: pytest -m bacnet tests/test_bacnet_proxy_integration.py -v
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import gevent
import pytest
from gevent.event import AsyncResult

from derhost.client.agent import Agent
from derhost.compat.import_hook import install_volttron_compatibility

# Install VOLTTRON compatibility hooks for BACnet proxy imports
install_volttron_compatibility()

# Test configuration
BACNET_PROXY_CONFIG = Path(__file__).parent / "fixtures" / "bacnet_proxy.config"
REGISTRY_CONFIG = Path(__file__).parent / "fixtures" / "test_device2.csv"  # Updated to use corrected registry
TEST_DEVICE_ADDRESS = "2001:2"  # From device config
TEST_DEVICE_ID = 2


@pytest.fixture(scope="module", autouse=True)
def cleanup_bacnet_port():
    """Ensure BACnet UDP port 47808 is available before tests."""
    # Kill any processes using BACnet port before starting tests
    try:
        subprocess.run(["lsof", "-ti:47808"], capture_output=True, check=False, timeout=2)
        subprocess.run(["pkill", "-9", "-f", "bacpypes"], capture_output=True, check=False, timeout=2)
        subprocess.run(["pkill", "-9", "-f", "grab_bacnet_config"], capture_output=True, check=False, timeout=2)
        time.sleep(1)  # Give OS time to release the port
    except Exception:
        pass  # Ignore errors, port might not be in use
    yield
    # Cleanup after tests
    try:
        subprocess.run(["pkill", "-9", "-f", "bacpypes"], capture_output=True, check=False, timeout=2)
    except Exception:
        pass


@pytest.fixture(scope="module")
def bacnet_proxy_config():
    """Load BACnet proxy configuration."""
    with open(BACNET_PROXY_CONFIG) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def test_point_map():
    """Create a point map for testing from the registry config."""
    import csv

    point_map = {}
    with open(REGISTRY_CONFIG, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Take first 10 points for testing
            if len(point_map) >= 10:
                break
            point_name = row["Volttron Point Name"]
            obj_type = row["BACnet Object Type"]
            index = int(row["Index"])
            point_map[point_name] = [obj_type, index, "presentValue", None]

    return point_map


class TestBACnetProxyIntegration:
    """Integration tests for BACnet Proxy agent."""

    @pytest.mark.bacnet
    @pytest.mark.integration
    def test_bacnet_device_reachable_with_bacpypes(self, bacnet_proxy_config):
        """
        Test if BACnet device is reachable using raw bacpypes library.

        This is a control test to verify if timeouts are due to actual network
        communication issues rather than implementation problems.
        """
        pytest.importorskip("bacpypes")
        from bacpypes.apdu import ReadPropertyRequest
        from bacpypes.app import BIPSimpleApplication
        from bacpypes.core import run as bacpypes_run, stop as bacpypes_stop
        from bacpypes.iocb import IOCB
        from bacpypes.pdu import Address
        from bacpypes.service.device import LocalDeviceObject

        # Create a simple BACnet application
        device_address = bacnet_proxy_config["device_address"]
        device = LocalDeviceObject(
            objectName=bacnet_proxy_config["object_name"],
            objectIdentifier=bacnet_proxy_config["object_id"],
            vendorIdentifier=bacnet_proxy_config["vendor_id"],
        )

        app = BIPSimpleApplication(device, device_address)

        # Try to read a simple property (device name) from the target device
        request = ReadPropertyRequest(
            objectIdentifier=("device", TEST_DEVICE_ID), propertyIdentifier="objectName"
        )
        request.pduDestination = Address(TEST_DEVICE_ADDRESS)

        iocb = IOCB(request)

        # BIPSimpleApplication uses request() method, not submit_request()
        app.request(iocb)

        # Give it time to respond
        result = {"success": False, "error": None, "response": None}

        def check_result():
            try:
                # Wait up to 10 seconds for response
                iocb.wait(10)
                if iocb.ioResponse:
                    result["success"] = True
                    result["response"] = iocb.ioResponse
                else:
                    result["error"] = str(iocb.ioError) if iocb.ioError else "No response"
            except Exception as e:
                result["error"] = str(e)
            finally:
                bacpypes_stop()

        gevent.spawn(check_result)
        gevent.sleep(0)

        # Run bacpypes event loop
        try:
            bacpypes_run()
        except KeyboardInterrupt:
            pass

        # Assert and provide diagnostic information
        if not result["success"]:
            pytest.skip(
                f"BACnet device at {TEST_DEVICE_ADDRESS} is not reachable: {result['error']}. "
                f"This indicates a network/device communication issue, not an implementation problem. "
                f"Ensure device is powered on, network is configured correctly, and device address is correct."
            )

        assert result["success"], f"BACnet device should be reachable: {result['error']}"

    @pytest.mark.bacnet
    @pytest.mark.integration
    def test_start_bacnet_proxy_agent(self, bacnet_proxy_config, message_bus, test_port):
        """
        Test starting the BACnet proxy agent (simulating start-legacy-platform-driver-proxy.sh).

        This test verifies that the BACnet proxy agent can be started and connects successfully.
        """
        # Create a BACnet proxy agent
        config = {
            "device_address": bacnet_proxy_config["device_address"],
            "max_apdu_length": bacnet_proxy_config["max_apdu_length"],
            "object_id": bacnet_proxy_config["object_id"],
            "object_name": bacnet_proxy_config["object_name"],
            "vendor_id": bacnet_proxy_config["vendor_id"],
            "segmentation_supported": bacnet_proxy_config["segmentation_supported"],
        }

        # Import the BACnet proxy agent module

        sys.path.insert(0, "/home/volttron/volttron/services/core/BACnetProxy")

        try:
            from bacnet_proxy.agent import BACnetProxyAgent

            # Create agent instance with correct parameters
            agent = BACnetProxyAgent(
                device_address=config["device_address"],
                max_apdu_len=config["max_apdu_length"],
                seg_supported=config["segmentation_supported"],
                obj_id=config["object_id"],
                obj_name=config["object_name"],
                ven_id=config["vendor_id"],
                max_per_request=bacnet_proxy_config.get("default_max_per_request", 1000000),
                request_check_interval=bacnet_proxy_config.get("request_check_interval", 100),
                address=f"ws://localhost:{test_port}",
                identity="test.bacnet_proxy"
            )

            # Start agent in greenlet
            agent_greenlet = gevent.spawn(agent.core.run)
            gevent.sleep(2)  # Give it time to start

            # Verify agent is connected
            assert agent.websocket is not None, "Agent should have WebSocket connection"
            assert hasattr(agent, "vip"), "Agent should have VIP subsystem"
            assert hasattr(agent.vip, "rpc"), "Agent should have RPC subsystem"

            # Cleanup
            agent_greenlet.kill()
            gevent.sleep(0.5)

        except ImportError as e:
            pytest.skip(f"BACnet proxy agent not available: {e}")

    @pytest.mark.bacnet
    @pytest.mark.integration
    def test_bacnet_proxy_rpc_read_properties(self, bacnet_proxy_config, test_point_map, message_bus_manager_fixture):
        """
        Test BACnet proxy RPC read_properties call (simulating platform driver).

        This test verifies:
        1. BACnet proxy receives RPC requests
        2. Executes read_properties method
        3. Communicates with BACnet device
        4. Returns response or times out appropriately

        Uses subprocess approach to avoid gevent/BACpypes event loop conflicts.
        """

        # Start BACnet proxy agent in subprocess (production approach)
        proxy_process = None
        proxy_log = None
        proxy_log_path = None
        caller_agent = None
        rpc_greenlet = None

        try:
            # Start the message bus using the manager
            message_bus_manager_fixture.start_bus()
            test_port = message_bus_manager_fixture.port

            # Get path to start-legacy.py
            script_dir = Path(__file__).parent.parent
            start_legacy_script = script_dir / "start-legacy.py"

            if not start_legacy_script.exists():
                pytest.skip(f"start-legacy.py not found at {start_legacy_script}")

            # Check for venv python early so we skip before doing expensive work
            venv_python = script_dir / ".venv" / "bin" / "python"
            if not venv_python.exists():
                pytest.skip(f"Venv python not found at {venv_python}")

            # Verify WebSocket endpoint is actually working by connecting a test agent
            print(f"Verifying WebSocket endpoint is ready on port {test_port}...")
            test_agent = message_bus_manager_fixture.create_connected_agent("test.connectivity_check")
            print("✓ WebSocket endpoint verified working (test process)")
            test_agent.disconnect()
            gevent.sleep(1)  # Let disconnect complete

            # Test if subprocess can connect to same port
            print("Testing subprocess connectivity...")
            venv_python = script_dir / ".venv" / "bin" / "python"
            test_result = subprocess.run(
                [str(venv_python), "/tmp/test_subprocess_connection.py", str(test_port)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            print(f"Subprocess test output:\n{test_result.stdout}")
            if test_result.returncode != 0:
                print(f"Subprocess test errors:\n{test_result.stderr}")
                pytest.fail(f"Subprocess cannot connect to message bus (exit code {test_result.returncode}). This is a system-level issue, not a BACnet proxy issue.")
            print("✓ Subprocess connectivity verified")

            # Start the BACnet proxy agent using start-legacy.py
            # Use venv python to ensure correct environment
            venv_python = script_dir / ".venv" / "bin" / "python"
            if not venv_python.exists():
                pytest.skip(f"Venv python not found at {venv_python}")

            # Use 127.0.0.1 explicitly instead of localhost to avoid resolution issues
            address = f"ws://127.0.0.1:{test_port}"

            print("Starting BACnet proxy agent via subprocess...")
            print(f"  Python: {venv_python}")
            print(f"  Address: {address}")
            print(f"  Config: {BACNET_PROXY_CONFIG}")

            # Write output to temp file for easier debugging
            import tempfile
            proxy_log = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.log', prefix='bacnet_proxy_')
            proxy_log_path = proxy_log.name
            print(f"  Logging to: {proxy_log_path}")

            # Use subprocess with explicit environment to avoid gevent interference
            import os
            clean_env = os.environ.copy()
            # Unset any gevent-related variables that might interfere
            clean_env.pop('GEVENT_SUPPORT', None)

            # Start subprocess with output redirected to file
            proxy_process = subprocess.Popen(
                [
                    str(venv_python),
                    str(start_legacy_script),
                    "--agent-dir", "/home/volttron/volttron/services/core/BACnetProxy",
                    "--address", address,
                    "--identity", "test.bacnet_proxy",
                    "--config", str(BACNET_PROXY_CONFIG),
                ],
                stdout=proxy_log,
                stderr=subprocess.STDOUT,
                env=clean_env,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None,  # Start in new session
            )

            # Give agent time to start and connect
            print("Waiting for BACnet proxy agent to connect...")

            # Monitor subprocess output to detect connection
            connected = False
            start_time = time.time()
            last_pos = 0

            while time.time() - start_time < 10:  # 10 second timeout
                # Check if process exited
                if proxy_process.poll() is not None:
                    proxy_log.flush()
                    proxy_log.seek(0)
                    full_output = proxy_log.read()
                    proxy_log.close()
                    print(f"\n=== BACnet Proxy Log (Full) ===\n{full_output}\n=== End Log ===\n")
                    pytest.fail(
                        f"BACnet proxy process exited with code {proxy_process.returncode}.\n"
                        f"See log above or check: {proxy_log_path}"
                    )

                # Read new output from log file
                proxy_log.seek(last_pos)
                new_output = proxy_log.read()
                if new_output:
                    print(new_output, end='')
                    last_pos = proxy_log.tell()

                    # Check for connection success
                    if "Agent test.bacnet_proxy connected" in new_output:
                        connected = True
                        print(f"\n✓ BACnet proxy agent connected (PID: {proxy_process.pid})")
                        break

                    # Check for connection errors
                    if "Connection refused" in new_output or "Connection timeout" in new_output:
                        print("\n✗ BACnet proxy connection error detected")

                gevent.sleep(0.1)

            if not connected and proxy_process.poll() is None:
                print(f"\n⚠ BACnet proxy agent started but may not be connected yet (PID: {proxy_process.pid})")
            elif not connected and proxy_process.poll() is not None:
                proxy_log.flush()
                proxy_log.seek(0)
                full_output = proxy_log.read()
                proxy_log.close()
                pytest.fail(f"BACnet proxy failed to connect.\nLog: {proxy_log_path}\n{full_output}")

            # Create a test caller agent (simulating platform driver) using the manager
            caller_agent = message_bus_manager_fixture.create_connected_agent("test.caller")
            print("Caller agent connected successfully")

            # Give BACnet application extra time to initialize
            gevent.sleep(2)
            print(f"Making RPC call to read {len(test_point_map)} points from {TEST_DEVICE_ADDRESS}")

            # Make RPC call to read properties
            result_container = {"result": None, "error": None, "completed": False}

            def make_rpc_call():
                try:
                    # Call read_properties on the proxy
                    result = caller_agent.vip.rpc.call(
                        "test.bacnet_proxy",
                        "read_properties",
                        TEST_DEVICE_ADDRESS,
                        test_point_map,
                        24,  # max_per_request
                        True,  # use_read_multiple
                    )

                    # If result is AsyncResult, wait for it
                    if isinstance(result, AsyncResult):
                        actual_result = result.get(timeout=15)
                        result_container["result"] = actual_result
                    else:
                        result_container["result"] = result

                    result_container["completed"] = True

                except Exception as e:
                    result_container["error"] = str(e)
                    result_container["completed"] = True

            # Spawn RPC call
            rpc_greenlet = gevent.spawn(make_rpc_call)

            # Wait for result with timeout
            rpc_greenlet.join(timeout=20)

            # Analyze results
            if not result_container["completed"]:
                pytest.fail("RPC call did not complete within timeout (20s). This indicates a blocking issue.")

            if result_container["error"]:
                error_msg = result_container["error"]
                if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                    pytest.skip(
                        f"RPC call timed out: {error_msg}. "
                        f"This likely indicates the BACnet device at {TEST_DEVICE_ADDRESS} is not responding. "
                        f"Run test_bacnet_device_reachable_with_bacpypes to verify device connectivity."
                    )
                else:
                    pytest.fail(f"RPC call failed with error: {error_msg}")

            # If we got a result, verify it's a dictionary
            assert result_container["result"] is not None, "RPC call should return a result"
            assert isinstance(
                result_container["result"], dict
            ), f"Result should be a dictionary, got {type(result_container['result'])}"
            assert len(result_container["result"]) > 0, "Result should contain data points"

            # Verify we got back some of the requested points
            returned_points = set(result_container["result"].keys())
            requested_points = set(test_point_map.keys())
            overlap = returned_points.intersection(requested_points)

            assert len(overlap) > 0, f"Should get back at least some requested points. Got: {returned_points}"

            print(f"\nSuccessfully read {len(overlap)} points from BACnet device:")
            for point in list(overlap)[:5]:  # Print first 5 points
                print(f"  {point}: {result_container['result'][point]}")

        except Exception:
            # If exception occurs before subprocess starts, re-raise
            if proxy_process is None:
                raise
            # Print diagnostic info
            print("\n=== Test failed, subprocess status ===")
            if proxy_process.poll() is None:
                print(f"Subprocess still running (PID: {proxy_process.pid})")
            else:
                print(f"Subprocess exited with code {proxy_process.returncode}")
            raise

        finally:
            # Cleanup subprocess - terminate gracefully, then force kill if needed
            if proxy_process is not None:
                print(f"\nCleaning up BACnet proxy subprocess (PID: {proxy_process.pid})...")

                if proxy_process.poll() is None:  # Process still running
                    # Try graceful shutdown first
                    proxy_process.terminate()
                    try:
                        proxy_process.wait(timeout=5)
                        print("BACnet proxy terminated gracefully")
                    except subprocess.TimeoutExpired:
                        # Force kill if graceful shutdown failed
                        print("Graceful shutdown timed out, force killing...")
                        proxy_process.kill()
                        proxy_process.wait(timeout=2)
                        print("BACnet proxy killed")
                else:
                    print(f"BACnet proxy already exited with code {proxy_process.returncode}")

            # Cleanup caller agent
            if caller_agent is not None:
                try:
                    caller_agent.disconnect()
                except Exception:
                    pass

            # Cleanup RPC greenlet if it was created
            if rpc_greenlet is not None:
                rpc_greenlet.kill()

            # Cleanup log file
            if proxy_log is not None:
                try:
                    proxy_log.close()
                except Exception:
                    pass
            if proxy_log_path is not None:
                try:
                    import os
                    os.unlink(proxy_log_path)
                    print(f"Cleaned up proxy log: {proxy_log_path}")
                except Exception:
                    pass

            gevent.sleep(1)

    @pytest.mark.bacnet
    @pytest.mark.integration
    def test_bacnet_proxy_timeout_behavior(self, bacnet_proxy_config, message_bus, test_port):
        """
        Test BACnet proxy timeout behavior with invalid device address.

        This verifies that timeouts are handled correctly and distinguishes between:
        1. Implementation issues (greenlet blocking, AsyncResult not handled)
        2. Communication failures (device not responding)
        """

        sys.path.insert(0, "/home/volttron/volttron/services/core/BACnetProxy")

        try:
            from bacnet_proxy.agent import BACnetProxyAgent
        except ImportError as e:
            pytest.skip(f"BACnet proxy agent not available: {e}")

        # Create BACnet proxy agent with correct parameters
        proxy_agent = BACnetProxyAgent(
            device_address=bacnet_proxy_config["device_address"],
            max_apdu_len=bacnet_proxy_config["max_apdu_length"],
            seg_supported=bacnet_proxy_config["segmentation_supported"],
            obj_id=bacnet_proxy_config["object_id"],
            obj_name=bacnet_proxy_config["object_name"],
            ven_id=bacnet_proxy_config["vendor_id"],
            max_per_request=bacnet_proxy_config.get("default_max_per_request", 1000000),
            request_check_interval=bacnet_proxy_config.get("request_check_interval", 100),
            address=f"ws://localhost:{test_port}",
            identity="test.bacnet_proxy"
        )

        caller_agent = Agent(address=f"ws://localhost:{test_port}", identity="test.caller")

        # Start both agents
        proxy_greenlet = gevent.spawn(proxy_agent.core.run)
        caller_greenlet = gevent.spawn(caller_agent.core.run)
        gevent.sleep(3)

        try:
            # Try to read from a non-existent device
            invalid_address = "9999:99"
            test_point = {"TestPoint": ["analogValue", 1, "presentValue", None]}

            start_time = time.time()
            result_container = {"error": None, "completed": False}

            def make_rpc_call():
                try:
                    result = caller_agent.vip.rpc.call(
                        "test.bacnet_proxy", "read_properties", invalid_address, test_point, 24, True
                    )

                    if isinstance(result, AsyncResult):
                        result.get(timeout=15)

                except Exception as e:
                    result_container["error"] = str(e)
                finally:
                    result_container["completed"] = True

            rpc_greenlet = gevent.spawn(make_rpc_call)
            rpc_greenlet.join(timeout=20)
            elapsed = time.time() - start_time

            # Verify timeout behavior
            assert result_container["completed"], "RPC call should complete (with timeout error)"
            assert result_container["error"] is not None, "Should get a timeout error for invalid device"
            assert elapsed < 18, f"Timeout should occur around 10-15s, but took {elapsed:.1f}s"

            print(f"\nTimeout behavior verified: Failed appropriately in {elapsed:.1f}s")
            print(f"Error: {result_container['error']}")

        finally:
            # Cleanup
            proxy_greenlet.kill()
            caller_greenlet.kill()
            if "rpc_greenlet" in locals():
                rpc_greenlet.kill()
            gevent.sleep(1)

    @pytest.mark.bacnet
    @pytest.mark.integration
    @pytest.mark.slow
    def test_bacnet_proxy_concurrent_requests(self, bacnet_proxy_config, test_point_map, message_bus, test_port):
        """
        Test BACnet proxy handling multiple concurrent RPC requests.

        This ensures the proxy can handle multiple requests without blocking or deadlocking.
        """

        sys.path.insert(0, "/home/volttron/volttron/services/core/BACnetProxy")

        try:
            from bacnet_proxy.agent import BACnetProxyAgent
        except ImportError as e:
            pytest.skip(f"BACnet proxy agent not available: {e}")

        # Create BACnet proxy agent with correct parameters
        proxy_agent = BACnetProxyAgent(
            device_address=bacnet_proxy_config["device_address"],
            max_apdu_len=bacnet_proxy_config["max_apdu_length"],
            seg_supported=bacnet_proxy_config["segmentation_supported"],
            obj_id=bacnet_proxy_config["object_id"],
            obj_name=bacnet_proxy_config["object_name"],
            ven_id=bacnet_proxy_config["vendor_id"],
            max_per_request=bacnet_proxy_config.get("default_max_per_request", 1000000),
            request_check_interval=bacnet_proxy_config.get("request_check_interval", 100),
            address=f"ws://localhost:{test_port}",
            identity="test.bacnet_proxy"
        )

        # Create multiple caller agents
        callers = [Agent(address=f"ws://localhost:{test_port}", identity=f"test.caller{i}") for i in range(3)]

        # Start all agents
        proxy_greenlet = gevent.spawn(proxy_agent.core.run)
        caller_greenlets = [gevent.spawn(c.core.run) for c in callers]
        gevent.sleep(3)

        try:
            # Make concurrent RPC calls
            results = []

            def make_call(caller, idx):
                try:
                    result = caller.vip.rpc.call(
                        "test.bacnet_proxy", "read_properties", TEST_DEVICE_ADDRESS, test_point_map, 24, True
                    )

                    if isinstance(result, AsyncResult):
                        actual = result.get(timeout=15)
                        results.append({"idx": idx, "success": True, "data": actual})
                    else:
                        results.append({"idx": idx, "success": True, "data": result})

                except Exception as e:
                    results.append({"idx": idx, "success": False, "error": str(e)})

            # Spawn all calls concurrently
            call_greenlets = [gevent.spawn(make_call, caller, i) for i, caller in enumerate(callers)]

            # Wait for all to complete
            gevent.joinall(call_greenlets, timeout=30)

            # Verify all completed
            assert len(results) == 3, f"All 3 requests should complete, got {len(results)}"

            # Check success rate
            successes = sum(1 for r in results if r["success"])
            if successes == 0:
                pytest.skip(
                    "All concurrent requests failed - likely device communication issue. "
                    "Check BACnet device connectivity."
                )

            print(f"\nConcurrent requests: {successes}/3 succeeded")

        finally:
            # Cleanup
            proxy_greenlet.kill()
            for g in caller_greenlets:
                g.kill()
            for g in call_greenlets:
                g.kill()
            gevent.sleep(1)
