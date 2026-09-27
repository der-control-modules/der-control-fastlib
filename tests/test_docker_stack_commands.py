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
import shlex
import shutil
import socket
import subprocess
import threading
from collections.abc import Iterator
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


# --- DERHOST_PUBLISH_HOST resolution (_stack-resolve-host). ------------------


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
    result = _run_make("_stack-resolve-host", overrides={"DERHOST_PUBLISH_HOST": value})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_host


def test_preflight_rejects_scope_id() -> None:
    # Control: ipaddress.ip_address() accepts a scope id on its own (3.9+),
    # so this refusal is not free; the resolver adds the check itself,
    # since docker compose's ports mapping cannot use a scoped address.
    assert ipaddress.ip_address("fe80::1%eth0").version == 6
    result = _run_make("_stack-resolve-host", overrides={"DERHOST_PUBLISH_HOST": "fe80::1%eth0"})
    assert result.returncode != 0
    assert "scope id" in result.stderr


def test_preflight_rejects_localhost() -> None:
    result = _run_make("_stack-resolve-host", overrides={"DERHOST_PUBLISH_HOST": "localhost"})
    assert result.returncode != 0
    assert "not a valid IP address" in result.stderr


def test_preflight_rejects_quote_injection_without_executing_it(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    payload = f'127.0.0.1"; touch {marker}; #'
    result = _run_make("_stack-resolve-host", overrides={"DERHOST_PUBLISH_HOST": payload})
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", STACK_PORT))
    except OSError:
        pytest.skip(f"127.0.0.1:{STACK_PORT} is already held by something else on this host")
    finally:
        sock.close()
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
        result = _run_make("_stack-resolve-host")
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
        result = _run_make("_stack-resolve-host")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == _compose_config_host_ip() == "0.0.0.0"
    finally:
        env_path.unlink()


def test_preflight_agrees_with_compose_when_env_is_set_empty_over_env_file() -> None:
    env_path = REPO_ROOT / "docker" / "server" / ".env"
    assert not env_path.exists(), "a real .env here would be clobbered by this test"
    env_path.write_text("DERHOST_PUBLISH_HOST=127.0.0.2\n")
    try:
        result = _run_make("_stack-resolve-host", overrides={"DERHOST_PUBLISH_HOST": ""})
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
# (the four caller-supplied names); `git grep` for `$(NAME)` inside
# docker.mk finds no remaining reference to any of the four outside their
# own declaration. AGENT_DIRS (#68) is fixed text this file itself
# declares, not caller- or filesystem-derived, so it carries no such test.

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
    try:
        server = http.server.HTTPServer((host, STACK_PORT), _ConnectionsHandler)
    except OSError:
        pytest.skip(f"{host}:{STACK_PORT} is already held by something else on this host")
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


# --- shared docker stub: replaces the real `docker` CLI on PATH. -----------
#
# Every invocation is logged (argv plus DERHOST_PUBLISH_HOST) before it is
# dispatched, so a test can assert on exactly what reached `docker` without
# any of it reaching a real daemon. `compose ... config` is the one
# subcommand forwarded to the real binary, since it needs no daemon and its
# real output is what a caller (DERHOST_RESOLVE_PY, the disjointness test)
# actually depends on; every other subcommand this stub knows about returns
# a canned, per-test-configurable result, and anything else fails loudly
# rather than silently doing nothing.
_DOCKER_STUB = """#!/usr/bin/env bash
set -euo pipefail
{
    printf 'DERHOST_PUBLISH_HOST=%s ARGS:' "${DERHOST_PUBLISH_HOST-unset}"
    printf ' %q' "$@"
    printf '\\n'
} >> "$STUB_LOG"

if [ "${1-}" = "compose" ]; then
    shift
    case " $* " in
        *" config "*)
            exec "$STUB_REAL_DOCKER" compose "$@"
            ;;
        *" port "*)
            printf '%s\\n' "${STUB_PORT_OUTPUT:-0.0.0.0:32768}"
            exit "${STUB_PORT_EXIT:-0}"
            ;;
        *" up "*)
            exit "${STUB_UP_EXIT:-0}"
            ;;
        *" down "*)
            exit "${STUB_DOWN_EXIT:-0}"
            ;;
        *" build "*)
            exit "${STUB_BUILD_EXIT:-0}"
            ;;
        *" ps "*)
            exit "${STUB_COMPOSE_PS_EXIT:-0}"
            ;;
        *)
            echo "stub: unexpected docker compose invocation: $*" >&2
            exit 1
            ;;
    esac
elif [ "${1-}" = "ps" ]; then
    printf '%s\\n' "${STUB_DOCKER_PS_OUTPUT:-}"
    exit 0
elif [ "${1-}" = "inspect" ]; then
    # _stack-wait-healthy's health poll; a fixed "healthy" default lets a
    # stack-up test reach its agent-loop compose calls without a real wait.
    printf '%s\\n' "${STUB_INSPECT_HEALTH:-healthy}"
    exit 0
elif [ "${1-}" = "network" ] && [ "${2-}" = "ls" ]; then
    printf '%s\\n' "${STUB_NETWORK_LS_OUTPUT:-}"
    exit 0
elif [ "${1-}" = "volume" ] && [ "${2-}" = "ls" ]; then
    printf '%s\\n' "${STUB_VOLUME_LS_OUTPUT:-}"
    exit 0
else
    echo "stub: unexpected docker invocation: $*" >&2
    exit 1
fi
"""


@pytest.fixture
def docker_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub_path = bin_dir / "docker"
    stub_path.write_text(_DOCKER_STUB)
    stub_path.chmod(0o755)
    real_docker = shutil.which("docker")
    assert real_docker is not None, "the real docker CLI must be on PATH to forward `compose ... config`"
    log_path = tmp_path / "stub.log"
    log_path.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(log_path))
    monkeypatch.setenv("STUB_REAL_DOCKER", real_docker)
    return log_path


