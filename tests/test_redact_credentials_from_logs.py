"""
Tests for der-control-modules/der-control-fastlib#36: no log record, at any
level, from any module under derhost, carries a credential value from the
/gs RPC path or the agent's receive path.
"""

import logging

import gevent
import httpx
import pytest

MARKER = "s3cr3t-marker"


def is_derhost_record(record: logging.LogRecord) -> bool:
    """True for a record from the derhost package or any of its submodules."""
    return record.name == "derhost" or record.name.startswith("derhost.")


def assert_marker_absent(records, marker: str) -> None:
    """Fail if any caplog record's rendered message contains marker."""
    for record in records:
        assert marker not in record.getMessage()


class TestControlFires:
    """Proves assert_marker_absent can fail before it is trusted to pass."""

    def test_control_fires_on_a_record_with_the_marker(self, caplog):
        logger = logging.getLogger("derhost.test-control")
        with caplog.at_level(logging.DEBUG):
            logger.debug("leaked authentication=%s", MARKER)
        with pytest.raises(AssertionError):
            assert_marker_absent(caplog.records, MARKER)


class TestRedactCredentialsFromLogs:
    """A /gs call carrying authentication is never echoed into a log record,
    on the server's outbound RPC log or on the connected agent's receive
    path, while the method name is still logged."""

    @pytest.fixture(autouse=True)
    def setup_agent(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        self.agent = self.manager.create_connected_agent("gs_redact_target")

        def echo_method(**kwargs):
            return "ok"

        self.agent.vip.rpc.export_method("echo_target_method", echo_method)
        gevent.sleep(1)

        yield

        self.agent.disconnect()

    def test_gs_call_with_authentication_leaves_no_marker_in_any_log_record(self, caplog):
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_target",
            "method": "echo_target_method",
            "params": {"authentication": MARKER, "kwargs": {}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
            gevent.sleep(1)  # let the agent's receive-path logging run

        assert response.status_code == 200

        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)

        method_named = any("echo_target_method" in r.getMessage() for r in derhost_records)
        assert method_named, "expected at least one RPC log record to still name the method"
