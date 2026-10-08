"""docker/realtime-control-agent build gating (#68): the shared
docker/lib/check-clean.sh dirty-checkout refusal reached through this
agent's own wrapper, and build.sh's archive isolation and labeling for the
rt_control package layout (no src/ prefix, unlike interoperability-service).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_CLEAN = REPO_ROOT / "docker" / "realtime-control-agent" / "check-clean.sh"
BUILD_SH = REPO_ROOT / "docker" / "realtime-control-agent" / "build.sh"


def _run_check_clean(checkout: Path, allow_dirty: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("ALLOW_DIRTY", None)
    if allow_dirty is not None:
        env["ALLOW_DIRTY"] = allow_dirty
    return subprocess.run(
        ["bash", str(CHECK_CLEAN), str(checkout)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def scratch_checkout(tmp_path: Path) -> Path:
    """A throwaway git repo with one committed file."""
    repo = tmp_path / "agent-src"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "agent.py").write_text("# placeholder\n")
    _git(repo, "add", "agent.py")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_clean_checkout_passes(scratch_checkout: Path) -> None:
    result = _run_check_clean(scratch_checkout)
    assert result.returncode == 0, result.stderr


def test_dirty_checkout_is_refused(scratch_checkout: Path) -> None:
    # Control: the wrapper must actually be able to fail on a dirty checkout,
    # proving it reaches the shared docker/lib/check-clean.sh logic and does
    # not just always exit 0.
    (scratch_checkout / "agent.py").write_text("# changed\n")
    result = _run_check_clean(scratch_checkout)
    assert result.returncode == 1
    assert "uncommitted changes" in result.stderr


def test_allow_dirty_bypasses_the_refusal(scratch_checkout: Path) -> None:
    (scratch_checkout / "agent.py").write_text("# changed\n")
    result = _run_check_clean(scratch_checkout, allow_dirty="1")
    assert result.returncode == 0, result.stderr


def test_wrapper_forwards_the_shared_script_message() -> None:
    # The wrapper execs docker/lib/check-clean.sh rather than reimplementing
    # it; a message unique to that shared script proves the exec happened.
    not_a_repo_message = "check-clean: is not a git checkout"
    result = _run_check_clean(REPO_ROOT / "docker" / "realtime-control-agent" / "config.example.json")
    assert result.returncode == 1
    assert not_a_repo_message.split(":")[0] in result.stderr


# --- build.sh: archive isolation, committed-content-only, rt_control layout -
#
# build.sh's last step is "docker compose ... build"; these tests stub
# `docker` on PATH so no image is ever built.


@pytest.fixture
def agent_checkout(tmp_path: Path) -> Path:
    """A throwaway git repo shaped like realtime-control-agent: a committed
    rt_control/agent.py at the repository root, no src/ prefix, matching
    what build.sh archives and the Dockerfile copies."""
    repo = tmp_path / "agent-src"
    (repo / "rt_control").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "rt_control" / "agent.py").write_text("# placeholder\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _stub_docker_bin(bin_dir: Path, log_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
  exit 0
fi
if [ "$1" = "compose" ]; then
  printf '%s' "${{DER_AGENT_SRC:-}}" > "{log_dir}/der_agent_src.txt"
  printf '%s' "${{AGENT_REVISION:-}}" > "{log_dir}/agent_revision.txt"
  if [ -f "${{DER_AGENT_SRC:-/nonexistent}}/rt_control/agent.py" ]; then
    echo present > "{log_dir}/agent_py.txt"
  else
    echo absent > "{log_dir}/agent_py.txt"
  fi
  if [ -f "${{DER_AGENT_SRC:-/nonexistent}}/rt_control/leaked-secret.txt" ]; then
    echo present > "{log_dir}/leaked.txt"
  else
    echo absent > "{log_dir}/leaked.txt"
  fi
  exit 0
fi
echo "stub docker: unexpected invocation: $*" >&2
exit 1
""")
    stub.chmod(0o755)


