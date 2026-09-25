"""Server port binding: compose publishes on loopback, start-server binds loopback.

Covers der-control-modules/der-control-fastlib#36: the host side of the
published compose port and start-server's bind address default to loopback,
with an override for each.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "generate-docker-compose.py"
START_SERVER = REPO_ROOT / "start-server"


def _generate(extra_env: dict[str, str] | None = None) -> str:
    """Run the generator in --dry-run mode and return its compose output."""
    env = os.environ.copy()
    env.pop("DERHOST_PUBLISH_HOST", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _server_port_line(compose_text: str) -> str:
    for line in compose_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('- "') and ":8000" in stripped:
            return stripped
    raise AssertionError(f"no server port line found in generator output:\n{compose_text}")


def test_generator_publishes_on_loopback_by_default() -> None:
    line = _server_port_line(_generate())
    assert line.startswith('- "127.0.0.1:'), line


def test_generator_publish_host_override_is_not_loopback() -> None:
    # Control: proves the assertion above can fail, since an unloopbacked
    # value must not start with "127.0.0.1:".
    line = _server_port_line(_generate({"DERHOST_PUBLISH_HOST": "0.0.0.0"}))
    assert not line.startswith('- "127.0.0.1:'), line
    assert line.startswith('- "0.0.0.0:'), line


def test_generator_publish_host_empty_is_loopback() -> None:
    # An explicitly empty value is unset, not a literal address to publish.
    line = _server_port_line(_generate({"DERHOST_PUBLISH_HOST": ""}))
    assert line.startswith('- "127.0.0.1:'), line


def test_generator_publish_host_rejects_non_ip() -> None:
    # Control: a well-formed override (the test above) succeeds, so a
    # failure here is the rejection, not an unrelated crash.
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _generate({"DERHOST_PUBLISH_HOST": "not-an-ip"})
    assert "not a valid IP address" in excinfo.value.stderr


def test_generator_publish_host_ipv6_is_bracketed() -> None:
    line = _server_port_line(_generate({"DERHOST_PUBLISH_HOST": "::1"}))
    assert line.startswith('- "[::1]:'), line


def _start_server_host_arg(tmp_path: Path, extra_env: dict[str, str] | None = None) -> str:
    """Run start-server against a stub python that records its --host value."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    captured = tmp_path / "captured-args.txt"
    stub = venv_bin / "python"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{captured}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    script = tmp_path / "start-server"
    script.write_text(START_SERVER.read_text())
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env.pop("DERHOST_HOST", None)
    if extra_env:
        env.update(extra_env)
    subprocess.run([str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)

    args = captured.read_text().splitlines()
    host_index = args.index("--host")
    return args[host_index + 1]


def test_start_server_binds_loopback_by_default(tmp_path: Path) -> None:
    assert _start_server_host_arg(tmp_path) == "127.0.0.1"


def test_start_server_host_override(tmp_path: Path) -> None:
    # Control: proves the assertion above can fail. A non-default value is
    # required here: base hardcodes --host 0.0.0.0 and ignores DERHOST_HOST
    # entirely, so an override of "0.0.0.0" would pass on base too, by
    # coincidence rather than because the override is wired.
    assert _start_server_host_arg(tmp_path, {"DERHOST_HOST": "192.0.2.1"}) == "192.0.2.1"
