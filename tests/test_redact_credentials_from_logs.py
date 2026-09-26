"""
Tests for der-control-modules/der-control-fastlib#36: no log record, at any
level, from any module under derhost, carries a credential value from the
/gs RPC path or the agent's receive path.
"""

import json
import logging
import time

import gevent
import httpx
import pytest

MARKER = "s3cr3t-marker"
# The marker's own prefix: a partial leak (a value truncated mid-marker)
# must fail these tests too, not only a leak of the whole string.
MARKER_PREFIX = MARKER[:6]


def is_derhost_record(record: logging.LogRecord) -> bool:
    """True for a record from the derhost package or any of its submodules."""
    return record.name == "derhost" or record.name.startswith("derhost.")


def assert_marker_absent(records, marker: str) -> None:
    """Fail if any caplog record's rendered message contains marker's prefix.

    Checking the prefix, not the whole marker, means a truncated partial
    leak (the marker cut in half by a length limit) fails this check too.
    """
    prefix = marker[:6]
    for record in records:
        assert prefix not in record.getMessage()


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

        def echo_args_method(*args, **kwargs):
            return "ok"

        def int_key_result_method(**kwargs):
            return {1: "one"}

        self.agent.vip.rpc.export_method("echo_target_method", echo_method)
        # A one-character name: the WS receive log truncates at SIZE_OUTPUT
        # (100) characters, and a realistic method name pushes a short
        # secret value past that cutoff before it can be checked.
        self.agent.vip.rpc.export_method("e", echo_method)
        self.agent.vip.rpc.export_method("echo_args_target_method", echo_args_method)
        self.agent.vip.rpc.export_method("int_key_result_method", int_key_result_method)
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

    def test_gs_call_with_dict_authentication_leaves_no_marker_in_any_log_record(self, caplog):
        """Item 1: a dict-valued authentication field leaks on the WS
        receive log's text-based redaction path, not only a string value."""
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_target",
            "method": "e",
            "params": {"authentication": {"v": MARKER}, "kwargs": {}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
            gevent.sleep(1)

        assert response.status_code == 200

        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)

    def test_call_and_handle_request_logs_redact_args(self, caplog):
        """Item 4: RPC args are redacted in logs the same way kwargs are, on
        both the outbound call log and the inbound dispatch log."""
        with caplog.at_level(logging.DEBUG):
            # Outbound: RPC.call's own "sent RPC call" log (agent.py, the
            # method= / args= / kwargs= debug line).
            self.agent.vip.rpc.call("nonexistent_peer_xyz", "some_remote_method", {"token": MARKER})

            # Inbound: handle_request's "RPC CALL" log for a locally exported
            # method, called directly so it does not depend on the outbound
            # call above reaching a peer.
            self.agent.vip.rpc.handle_request(
                sender="test_sender",
                method_name="echo_args_target_method",
                args=[{"token": MARKER}],
                kwargs={},
                msg_id="handle-request-args-test",
            )
            gevent.sleep(0.5)

        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)

    def test_gs_call_with_non_string_result_keys_succeeds(self):
        """Item 3: redaction must not raise on a non-string dict key, or a
        successful RPC whose result has int keys turns into an internal
        error instead of returning its result."""
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_target",
            "method": "int_key_result_method",
            "params": {"kwargs": {}},
        }

        response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
        gevent.sleep(1)

        assert response.status_code == 200
        data = response.json()
        assert data.get("error") is None, f"unexpected error: {data.get('error')}"
        assert data.get("result") == {"1": "one"}


class TestExceptionMessagesNeverLeakSecrets:
    """Item 2: no exception message this code builds, and no exception text
    it logs, carries a secret value."""

    @pytest.fixture(autouse=True)
    def setup_agent(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        self.agent = self.manager.create_connected_agent("gs_redact_exc_target")

        def raising_method(**kwargs):
            # Simulates a validator (e.g. pydantic) that echoes its rejected
            # input in its own exception text, the way ValidationError does.
            raise ValueError(f"boom {kwargs.get('token')}")

        self.agent.vip.rpc.export_method("raising_target_method", raising_method)
        gevent.sleep(1)

        yield

        self.agent.disconnect()

    def test_non_serializable_secret_kwarg_does_not_leak(self, caplog):
        """P1: RPC.call's own TypeError for a non-serializable kwarg must not
        embed the raw secret value in its message."""
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TypeError) as exc_info:
                self.agent.vip.rpc.call(
                    "nonexistent_peer_xyz", "some_method", authentication=MARKER.encode()
                )

        assert MARKER_PREFIX not in str(exc_info.value)
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert_marker_absent(derhost_records, MARKER)

    def test_rpc_method_exception_does_not_leak_secret_in_logs(self, caplog):
        """An exported method's own exception text (not built by this code
        from a fixed template) must not surface a secret in a log record."""
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_exc_target",
            "method": "raising_target_method",
            "params": {"kwargs": {"token": MARKER}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
            gevent.sleep(1)

        assert response.status_code == 200
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)


def test_redact_secrets_handles_non_string_keys_without_raising():
    """Item 3, unit level: a non-string dict key must not raise, and a
    sibling secret-named string key in the same dict must still redact."""
    from derhost._redact import redact_secrets

    result = redact_secrets({1: "one", "token": MARKER})

    assert result[1] == "one"
    assert result["token"] == "[REDACTED]"


