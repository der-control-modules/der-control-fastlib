"""docker/docker.mk's real AGENT_DIRS default (#68): once it names both
interoperability-service and realtime-control-agent, `stack-up C=all` must
start the server then both agents, and `stack-down` must stop both agents
before the server. test_docker_stack_commands.py exercises this wiring
mechanism generically with a synthetic override; these tests exercise the
actual default this PR sets, with no override, through the same kind of
stub `docker` on PATH so no real daemon or image build is reached.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STACK_PORT = 5410

_OVERRIDE_KEYS = ("DERHOST_PUBLISH_HOST", "EXPECTED", "DERHOST_CHECK_BASE_URL", "C")
_MAKE_RECURSION_KEYS = ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")

_DOCKER_STUB = """#!/usr/bin/env bash
set -euo pipefail
{
    printf 'ARGS:'
    printf ' %q' "$@"
    printf '\\n'
} >> "$STUB_LOG"

if [ "${1-}" = "compose" ]; then
    shift
    case " $* " in
        *" config "*) exec "$STUB_REAL_DOCKER" compose "$@" ;;
        *" up "*) exit 0 ;;
        *" down "*) exit 0 ;;
        *" ps "*) exit 0 ;;
        *)
            echo "stub: unexpected docker compose invocation: $*" >&2
            exit 1
            ;;
    esac
elif [ "${1-}" = "inspect" ]; then
    printf 'healthy\\n'
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


def _stub_calls(log_text: str) -> list[list[str]]:
    calls = []
    for line in log_text.splitlines():
        if not line.strip():
            continue
        _, _, rest = line.partition("ARGS:")
        calls.append(shlex.split(rest))
    return calls


def _project_dirs(calls: list[list[str]], subcommand: str) -> list[str]:
    dirs = []
    for call in calls:
        if not call or call[0] != "compose" or subcommand not in call:
            continue
        idx = call.index("--project-directory")
        dirs.append(call[idx + 1])
    return dirs


def test_stack_down_default_stops_both_agents_before_the_server(docker_stub: Path) -> None:
    result = _run_make("stack-down")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _stub_calls(docker_stub.read_text())
    dirs = _project_dirs(calls, "down")
    assert dirs == [
        "docker/interoperability-service",
        "docker/realtime-control-agent",
        "docker/server",
    ], dirs


def test_stack_status_default_covers_the_server_and_both_agents(docker_stub: Path) -> None:
    result = _run_make("stack-status")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _stub_calls(docker_stub.read_text())
    dirs = _project_dirs(calls, "ps")
    assert dirs == [
        "docker/server",
        "docker/interoperability-service",
        "docker/realtime-control-agent",
    ], dirs


def test_stack_up_all_default_builds_and_starts_the_server_then_both_agents(docker_stub: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", STACK_PORT))
    except OSError:
        pytest.skip(f"127.0.0.1:{STACK_PORT} is already held by something else on this host")
    finally:
        sock.close()
    result = _run_make("stack-up", overrides={"C": "all"})
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _stub_calls(docker_stub.read_text())
    dirs = _project_dirs(calls, "up")
    assert dirs == [
        "docker/server",
        "docker/interoperability-service",
        "docker/realtime-control-agent",
    ], dirs
