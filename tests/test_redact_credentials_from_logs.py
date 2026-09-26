"""
Tests for der-control-modules/der-control-fastlib#36: no log record, at any
level, from any module under derhost, carries a credential value from the
/gs RPC path or the agent's receive path.
"""

import copy
import dataclasses
import json
import logging
import random
import time

import gevent
import httpx
import pytest
from gevent.event import AsyncResult

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

        def raising_method_with_untracked_pattern(**kwargs):
            # The secret-named pattern here is not part of any payload the
            # RPC received, so value-blanking alone has nothing to blank:
            # only the text-based rule (item 2) catches it.
            raise ValueError(f"downstream auth failed, password: {MARKER}")

        def async_raising_method(**kwargs):
            result = AsyncResult()

            def fail_later():
                gevent.sleep(0.1)
                result.set_exception(ValueError(f"downstream auth failed, password: {MARKER}"))

            gevent.spawn(fail_later)
            return result

        self.agent.vip.rpc.export_method("raising_target_method", raising_method)
        self.agent.vip.rpc.export_method("raising_untracked_pattern_method", raising_method_with_untracked_pattern)
        self.agent.vip.rpc.export_method("async_raising_target_method", async_raising_method)
        gevent.sleep(1)

        yield

        self.agent.disconnect()

    def test_non_serializable_secret_kwarg_does_not_leak(self, caplog):
        """P1: RPC.call's own TypeError for a non-serializable kwarg must not
        embed the raw secret value in its message."""
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TypeError) as exc_info:
                self.agent.vip.rpc.call("nonexistent_peer_xyz", "some_method", authentication=MARKER.encode())

        assert MARKER_PREFIX not in str(exc_info.value)
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert_marker_absent(derhost_records, MARKER)

    def test_nested_secret_under_non_secret_kwarg_does_not_leak(self, caplog):
        """Item 3: a secret nested under a non-secret-named kwarg (e.g.
        payload={"credentials": {...}}) must be redacted by walking the
        value, not only by checking the kwarg's own name."""
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TypeError) as exc_info:
                self.agent.vip.rpc.call(
                    "nonexistent_peer_xyz",
                    "some_method",
                    payload={"credentials": {"token": MARKER}, "cache": {1, 2, 3}},
                )

        assert MARKER_PREFIX not in str(exc_info.value)
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert_marker_absent(derhost_records, MARKER)

    def test_dataclass_positional_arg_does_not_leak(self, caplog):
        """Item 4: a non-container positional arg (a dataclass with a
        password field) is stringified and text-redacted, not passed
        through unchanged."""

        @dataclasses.dataclass
        class _Creds:
            password: str

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TypeError) as exc_info:
                self.agent.vip.rpc.call("nonexistent_peer_xyz", "some_method", _Creds(password=MARKER))

        assert MARKER_PREFIX not in str(exc_info.value)
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert_marker_absent(derhost_records, MARKER)

    def test_bytes_positional_arg_does_not_leak(self, caplog):
        """Item 4: raw bytes as a positional arg (b'token=...') is
        stringified and text-redacted the same way."""
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TypeError) as exc_info:
                self.agent.vip.rpc.call("nonexistent_peer_xyz", "some_method", f"token={MARKER}".encode())

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

    def test_rpc_method_exception_with_untracked_pattern_is_text_redacted(self, caplog):
        """Item 2: exception text carrying a secret-named "name: value"
        pattern that is not part of the RPC's own payload (so value
        blanking has nothing to blank) must still be caught by the
        text-based rule, on both the local-dispatch log and the
        send-response log."""
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_exc_target",
            "method": "raising_untracked_pattern_method",
            "params": {"kwargs": {}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
            gevent.sleep(1)

        assert response.status_code == 200
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)

    def test_async_result_exception_text_is_text_redacted(self, caplog):
        """Item 2: an exported method that returns an AsyncResult, whose
        result later fails, must have its RPC ERROR log text-redacted too,
        not only value-blanked."""
        with caplog.at_level(logging.DEBUG):
            self.agent.vip.rpc.handle_request(
                sender="test_sender",
                method_name="async_raising_target_method",
                args=[],
                kwargs={},
                msg_id="async-error-redaction-test",
            )
            gevent.sleep(1)

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
    """Key matching is case- and separator-insensitive and catches
    normalized names that contain a secret word. "author" and "primary_key"
    are non-secret identifiers and must NOT match: bare "auth" and bare
    "key" are not substrings (see the names table test for the full field
    list this drives)."""
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

    for key in ("API-Key", "access_key", "private_key", "Passwd", "Cookie", "credentials"):
        assert result[key] == "[REDACTED]", f"expected {key} to be redacted"
    assert result["author"] == "not-a-secret"
    assert result["primary_key"] == "not-a-secret-id"