def test_redact_secrets_matches_normalized_secret_key_variants():
    """Item 5: key matching is case- and -/_-insensitive and catches
    normalized names that contain a secret word, not only the exact field
    names in SECRET_LOG_FIELDS. "author" is a known false positive from the
    "auth" substring; over-redacting a log line is an acceptable trade for
    never missing an authorization/authentication spelling variant, and
    "primary_key" (a non-secret identifier) must NOT match."""
    from derhost._redact import redact_secrets

    payload = {
        "API-Key": MARKER,
        "access_key": MARKER,
        "private_key": MARKER,
        "Passwd": MARKER,
        "Cookie": MARKER,
        "credentials": MARKER,
        "author": "not-a-secret",
        "primary_key": "not-a-secret-id",
    }

    result = redact_secrets(payload)

    for key in ("API-Key", "access_key", "private_key", "Passwd", "Cookie", "credentials", "author"):
        assert result[key] == "[REDACTED]", f"expected {key} to be redacted"
    assert result["primary_key"] == "not-a-secret-id"


# Bound justified by measurement: at 9e7bdb2 (the restartable-regex scanner)
# the escaped-quote input took 17.9s and the nested-JSON-as-string input took
# 3.3s on this host; the linear scanner finishes both in well under 0.05s.
_SCAN_TIME_BOUND_SECONDS = 0.2


def test_redact_secrets_in_text_stays_linear_on_dense_escaped_quotes():
    """Item 1: a text-based scan must not restart itself at every escaped
    quote. 64 KB of nothing but escaped quotes has no field name to find and
    must still finish quickly, not scan forward from every one of them."""
    from derhost._redact import redact_secrets_in_text

    text = '\\"' * (64 * 1024 // 2)

    start = time.monotonic()
    result = redact_secrets_in_text(text)
    elapsed = time.monotonic() - start

    assert result == text  # no field name in this input, so nothing changes
    assert elapsed < _SCAN_TIME_BOUND_SECONDS, f"took {elapsed:.3f}s, expected under {_SCAN_TIME_BOUND_SECONDS}s"


def test_redact_secrets_in_text_stays_linear_on_json_nested_as_a_string():
    """Item 1: a JSON document carried as a string value (heavy on escaped
    quotes, from json.dumps re-encoding it) must also stay linear. A field
    name inside that escaped string is a known, pre-existing gap (it is not
    real unescaped JSON to this scanner), so this asserts only that a real
    top-level secret field is still found and redacted around the blob, not
    that the gap is closed."""
    from derhost._redact import redact_secrets_in_text

    inner = json.dumps({f"field{i}": f"value{i}" for i in range(2150)})
    text = json.dumps({"token": MARKER, "data": inner})
    assert 60_000 <= len(text) <= 68_000  # close to the 64 KB the item names

    start = time.monotonic()
    result = redact_secrets_in_text(text)
    elapsed = time.monotonic() - start

    assert MARKER not in result  # the real top-level "token" field is redacted
    assert elapsed < _SCAN_TIME_BOUND_SECONDS, f"took {elapsed:.3f}s, expected under {_SCAN_TIME_BOUND_SECONDS}s"


def test_redact_secrets_in_text_redacts_unterminated_value_to_the_end():
    """Keeps the existing fail-closed guarantee through the rewrite: a
    secret value with no closing quote is redacted to the end of the text,
    not left with an unredacted prefix."""
    from derhost._redact import redact_secrets_in_text

    text = f'"token": "{MARKER}'

    result = redact_secrets_in_text(text)

    assert MARKER_PREFIX not in result
    assert result == '"token": "[REDACTED]"'


def test_redact_known_secret_values_ignores_short_or_non_string_secrets():
    """Item 2: a short or non-string secret value must not be blanked, since
    the same text is the RPC error the caller receives, and blanking a
    common short word or literal would corrupt that message."""
    from derhost._redact import redact_known_secret_values

    text = "boom a token failed"
    assert redact_known_secret_values(text, {"token": "a"}) == text

    text = "auth is None here, retry True after 1 second"
    assert redact_known_secret_values(text, {"auth": None, "retry": True, "token": 1}) == text


def test_redact_known_secret_values_still_blanks_secrets_of_length_8_or_more():
    """Item 2 control: an 8-character-or-longer str or bytes secret is still
    blanked, so the length gate does not silently disable redaction. A bytes
    secret is matched in its str() form, same as the existing conversion this
    function already applies before searching the text."""
    from derhost._redact import redact_known_secret_values

    text = "boom eightlet token failed"
    assert redact_known_secret_values(text, {"token": "eightlet"}) == "boom [REDACTED] token failed"

    secret = b"eightlet"
    text = f"boom {secret!s} token failed"
    assert redact_known_secret_values(text, {"token": secret}) == "boom [REDACTED] token failed"


def test_auth_failure_diagnostic_fields_stay_visible():
    """Item 3: "authenticated"/"authorized" (and case variants) must not be
    treated as secret field names, so auth-failure diagnostics stay visible;
    "authorization" and "auth_token" are still redacted as a control."""
    from derhost._redact import is_secret_field

    assert is_secret_field("authenticated") is False
    assert is_secret_field("Authorized") is False
    assert is_secret_field("authorization") is True
    assert is_secret_field("auth_token") is True