def _stub_calls(log_text: str) -> list[list[str]]:
    calls = []
    for line in log_text.splitlines():
        if not line.strip():
            continue
        _, _, rest = line.partition("ARGS:")
        calls.append(shlex.split(rest))
    return calls


def _compose_calls(calls: list[list[str]], subcommand: str) -> list[list[str]]:
    return [c for c in calls if c and c[0] == "compose" and subcommand in c]


def _flag_value(call: list[str], flag: str) -> str | None:
    if flag not in call:
        return None
    return call[call.index(flag) + 1]


# --- smoke.sh's overlay shares no object names with the base stack. -------


def test_smoke_overlay_config_shares_no_names_with_the_base_stack() -> None:
    env = os.environ.copy()
    for key in _OVERRIDE_KEYS:
        env.pop(key, None)

    def _config(*extra_files: str, project: str | None = None) -> dict:
        cmd = ["docker", "compose"]
        if project:
            cmd += ["-p", project]
        cmd += ["-f", "docker/server/docker-compose.yml"]
        for f in extra_files:
            cmd += ["-f", f]
        cmd += ["--project-directory", "docker/server", "config", "--format", "json"]
        result = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    base = _config()
    merged = _config("docker/server/compose.smoke.yml", project="derhost-smoke")

    base_svc = base["services"]["derhost-server"]
    merged_svc = merged["services"]["derhost-server"]
    base_names = {
        base["name"],
        base_svc["container_name"],
        base_svc["image"],
        base["networks"]["derhost-net"]["name"],
        base["volumes"]["derhost-home"]["name"],
    }
    merged_names = {
        merged["name"],
        merged_svc["container_name"],
        merged_svc["image"],
        merged["networks"]["derhost-net"]["name"],
        merged["volumes"]["derhost-home"]["name"],
    }
    assert base_names.isdisjoint(merged_names), (base_names, merged_names)
    assert len(merged_svc["ports"]) == 1, merged_svc["ports"]
    assert "published" not in merged_svc["ports"][0], merged_svc["ports"]