def _run_build_sh(
    tmp_path: Path,
    *,
    cwd: Path,
    der_agent_src: str,
    allow_dirty: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    log_dir = tmp_path / "stub-log"
    log_dir.mkdir()
    bin_dir = tmp_path / "stub-bin"
    _stub_docker_bin(bin_dir, log_dir)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DER_AGENT_SRC"] = der_agent_src
    env.pop("ALLOW_DIRTY", None)
    if allow_dirty is not None:
        env["ALLOW_DIRTY"] = allow_dirty

    result = subprocess.run(
        ["bash", str(BUILD_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        check=False,
    )
    return result, log_dir


def test_build_exports_the_archive_dir_not_the_checkout(agent_checkout: Path, tmp_path: Path) -> None:
    """DER_AGENT_SRC must reach compose as the git-archive export directory,
    never agent_src itself (#68)."""
    caller_cwd = tmp_path / "somewhere-else"
    caller_cwd.mkdir()
    relative = os.path.relpath(agent_checkout, start=caller_cwd)

    result, log_dir = _run_build_sh(tmp_path, cwd=caller_cwd, der_agent_src=relative)
    assert result.returncode == 0, result.stderr

    exported = (log_dir / "der_agent_src.txt").read_text()
    assert Path(exported).is_absolute(), exported
    assert Path(exported) != agent_checkout


def test_build_archives_rt_control_with_no_src_prefix(agent_checkout: Path, tmp_path: Path) -> None:
    # realtime-control-agent's package sits at the repository root
    # (pyproject.toml: packages = [{include = "rt_control"}]), unlike
    # interoperability-service's src/ layout; build.sh must archive
    # "rt_control", not "src/rt_control" (#68).
    result, log_dir = _run_build_sh(tmp_path, cwd=tmp_path, der_agent_src=str(agent_checkout))
    assert result.returncode == 0, result.stderr
    assert (log_dir / "agent_py.txt").read_text().strip() == "present"


def test_build_never_ships_an_uncommitted_file(agent_checkout: Path, tmp_path: Path) -> None:
    (agent_checkout / "rt_control" / "leaked-secret.txt").write_text("JWT_SECRET_KEY=MARKER\n")

    result, log_dir = _run_build_sh(tmp_path, cwd=tmp_path, der_agent_src=str(agent_checkout), allow_dirty="1")
    assert result.returncode == 0, result.stderr
    assert (log_dir / "agent_py.txt").read_text().strip() == "present"
    assert (log_dir / "leaked.txt").read_text().strip() == "absent"


def test_build_label_never_claims_dirty_content_it_does_not_ship(agent_checkout: Path, tmp_path: Path) -> None:
    clean_result, clean_log = _run_build_sh(tmp_path / "clean-run", cwd=tmp_path, der_agent_src=str(agent_checkout))
    assert clean_result.returncode == 0, clean_result.stderr
    clean_revision = (clean_log / "agent_revision.txt").read_text()
    assert not clean_revision.endswith("-dirty"), clean_revision

    (agent_checkout / "rt_control" / "agent.py").write_text("# changed\n")
    dirty_result, dirty_log = _run_build_sh(
        tmp_path / "dirty-run", cwd=tmp_path, der_agent_src=str(agent_checkout), allow_dirty="1"
    )
    assert dirty_result.returncode == 0, dirty_result.stderr
    dirty_revision = (dirty_log / "agent_revision.txt").read_text()
    assert dirty_revision == clean_revision, dirty_revision


# --- docker-compose.yml: DER_AGENT_SRC has no default (#68) -----------------

COMPOSE_FILE = REPO_ROOT / "docker" / "realtime-control-agent" / "docker-compose.yml"


def _run_compose_config(der_agent_src: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if der_agent_src is None:
        env.pop("DER_AGENT_SRC", None)
    else:
        env["DER_AGENT_SRC"] = der_agent_src
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_compose_refuses_to_resolve_without_der_agent_src() -> None:
    result = _run_compose_config(None)
    assert result.returncode != 0
    assert "DER_AGENT_SRC" in result.stderr


def test_compose_resolves_once_der_agent_src_is_set() -> None:
    result = _run_compose_config("unused")
    assert result.returncode == 0, result.stderr
    assert "agent_src:" in result.stdout
