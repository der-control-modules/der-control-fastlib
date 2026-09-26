"""docker/docker.mk: DERHOST_PUBLISH_HOST validation and stack-check (#68).

Each `make` target under test reads its inputs from the environment, so
these tests invoke `make` as a subprocess exactly as a developer would,
rather than importing anything: that is the path the actual value travels
(see [[claims-and-provenance]] "read by the consumer's path").
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
        assert "root docker-compose.yml" in result.stderr
    finally:
        sock.close()


def test_preflight_succeeds_once_port_is_free() -> None:
    # Control: the same command on the same host succeeds once nothing holds
    # the port, so the refusal above is about the port, not a broken command.
    result = _run_make("stack-preflight", overrides={"DERHOST_PUBLISH_HOST": "127.0.0.1"})
    assert result.returncode == 0, result.stderr


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
