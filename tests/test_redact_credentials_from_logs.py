"""
Tests for der-control-modules/der-control-fastlib#36 PR 3: no log record, at
any level, from the two files this PR covers, carries a credential value from
the /gs RPC path or the agent's receive path.

connection_manager.py also logs the raw RPC message (send_message,
truncate_debug_message does not redact) and is a real, separate leak; it is
outside this PR's Allowed files and is reported, not fixed, here.
"""

import logging

import gevent
import httpx
import pytest

MARKER = "s3cr3t-marker"

# The two loggers this PR redacts. connection_manager.py deliberately excluded:
# see module docstring.
COVERED_LOGGERS = {"derhost.server.fastapi_message_bus", "derhost.client.agent"}


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

        covered_records = [r for r in caplog.records if r.name in COVERED_LOGGERS]
        assert covered_records, "expected at least one record from the covered loggers"
        assert_marker_absent(covered_records, MARKER)

        method_named = any("echo_target_method" in r.getMessage() for r in covered_records)
        assert method_named, "expected at least one RPC log record to still name the method"