# Bound justified by measurement: a restartable regex scanner took 17.9s on
# the escaped-quote input and 3.3s on the nested-JSON-as-string input on this
# host; a linear scanner finishes each 256 KB shape well under this bound
# (measured worst shape 0.35s/MB).
_SCAN_TIME_BOUND_SECONDS = 0.5
_SHAPE_SIZE = 256 * 1024


def _dense_escaped_quotes(size: int) -> str:
    return '\\"' * (size // 2)


def _json_nested_as_string(size: int) -> str:
    inner = json.dumps({f"field{i}": f"value{i}" for i in range(size // 30)})
    return json.dumps({"token": MARKER, "data": inner})


def _deep_nesting_json(size: int) -> str:
    depth = min(size // 2, 2900)  # stay under the 3000-level RecursionError case
    return "[" * depth + "]" * depth


def _distinct_key_names(size: int) -> str:
    # The measured worst shape: many distinct "nX:" names, none secret.
    parts = []
    n = 0
    while sum(len(p) for p in parts) < size:
        parts.append(f'"n{n}": {n}, ')
        n += 1
    return "{" + "".join(parts) + '"token": "' + MARKER + '"}'


def _unbalanced_odd_quotes(size: int) -> str:
    return ('bad "input ' * (size // 12)) + '{"token": "' + MARKER + '"}'


def _plain_text_no_separators(size: int) -> str:
    return "no field names here, just prose. " * (size // 34)


def _large_list_of_secret_dicts(size: int) -> str:
    count = size // 40
    return json.dumps([{"token": MARKER, "n": i} for i in range(count)])


def _long_string_leaf_with_embedded_pairs(size: int) -> str:
    # A single string VALUE (not a real key) that itself looks like many
    # "name:" pairs, stressing the conservative rule applied to one leaf.
    return json.dumps({"note": ("field: notasecret, " * (size // 20))})


_TIME_BOUND_SHAPES = {
    "dense_escaped_quotes": _dense_escaped_quotes,
    "json_nested_as_string": _json_nested_as_string,
    "deep_nesting": _deep_nesting_json,
    "distinct_key_names": _distinct_key_names,
    "unbalanced_odd_quotes": _unbalanced_odd_quotes,
    "plain_text_no_separators": _plain_text_no_separators,
    "large_list_of_secret_dicts": _large_list_of_secret_dicts,
    "long_string_leaf_with_embedded_pairs": _long_string_leaf_with_embedded_pairs,
}


@pytest.mark.parametrize("shape_name", sorted(_TIME_BOUND_SHAPES))
def test_redact_text_stays_within_time_bound_on_adversarial_shapes(shape_name):
    """Every shape runs in linear time. A reviewer reverting to a
    restartable regex scanner on any of these should make this test fail,
    not just the two it was originally measured against."""
    from derhost._redact import redact_text

    text = _TIME_BOUND_SHAPES[shape_name](_SHAPE_SIZE)

    start = time.monotonic()
    redact_text(text)
    elapsed = time.monotonic() - start

    assert elapsed < _SCAN_TIME_BOUND_SECONDS, (
        f"{shape_name} took {elapsed:.3f}s, expected under {_SCAN_TIME_BOUND_SECONDS}s"
    )


def test_redact_text_redacts_unterminated_value_to_the_end():
    """Keeps the fail-closed guarantee through the rewrite: a secret value
    with no closing quote is redacted to the end of the text, not left with
    an unredacted prefix. redact_text does not try to find the value's own
    end (quote-pairing to find that end was an earlier bug); it drops
    everything after the separator."""
    from derhost._redact import redact_text

    text = f'"token": "{MARKER}'

    result = redact_text(text)

    assert MARKER_PREFIX not in result
    assert result == '"token": [REDACTED]'


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


# ---------------------------------------------------------------------------
# Test plan: field-name table, fuzz properties, never-raises and
# no-mutation guarantees, and the value-blanking forms, each with the
# control that shows it can fail.
# ---------------------------------------------------------------------------

_NAMES_TABLE = [
    # (name, expected is_secret_field result)
    ("auth", True),
    ("Authorization", True),
    ("AUTH-HEADER", True),
    ("key", True),
    ("pass", True),
    ("pwd", True),
    ("PIN", True),
    ("pincode", True),
    ("otp", True),
    ("salt", True),
    ("psk", True),
    ("session", True),
    ("session_id", True),
    ("session-key", True),
    ("dsn", True),
    ("db_url", True),
    ("database_url", True),
    ("connection_string", True),
    ("token", True),
    ("secret", True),
    ("password", True),
    ("passphrase", True),
    ("cookie", True),
    ("credential", True),
    ("api_key", True),
    ("access_key", True),
    ("private_key", True),
    ("encryption_key", True),
    ("signing_key", True),
    ("hmac_key", True),
    ("ssh_key", True),
    ("master_key", True),
    ("jwt", True),
    ("bearer", True),
    ("signature", True),
    ("auth_token", True),
    ("authorized_token", True),
    ("authenticated_token", True),
    # exemptions and fixed false positives
    ("authenticated", False),
    ("Authorized", False),
    ("author", False),
    ("authority", False),
    ("auth_method", False),
    ("token_count", False),
    ("max_tokens", False),
    ("min_token_length", False),
    ("num_tokens", False),
    ("credential_id", False),
    ("credential_type", False),
    ("password_expires_at", False),
    ("password_expiry", False),
    ("password_ttl", False),
    ("password_policy", False),
    ("password_required", False),
    ("password_enabled", False),
    # no bare "*key" suffix
    ("primary_key", False),
    ("foreign_key", False),
    ("serverkey", False),
    ("alert_key", False),
    # min/max/num prefix exemption applies to the first name segment only
    # (min_token_length above already covers the exempt case)
    ("minio_secret_key", True),
    ("minio_root_password", True),
    # pass, pwd, and passwd as secret suffixes
    ("db_pass", True),
    ("db_pwd", True),
    ("user_passwd", True),
]


@pytest.mark.parametrize("name,expected", _NAMES_TABLE)
def test_is_secret_field_names_table(name, expected):
    """The full field-name contract, both sides: every listed secret name
    is caught, and every listed exemption or fixed false positive is not."""
    from derhost._redact import is_secret_field

    assert is_secret_field(name) is expected


def test_redact_secrets_never_raises_on_pathological_nesting():
    """Deep nesting, a non-string key, and a tuple of dicts must not raise.
    The control (deep nesting alone) raises RecursionError against the old
    redact_secrets_in_text's caller-side json.loads/walk, which had no depth
    guard."""
    from derhost._redact import redact_secrets

    deep = current = {}
    for _ in range(3000):
        current["n"] = {}
        current = current["n"]
    result = redact_secrets(deep)
    assert result == "[REDACTED]"

    result = redact_secrets({1: "one", (2, 3): "two", "token": MARKER})
    assert result[1] == "one"
    assert result["token"] == "[REDACTED]"

    result = redact_secrets((({"token": MARKER},), [{"password": MARKER}]))
    assert MARKER not in str(result)


def test_redact_secrets_does_not_mutate_its_input():
    """redact_secrets is copy-on-read. The wire payload (and anything else
    the caller still holds after logging) must be unchanged."""
    from derhost._redact import redact_secrets

    payload = {"token": MARKER, "nested": {"password": MARKER, "method": "echo"}, "items": [1, 2, {"key": MARKER}]}
    before = copy.deepcopy(payload)

    redact_secrets(payload)

    assert payload == before


def _random_json_value(rng, depth=0):
    secret_keys = ("token", "password", "api_key", "credentials")
    plain_keys = ("method", "id", "count", "note", "name")
    if depth >= 3:
        kinds = ("str", "int", "bool", "none")
    else:
        kinds = ("str", "int", "bool", "none", "dict", "list")
    kind = rng.choice(kinds)
    if kind == "str":
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(6, 24)))
    if kind == "int":
        return rng.randint(-10000, 10000)
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "none":
        return None
    if kind == "dict":
        result = {}
        for _ in range(rng.randint(1, 3)):
            key = rng.choice(secret_keys if rng.random() < 0.4 else plain_keys)
            result[key] = _random_json_value(rng, depth + 1)
        return result
    return [_random_json_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]


def _secret_leaves(value, under_secret_key=False):
    if isinstance(value, dict):
        for k, v in value.items():
            is_secret = under_secret_key or k in ("token", "password", "api_key", "credentials")
            yield from _secret_leaves(v, is_secret)
    elif isinstance(value, list):
        for v in value:
            yield from _secret_leaves(v, under_secret_key)
    elif under_secret_key and isinstance(value, str) and len(value) >= 6:
        yield value


def test_fuzz_json_no_secret_prefix_survives_redact_text_or_redact_secrets():
    """Over 2000 seeded JSON values, no 6-character prefix of a value under
    a secret-named key survives redact_text(json.dumps(v)) or
    str(redact_secrets(v)). The identity-swap control (redaction skipped)
    must report leaks, proving the check can fail."""
    from derhost._redact import redact_secrets, redact_text

    rng = random.Random(20260925)
    leaks_with_redaction = 0
    leaks_with_identity = 0

    for _ in range(2000):
        value = _random_json_value(rng)
        leaves = list(_secret_leaves(value))

        redacted_text = redact_text(json.dumps(value))
        redacted_struct = str(redact_secrets(value))
        identity_text = json.dumps(value)  # no-op "redactor"

        for leaf in leaves:
            prefix = leaf[:6]
            if prefix in redacted_text or prefix in redacted_struct:
                leaks_with_redaction += 1
            if prefix in identity_text:
                leaks_with_identity += 1

    assert leaks_with_redaction == 0, f"{leaks_with_redaction} secret prefixes survived redaction"
    assert leaks_with_identity > 0, "control did not fire: no secret leaves were generated to leak"


_QUOTE_NOISE_CHARS = "\"'\\ abc{}[]:=,"
_KEY_VALUE_FORMS = (
    '"{name}": "{value}"',
    "'{name}': '{value}'",
    "{name}: {value}",
    "{name}={value}",
    '\\"{name}\\": \\"{value}\\"',
)


def test_fuzz_text_nothing_survives_a_secret_separator_regardless_of_quote_noise():
    """2000 seeded quote-noise prefixes across 5 key forms; nothing after a
    secret-named separator survives, whatever quotes came before. The
    identity-swap control (redact_text replaced with a no-op) must report
    leaks, the same way an earlier odd-quote regression did."""
    from derhost._redact import redact_text

    rng = random.Random(20260925)
    leaks_with_redaction = 0
    leaks_with_identity = 0

    for _ in range(2000):
        noise = "".join(rng.choice(_QUOTE_NOISE_CHARS) for _ in range(rng.randint(0, 12)))
        form = rng.choice(_KEY_VALUE_FORMS)
        secret = "".join(rng.choice("abcdefghijklmnop0123456789") for _ in range(10))
        text = noise + form.format(name="token", value=secret)

        result = redact_text(text)
        if secret in result:
            leaks_with_redaction += 1
        if secret in text:  # identity "redactor"
            leaks_with_identity += 1

    assert leaks_with_redaction == 0, f"{leaks_with_redaction} of 2000 quote-noise cases leaked"
    assert leaks_with_identity == 2000  # every generated case contains its own secret verbatim


_BLANKING_CASES = {}


def _register_blanking_case(name):
    def wrap(fn):
        _BLANKING_CASES[name] = fn
        return fn

    return wrap


@_register_blanking_case("literal")
def _case_literal(secret):
    return f"error: {secret}", {"token": secret}


@_register_blanking_case("json_escaped")
def _case_json_escaped(secret):
    return f'error: "{json.dumps(secret)[1:-1]}"', {"token": secret}


@_register_blanking_case("json_escaped_non_ascii")
def _case_json_escaped_non_ascii(secret):
    escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
    return f'error: "{escaped}"', {"token": secret}


@_register_blanking_case("repr")
def _case_repr(secret):
    return f"error: {secret!r}", {"token": secret}


@_register_blanking_case("bytes_repr")
def _case_bytes_repr(secret):
    b = secret.encode("utf-8")
    return f"error: {b!r}", {"token": b}


@_register_blanking_case("bytes_decoded")
def _case_bytes_decoded(secret):
    b = secret.encode("utf-8")
    return f"error: {b.decode()}", {"token": b}


@_register_blanking_case("pydantic_shortened")
def _case_pydantic_shortened(secret):
    shortened = secret[:24] + "..." + secret[-22:] if len(secret) > 50 else secret[:8] + "..." + secret[-8:]
    return f"1 validation error\ninput_value='{shortened}'", {"token": secret}


@_register_blanking_case("container")
def _case_container(secret):
    return str({"credentials": {"password": secret}}), {"credentials": {"password": secret}}


@pytest.mark.parametrize("case_name", sorted(_BLANKING_CASES))
def test_redact_known_secret_values_blanks_every_form(case_name):
    """Every form a secret can render as (literal, JSON-escaped with and
    without ensure_ascii, repr, bytes repr and decode, a pydantic-style
    shortened repr, and a value nested inside a container) is blanked."""
    from derhost._redact import redact_known_secret_values

    secret = 'ab"cd\\ef01gh"secretval-74chars-of-pydantic-style-token-material-abcdefgh'
    text, payload = _BLANKING_CASES[case_name](secret)

    result = redact_known_secret_values(text, payload)

    assert secret[:8] not in result and secret[-8:] not in result, f"{case_name} leaked: {result!r}"


def test_redact_known_secret_values_blanks_two_occurrences_and_keeps_the_rest():
    """Item 1: the bounded head-and-tail match blanks every occurrence of a
    shortened secret, not just the first, and leaves surrounding text alone.
    An unbounded match would instead span from the first occurrence's head
    all the way to the second occurrence's tail, swallowing the text
    between them."""
    from derhost._redact import redact_known_secret_values

    secret = "s3cr3t-marker-abcdefghij-0123456789-longenough"
    shortened = secret[:8] + "..." + secret[-8:]
    text = f"token {shortened} expired at 12:00; resend token {shortened} after rotation"

    result = redact_known_secret_values(text, {"token": secret})

    assert secret not in result
    assert "expired at 12:00" in result
    assert "after rotation" in result
    assert result.count("[REDACTED]") == 2


def test_redact_known_secret_values_blanks_a_head_only_shortened_secret():
    """Item 1: a secret shortened to its first part only, with no tail
    shown, is still blanked. The head-and-tail match alone would never fire
    here, since there is no tail to anchor on."""
    from derhost._redact import redact_known_secret_values

    secret = "s3cr3t-marker-headonly-0123456789-longenough"
    shortened = secret[:8] + "..."
    text = f"value was {shortened} and stayed that way"

    result = redact_known_secret_values(text, {"token": secret})

    assert secret[:8] not in result
    assert "and stayed that way" in result


def test_redact_known_secret_values_blanks_shortened_secret_with_backslash_in_head():
    """Item 1: when a secret's first 8 characters need escaping (a quote, a
    backslash), a display library shortens its own escaped repr, not the
    raw text; anchoring on the raw secret's head alone misses this."""
    from derhost._redact import redact_known_secret_values

    secret = 'ab"cd\\ef01gh"secretval-74chars-of-pydantic-style-token-material-abcdefgh'
    escaped = repr(secret)[1:-1]
    shortened = escaped[:8] + "..." + escaped[-8:]
    text = f"1 validation error\ninput_value='{shortened}'"

    result = redact_known_secret_values(text, {"token": secret})

    assert secret[:8] not in result
    assert secret[-8:] not in result


# Bound justified by measurement: the head-and-tail match is bounded to
# _MAX_SHORTENED_SPAN characters, so it stays close to linear in the input
# size instead of the quadratic behavior an unbounded ".*" showed on
# adversarial input. A dense repeat of the head with no tail nearby (one
# match attempt every 20 characters) measured 3.7s on a 512 KB text at the
# head this fixes, and 0.03s on the same host with the bounded match.
_VALUE_BLANKING_TIME_BOUND_SECONDS = 1.0
_VALUE_BLANKING_SHAPE_SIZE = 512 * 1024


def test_redact_known_secret_values_value_blanking_stays_within_time_bound():
    """Item 1: a 512 KB error text carrying a dense repeat of a shortened
    secret's head, with no matching tail nearby, must not make the bounded
    match quadratic."""
    from derhost._redact import redact_known_secret_values

    secret = "abcdefgh" + "z" * 30 + "12345678"
    near_miss = "abcdefgh" + "x" * 12
    text = near_miss * (_VALUE_BLANKING_SHAPE_SIZE // len(near_miss))

    start = time.monotonic()
    redact_known_secret_values(text, {"token": secret})
    elapsed = time.monotonic() - start

    assert elapsed < _VALUE_BLANKING_TIME_BOUND_SECONDS, (
        f"value blanking took {elapsed:.3f}s, expected under {_VALUE_BLANKING_TIME_BOUND_SECONDS}s"
    )


def test_redact_known_secret_values_unchanged_with_no_payload_secrets():
    """Wire contract: no payload secrets means byte-identical text."""
    from derhost._redact import redact_known_secret_values

    text = "boom short and eightlet failed with retry True"
    result = redact_known_secret_values(text, {"auth": None, "retry": True, "token": 1, "pin": "1234"})
    assert result == text


def test_redact_known_secret_values_blanks_eight_digit_int_secret():
    """An int secret with 8+ decimal digits (e.g. a PIN-like value) is
    blanked; a shorter int is left alone (existing short-value test)."""
    from derhost._redact import redact_known_secret_values

    text = "boom 87654321 failed"
    assert redact_known_secret_values(text, {"pin": 87654321}) == "boom [REDACTED] failed"


def test_tuple_through_truncate_for_log_is_redacted():
    """Regression: a tuple value (not dict or list) reaching truncate_for_log
    must go through structural redaction, not a str()-then-text-scan that
    loses the field name inside the tuple's own repr quoting."""
    from derhost._redact import truncate_for_log

    result = truncate_for_log(({"token": MARKER},))
    assert MARKER not in result


def test_redact_secrets_redacts_a_bare_error_string():
    """Regression: redact_secrets applied directly to a string (the
    fastapi_message_bus.py:428 call shape, `_redact_secrets(error)` on a str)
    must not return it unchanged."""
    from derhost._redact import redact_secrets

    result = redact_secrets(f"validation failed for token: {MARKER}")
    assert MARKER not in result


class TestExceptionMessagesWithContainerSecretsNeverLeak:
    """Regression: a dict- or list-valued secret nested in an RPC exception
    must not leak on the wire error or in the log, the same as a scalar
    secret already does not."""

    @pytest.fixture(autouse=True)
    def setup_agent(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        self.agent = self.manager.create_connected_agent("gs_redact_container_target")

        def raising_method(**kwargs):
            raise ValueError(f"boom {kwargs.get('token')}")

        self.agent.vip.rpc.export_method("raising_container_target_method", raising_method)
        gevent.sleep(1)

        yield

        self.agent.disconnect()

    def test_container_secret_does_not_leak_on_wire_or_in_logs(self, caplog):
        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_container_target",
            "method": "raising_container_target_method",
            "params": {"kwargs": {"token": {"password": MARKER}}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)
            gevent.sleep(1)

        assert response.status_code == 200
        data = response.json()
        assert MARKER_PREFIX not in json.dumps(data)

        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)


class TestFastapiUnexpectedRpcErrorIsTextRedacted:
    """Item 2: fastapi_message_bus.py's fallback "Unexpected error in RPC
    processing" log, the one error log line outside the delta files, must
    go through redact_text too."""

    @pytest.fixture(autouse=True)
    def setup_agent(self, message_bus_manager_fixture):
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        self.agent = self.manager.create_connected_agent("gs_redact_fastapi_target")
        gevent.sleep(1)

        yield

        self.agent.disconnect()

    def test_unexpected_rpc_error_log_is_text_redacted(self, caplog, monkeypatch):
        """Force the fallback except branch by making the connection
        manager's own send_message raise, with a secret-named pattern in
        its text that is not part of any RPC payload."""

        async def raising_send_message(*args, **kwargs):
            raise RuntimeError(f"downstream auth failed, password: {MARKER}")

        monkeypatch.setattr(self.manager.bus.manager, "send_message", raising_send_message)

        base_url = self.manager.get_base_url()
        rpc_data = {
            "jsonrpc": "2.0",
            "id": "gs_redact_fastapi_target",
            "method": "some_method",
            "params": {"kwargs": {}},
        }

        with caplog.at_level(logging.DEBUG):
            response = httpx.post(f"{base_url}/gs", json=rpc_data, timeout=10.0)

        assert response.status_code == 200
        derhost_records = [r for r in caplog.records if is_derhost_record(r)]
        assert derhost_records, "expected at least one record from a derhost logger"
        assert_marker_absent(derhost_records, MARKER)
