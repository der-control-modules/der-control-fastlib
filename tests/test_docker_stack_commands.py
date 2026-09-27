"""docker/docker.mk: DERHOST_PUBLISH_HOST validation and stack-check (#68).

Each `make` target under test reads its inputs from the environment, so
these tests invoke `make` as a subprocess exactly as a developer would,
rather than importing anything.
"""

from __future__ import annotations

import http.server
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STACK_PORT = 5410

_OVERRIDE_KEYS = ("DERHOST_PUBLISH_HOST", "EXPECTED", "DERHOST_CHECK_BASE_URL", "C")

# When this suite runs under `make test`, the outer make exports MAKELEVEL
# and MAKEFLAGS to pytest's own environment; a `make` subprocess that
# inherits them treats itself as a sub-make and prints "Entering/Leaving
# directory" banners to stdout, which every assertion on stdout below would
# otherwise have to expect. Dropping both makes each nested `make` behave as
# a fresh top-level invocation, matching a plain `make stack-check` run.
_MAKE_RECURSION_KEYS = ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")


def _run_make(*args: str, overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (*_OVERRIDE_KEYS, *_MAKE_RECURSION_KEYS):
        env.pop(key, None)
    if overrides:
        env.update(overrides)
    return subprocess.run(
        ["make", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


# --- DERHOST_PUBLISH_HOST validation (stack-preflight). ---------------------


@pytest.mark.parametrize(
    ("value", "expected_host"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.2", "127.0.0.2"),
        ("", "127.0.0.1"),
        ("127.0.0.3", "127.0.0.3"),
    ],
)
def test_preflight_accepts(value: str, expected_host: str) -> None:
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": value})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_host


def test_preflight_rejects_scope_id() -> None:
    # Control: ipaddress.ip_address() accepts a scope id on its own (3.9+),
    # so this refusal is not free; stack-preflight adds the check itself,
    # since docker compose's ports mapping cannot use a scoped address.
    assert ipaddress.ip_address("fe80::1%eth0").version == 6
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": "fe80::1%eth0"})
    assert result.returncode != 0
    assert "scope id" in result.stderr


def test_preflight_rejects_localhost() -> None:
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": "localhost"})
    assert result.returncode != 0
    assert "not a valid IP address" in result.stderr


def test_preflight_rejects_quote_injection_without_executing_it(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    payload = f'127.0.0.1"; touch {marker}; #'
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": payload})
    assert result.returncode != 0
    assert "not a valid IP address" in result.stderr
    assert not marker.exists()


def test_preflight_refuses_when_port_busy() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", STACK_PORT))
    sock.listen(1)
    try:
        result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": "127.0.0.1"})
        assert result.returncode != 0
        assert "already bound" in result.stderr
        assert "a derhost or root stack" in result.stderr
    finally:
        sock.close()


def test_preflight_succeeds_once_port_is_free() -> None:
    # Control: the same command on the same host succeeds once nothing holds
    # the port, so the refusal above is about the port, not a broken command.
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": "127.0.0.1"})
    assert result.returncode == 0, result.stderr


# --- preflight resolves the same host docker compose will publish on. ------


def _compose_config_host_ip(overrides: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    for key in _OVERRIDE_KEYS:
        env.pop(key, None)
    if overrides:
        env.update(overrides)
    result = subprocess.run(
        ["docker", "compose", "-f", "docker/server/docker-compose.yml", "--project-directory", "docker/server", "config", "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)["services"]["derhost-server"]["ports"][0]["host_ip"]


def test_preflight_agrees_with_compose_on_an_export_prefixed_env_line() -> None:
    # docker compose strips a leading "export " keyword from a .env line; a
    # parser that instead treats "export DERHOST_PUBLISH_HOST" as the whole
    # key (a naive key, _, value = line.partition("=")) never matches
    # DERHOST_PUBLISH_HOST and falls through to its own default, 127.0.0.1,
    # while compose resolves 0.0.0.0.
    env_path = REPO_ROOT / "docker" / "server" / ".env"
    assert not env_path.exists(), "a real .env here would be clobbered by this test"
    env_path.write_text("export DERHOST_PUBLISH_HOST=0.0.0.0\n")
    try:
        result = _run_make("stack-preflight")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == _compose_config_host_ip() == "0.0.0.0"
    finally:
        env_path.unlink()


def test_preflight_agrees_with_compose_on_a_duplicate_env_file_key() -> None:
    # docker/server/.env is gitignored; this test owns its full lifecycle.
    env_path = REPO_ROOT / "docker" / "server" / ".env"
    assert not env_path.exists(), "a real .env here would be clobbered by this test"
    # compose's own .env parsing takes the LAST match for a repeated key;
    # a naive parser that stops at the first match (the pre-fix preflight
    # script) picks 127.0.0.2 here instead of compose's 0.0.0.0.
    env_path.write_text("DERHOST_PUBLISH_HOST=127.0.0.2\nDERHOST_PUBLISH_HOST=0.0.0.0\n")
    try:
        result = _run_make("stack-preflight")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == _compose_config_host_ip() == "0.0.0.0"
    finally:
        env_path.unlink()


def test_preflight_agrees_with_compose_when_env_is_set_empty_over_env_file() -> None:
    env_path = REPO_ROOT / "docker" / "server" / ".env"
    assert not env_path.exists(), "a real .env here would be clobbered by this test"
    env_path.write_text("DERHOST_PUBLISH_HOST=127.0.0.2\n")
    try:
        result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": ""})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == _compose_config_host_ip({"DERHOST_PUBLISH_HOST": ""}) == "127.0.0.1"
    finally:
        env_path.unlink()


# --- No caller-supplied or filesystem-derived value is Make-expanded. -------
#
# GNU Make auto-exports a command-line-set variable's text into every
# recipe's subprocess environment, expanding a `$(shell ...)` payload
# embedded in it as a side effect, whether or not any recipe references the
# variable by name; a Make-level reference ($(NAME)) in recipe text has the
# same problem. docker.mk avoids both routes for every caller-supplied
# name. Swept: DERHOST_PUBLISH_HOST, C, EXPECTED, DERHOST_CHECK_BASE_URL
# (the four caller-supplied names) and OTHER_COMPOSE_FILES (filesystem-
# derived from `wildcard`); `git grep` for `$(NAME)` inside docker.mk finds
# no remaining reference to any of the five outside their own declaration.

_INJECTION_TARGET = {
    "DERHOST_PUBLISH_HOST": "stack-status",
    "C": "stack-up",
    "EXPECTED": "stack-check",
    "DERHOST_CHECK_BASE_URL": "stack-check",
}


@pytest.mark.parametrize("var_name", sorted(_INJECTION_TARGET))
def test_no_caller_value_is_make_expanded_via_environment(tmp_path: Path, var_name: str) -> None:
    marker = tmp_path / f"pwned-env-{var_name}"
    payload = f"$(shell touch {marker})"
    _run_make(_INJECTION_TARGET[var_name], overrides={var_name: payload})
    assert not marker.exists()


@pytest.mark.parametrize("var_name", sorted(_INJECTION_TARGET))
def test_no_caller_value_is_make_expanded_via_command_line(tmp_path: Path, var_name: str) -> None:
    marker = tmp_path / f"pwned-cli-{var_name}"
    payload = f"$(shell touch {marker})"
    result = _run_make(f"{var_name}={payload}", _INJECTION_TARGET[var_name])
    assert not marker.exists(), result.stdout + result.stderr


def test_no_compose_directory_name_reaches_the_shell_as_code() -> None:
    # The marker name carries no path separator: a directory name is one
    # shell word once spliced into recipe text, and `make` runs recipes
    # with REPO_ROOT as the working directory, so a bare filename in the
    # backtick payload lands there if the backtick is ever executed.
    marker_name = "pwned-dirname-test-no-compose-directory-name"
    marker = REPO_ROOT / marker_name
    payload_dir = REPO_ROOT / "docker" / f"x`touch {marker_name}`"
    payload_dir.mkdir()
    try:
        (payload_dir / "docker-compose.yml").write_text("name: probe-dirname\nservices: {}\n")
        result = _run_make("stack-status")
        assert not marker.exists(), result.stdout + result.stderr
    finally:
        if marker.exists():
            marker.unlink()
        for child in payload_dir.iterdir():
            child.unlink()
        payload_dir.rmdir()


# --- stack-check: GET /connections against a stub server. -------------------


class _ConnectionsHandler(http.server.BaseHTTPRequestHandler):
    connected_identities: set[str] = set()

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        if self.path != "/connections":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"connections": {name: {} for name in self.connected_identities}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # quiet the test output
        pass


@pytest.fixture
def connections_server():
    _ConnectionsHandler.connected_identities = {"agent.one"}
    server = http.server.HTTPServer(("127.0.0.1", 0), _ConnectionsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stack_check_fake_identity_is_missing(connections_server: http.server.HTTPServer) -> None:
    base_url = f"http://127.0.0.1:{connections_server.server_port}"
    result = _run_make("stack-check", overrides={"DERHOST_CHECK_BASE_URL": base_url, "EXPECTED": "agent.missing"})
    assert result.returncode != 0
    assert "agent.missing: MISSING" in result.stdout


def test_stack_check_expected_identity_is_connected(connections_server: http.server.HTTPServer) -> None:
    base_url = f"http://127.0.0.1:{connections_server.server_port}"
    result = _run_make("stack-check", overrides={"DERHOST_CHECK_BASE_URL": base_url, "EXPECTED": "agent.one"})
    assert result.returncode == 0, result.stderr
    assert "agent.one: connected" in result.stdout


def test_stack_check_zero_expected_exits_zero(connections_server: http.server.HTTPServer) -> None:
    # Control: this reaches the same server as the fake-identity case above,
    # so a zero here is "nothing was expected", not "the request never ran".
    base_url = f"http://127.0.0.1:{connections_server.server_port}"
    result = _run_make("stack-check", overrides={"DERHOST_CHECK_BASE_URL": base_url, "EXPECTED": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_stack_check_fails_on_a_non_2xx_status() -> None:
    class _UnauthorizedHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"error": "unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _UnauthorizedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        # EXPECTED="" is smoke.sh's own case: with no identity to check, a
        # reader of the (empty, but validly parsed) body would exit 0 even
        # though the request itself failed.
        result = _run_make("stack-check", overrides={"DERHOST_CHECK_BASE_URL": base_url, "EXPECTED": ""})
        assert result.returncode != 0
        assert "returned status 401" in result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stack_check_does_not_follow_a_redirect() -> None:
    class _RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = _run_make("stack-check", overrides={"DERHOST_CHECK_BASE_URL": base_url, "EXPECTED": ""})
        assert result.returncode != 0
        assert "redirected" in result.stderr
        assert "refusing to follow" in result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


# --- stack-check's default target: resolved the same way preflight is. -----


def test_stack_check_default_rejects_localhost_like_preflight() -> None:
    result = _run_make("stack-check", overrides={"DERHOST_PUBLISH_HOST": "localhost"})
    assert result.returncode != 0
    assert "not a valid IP address" in result.stderr


def test_stack_check_default_rejects_scope_id_like_preflight() -> None:
    result = _run_make("stack-check", overrides={"DERHOST_PUBLISH_HOST": "fe80::1%eth0"})
    assert result.returncode != 0
    assert "scope id" in result.stderr


def test_stack_check_default_brackets_ipv6() -> None:
    # No server listens on ::1:5410; the connection-refused message names
    # the URL stack-check actually built, proving the bracket is there
    # (an unbracketed "http://::1:5410" is not a URL urllib can even parse
    # into host and port the same way).
    result = _run_make("stack-check", overrides={"DERHOST_PUBLISH_HOST": "::1", "EXPECTED": ""})
    assert result.returncode != 0
    assert "http://[::1]:5410/connections" in result.stderr


def test_stack_check_default_follows_env_file() -> None:
    env_path = REPO_ROOT / "docker" / "server" / ".env"
    assert not env_path.exists(), "a real .env here would be clobbered by this test"
    host = "127.0.0.7"
    _ConnectionsHandler.connected_identities = {"agent.one"}
    # .env is written only after the bind below succeeds: if the bind fails
    # (the address or port is already taken), nothing has written .env yet,
    # so there is nothing left behind for a next run's own guard to trip on.
    server = http.server.HTTPServer((host, STACK_PORT), _ConnectionsHandler)
    env_path.write_text(f"DERHOST_PUBLISH_HOST={host}\n")
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_make("stack-check", overrides={"EXPECTED": "agent.one"})
            assert result.returncode == 0, result.stdout + result.stderr
            assert "agent.one: connected" in result.stdout
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    finally:
        env_path.unlink()


# --- smoke.sh: never tears down a stack it did not start. ------------------


def test_smoke_does_not_tear_down_a_stack_it_never_started() -> None:
    # Port 5410 busy simulates a stack already up (the same convention
    # test_preflight_refuses_when_port_busy uses). If smoke.sh's trap were
    # armed before `up`, or if `build` ran ahead of `preflight`, a refusal
    # here would still tear the existing stack down.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", STACK_PORT))
    sock.listen(1)
    try:
        env = os.environ.copy()
        for key in _OVERRIDE_KEYS:
            env.pop(key, None)
        env["DERHOST_SMOKE_STEP_TIMEOUT"] = "20"
        result = subprocess.run(
            [str(REPO_ROOT / "docker" / "smoke.sh")],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode != 0
        assert "[smoke] preflight" in result.stdout, result.stdout
        assert "[smoke] build" not in result.stdout, result.stdout
        assert "[smoke] down" not in result.stdout, result.stdout
    finally:
        sock.close()


# --- make help: unaffected by docker.mk joining $(MAKEFILE_LIST). -----------


def test_help_lists_targets_with_no_filename_prefix_and_includes_stack_targets() -> None:
    # $(MAKEFILE_LIST) now holds two files (Makefile, docker/docker.mk); a
    # plain `grep -E ... $(MAKEFILE_LIST)` prefixes every match with its
    # filename once there is more than one, which breaks the awk split this
    # target relies on. `help`'s recipe uses `grep -hE` to suppress that.
    result = _run_make("help")
    assert result.returncode == 0, result.stderr
    assert "Makefile:" not in result.stdout
    assert "docker.mk:" not in result.stdout
    # A target defined before docker.mk was included, unaffected by this fix
    # on its own: proves the split still works, not just that the prefix
    # is gone.
    assert re.search(r"^\x1b\[36mtest\s*\x1b\[0m", result.stdout, re.MULTILINE), result.stdout
    # A target docker.mk itself defines, only reachable through the include.
    assert re.search(r"^\x1b\[36mstack-up\s*\x1b\[0m", result.stdout, re.MULTILINE), result.stdout