# --- smoke.sh's own project-label refusal and trap discipline. ------------


def _run_smoke(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("DERHOST_SMOKE_STEP_TIMEOUT", "20")
    env.update(extra_env)
    return subprocess.run(
        [str(REPO_ROOT / "docker" / "smoke.sh")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )


def test_smoke_refuses_when_a_leftover_object_exists(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_DOCKER_PS_OUTPUT", "deadbeef0001")
    result = _run_smoke({})
    assert result.returncode != 0
    assert "[smoke] refusing" in result.stdout, result.stdout
    calls = _stub_calls(docker_stub.read_text())
    assert not _compose_calls(calls, "build"), calls
    assert not _compose_calls(calls, "up"), calls
    assert not _compose_calls(calls, "down"), calls


def test_smoke_tears_down_once_when_up_fails(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_UP_EXIT", "1")
    result = _run_smoke({})
    assert result.returncode != 0
    calls = _stub_calls(docker_stub.read_text())
    assert len(_compose_calls(calls, "build")) == 1, calls
    assert len(_compose_calls(calls, "up")) == 1, calls
    down_calls = _compose_calls(calls, "down")
    assert len(down_calls) == 1, calls


def test_smoke_every_compose_call_carries_the_smoke_project_flag(docker_stub: Path) -> None:
    # Happy path through build/up/port; the final `check` step has nothing
    # real to reach and fails, which still exercises every compose call
    # this property covers (including the down the failure triggers).
    result = _run_smoke({})
    assert result.returncode != 0
    calls = _stub_calls(docker_stub.read_text())
    compose_calls = [c for c in calls if c and c[0] == "compose"]
    assert compose_calls, "no compose calls logged"
    for subcommand in ("build", "up", "port", "down"):
        matching = _compose_calls(calls, subcommand)
        assert matching, f"no compose {subcommand} call logged: {calls}"
    for call in compose_calls:
        assert _flag_value(call, "-p") == "derhost-smoke", call


# --- docker.mk pins the server compose project name. -----------------------


def test_stack_down_passes_server_project_name(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "zzz")
    _run_make("stack-down")
    calls = _stub_calls(docker_stub.read_text())
    down_calls = _compose_calls(calls, "down")
    assert down_calls, calls
    for call in down_calls:
        assert _flag_value(call, "-p") == "derhost-server", call


def test_stack_status_passes_server_project_name(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "zzz")
    _run_make("stack-status")
    calls = _stub_calls(docker_stub.read_text())
    ps_calls = _compose_calls(calls, "ps")
    assert ps_calls, calls
    for call in ps_calls:
        assert _flag_value(call, "-p") == "derhost-server", call


def test_stack_up_passes_server_project_name(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", STACK_PORT))
    except OSError:
        pytest.skip(f"127.0.0.1:{STACK_PORT} is already held by something else on this host")
    finally:
        sock.close()
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "zzz")
    # `up` fails at once so `_stack-wait-healthy` (a 60s loop against a
    # container this stub never creates) never runs.
    monkeypatch.setenv("STUB_UP_EXIT", "1")
    _run_make("stack-up", overrides={"C": "server"})
    calls = _stub_calls(docker_stub.read_text())
    up_calls = _compose_calls(calls, "up")
    assert up_calls, calls
    for call in up_calls:
        assert _flag_value(call, "-p") == "derhost-server", call


# --- docker.mk pins each agent's own compose project too. -------------------
#
# AGENT_DIRS is empty at this PR (#68), so these tests add one entry on the
# `make` command line, standing in for the per-agent PR that will list a real
# directory; the directory itself need not hold a compose file, since the
# stub `docker` never reads one.


@pytest.fixture
def agent_fixture_dir() -> Iterator[Path]:
    path = REPO_ROOT / "docker" / "agent-fixture"
    path.mkdir()
    try:
        yield path
    finally:
        path.rmdir()


def test_stack_status_passes_agent_project_name(docker_stub: Path, agent_fixture_dir: Path) -> None:
    _run_make("AGENT_DIRS=agent-fixture", "stack-status")
    calls = _stub_calls(docker_stub.read_text())
    ps_calls = _compose_calls(calls, "ps")
    assert len(ps_calls) == 2, calls
    agent_call = next(c for c in ps_calls if _flag_value(c, "--project-directory") == "docker/agent-fixture")
    assert _flag_value(agent_call, "-p") == "derhost-agent-fixture", agent_call


def test_stack_down_stops_agents_before_the_server(docker_stub: Path, agent_fixture_dir: Path) -> None:
    _run_make("AGENT_DIRS=agent-fixture", "stack-down")
    calls = _stub_calls(docker_stub.read_text())
    down_calls = _compose_calls(calls, "down")
    assert len(down_calls) == 2, calls
    assert [_flag_value(c, "-p") for c in down_calls] == ["derhost-agent-fixture", "derhost-server"], down_calls


def test_stack_up_passes_agent_project_name(docker_stub: Path, agent_fixture_dir: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", STACK_PORT))
    except OSError:
        pytest.skip(f"127.0.0.1:{STACK_PORT} is already held by something else on this host")
    finally:
        sock.close()
    _run_make("AGENT_DIRS=agent-fixture", "stack-up", overrides={"C": "all"})
    calls = _stub_calls(docker_stub.read_text())
    up_calls = _compose_calls(calls, "up")
    assert len(up_calls) == 2, calls
    agent_call = next(c for c in up_calls if _flag_value(c, "--project-directory") == "docker/agent-fixture")
    assert _flag_value(agent_call, "-p") == "derhost-agent-fixture", agent_call


# --- agent directories are a fixed list, not a filesystem glob. -----------


def test_stack_status_ignores_an_unlisted_directory_even_with_a_compose_file(docker_stub: Path) -> None:
    payload_dir = REPO_ROOT / "docker" / "x y"
    payload_dir.mkdir()
    try:
        (payload_dir / "docker-compose.yml").write_text("name: probe-dirname\nservices: {}\n")
        result = _run_make("stack-status")
        assert result.returncode == 0, result.stdout + result.stderr
        calls = _stub_calls(docker_stub.read_text())
        compose_calls = [c for c in calls if c and c[0] == "compose"]
        assert len(compose_calls) == 1, calls
        assert _flag_value(compose_calls[0], "-p") == "derhost-server", compose_calls
    finally:
        for child in payload_dir.iterdir():
            child.unlink()
        payload_dir.rmdir()


# --- DERHOST_PUBLISH_HOST reaches stack-preflight's sub-make. --------------


def test_stack_up_forwards_publish_host_through_the_preflight_submake(
    docker_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # docker.mk's stack-up passes DERHOST_PUBLISH_HOST to the `stack-preflight`
    # sub-make explicitly; without that, `_stack-resolve-host`'s own config
    # call inside it sees no override and resolves the compose file's
    # 127.0.0.1 default instead of the caller's 127.0.0.2.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.2", STACK_PORT))
    except OSError:
        pytest.skip(f"127.0.0.2:{STACK_PORT} is already held by something else on this host")
    finally:
        sock.close()
    monkeypatch.setenv("STUB_UP_EXIT", "1")
    _run_make("stack-up", overrides={"C": "server", "DERHOST_PUBLISH_HOST": "127.0.0.2"})
    config_lines = [line for line in docker_stub.read_text().splitlines() if "ARGS:" in line and "config" in line]
    assert config_lines, "no compose config call logged"
    assert all("DERHOST_PUBLISH_HOST=127.0.0.2" in line for line in config_lines), config_lines
