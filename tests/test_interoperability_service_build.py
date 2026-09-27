"""docker/interoperability-service/check-clean.sh: refuse a dirty agent
checkout unless ALLOW_DIRTY=1 (#68)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_CLEAN = REPO_ROOT / "docker" / "interoperability-service" / "check-clean.sh"
BUILD_SH = REPO_ROOT / "docker" / "interoperability-service" / "build.sh"


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
    # Control: the check must actually be able to fail on a dirty checkout.
    (scratch_checkout / "agent.py").write_text("# changed\n")
    result = _run_check_clean(scratch_checkout)
    assert result.returncode == 1
    assert "uncommitted changes" in result.stderr


def test_allow_dirty_bypasses_the_refusal(scratch_checkout: Path) -> None:
    (scratch_checkout / "agent.py").write_text("# changed\n")
    result = _run_check_clean(scratch_checkout, allow_dirty="1")
    assert result.returncode == 0, result.stderr


def test_non_git_checkout_is_refused(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    result = _run_check_clean(not_a_repo)
    assert result.returncode == 1
    assert "not a git checkout" in result.stderr


# --- build.sh: canonicalization, committed-content-only, dirty labeling ----
#
# build.sh's last step is "docker compose ... build"; these tests stub
# `docker` on PATH so no image is ever built. The stub still runs for real
# for "image inspect" (answers yes, so build.sh never tries the base image)
# and, for "compose", records what build.sh actually resolved and exported
# before it would have handed off to the real build: DER_AGENT_SRC,
# AGENT_REVISION, and whether two known files are present in the directory
# build.sh exported as DER_AGENT_SRC. That directory is a mktemp -d build.sh
# removes via its own EXIT trap, so the check has to happen here, inside the
# stub, while build.sh is still running.


@pytest.fixture
def agent_checkout(tmp_path: Path) -> Path:
    """A throwaway git repo shaped like interoperability-service: a
    committed src/interoperability/agent.py, matching what build.sh
    archives and the Dockerfile copies."""
    repo = tmp_path / "agent-src"
    (repo / "src" / "interoperability").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "src" / "interoperability" / "agent.py").write_text("# placeholder\n")
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
  if [ -f "${{DER_AGENT_SRC:-/nonexistent}}/src/interoperability/agent.py" ]; then
    echo present > "{log_dir}/agent_py.txt"
  else
    echo absent > "{log_dir}/agent_py.txt"
  fi
  if [ -f "${{DER_AGENT_SRC:-/nonexistent}}/src/interoperability/leaked-secret.txt" ]; then
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


def test_build_canonicalizes_a_relative_der_agent_src(agent_checkout: Path, tmp_path: Path) -> None:
    """A relative DER_AGENT_SRC, resolved from an arbitrary caller cwd, must
    reach compose as the same absolute directory check-clean.sh validated:
    compose resolves a relative path against build.sh's own directory, not
    the caller's cwd, so the two could otherwise disagree (#68)."""
    caller_cwd = tmp_path / "somewhere-else"
    caller_cwd.mkdir()
    relative = os.path.relpath(agent_checkout, start=caller_cwd)

    result, log_dir = _run_build_sh(tmp_path, cwd=caller_cwd, der_agent_src=relative)
    assert result.returncode == 0, result.stderr

    exported = (log_dir / "der_agent_src.txt").read_text()
    assert Path(exported).is_absolute(), exported
    # It must not be agent_checkout itself: build.sh exports the git-archive
    # export directory (item 2), never the raw checkout.
    assert Path(exported) != agent_checkout


def test_build_never_ships_an_uncommitted_file(agent_checkout: Path, tmp_path: Path) -> None:
    (agent_checkout / "src" / "interoperability" / "leaked-secret.txt").write_text("JWT_SECRET_KEY=MARKER\n")

    result, log_dir = _run_build_sh(tmp_path, cwd=tmp_path, der_agent_src=str(agent_checkout), allow_dirty="1")
    assert result.returncode == 0, result.stderr
    assert (log_dir / "agent_py.txt").read_text().strip() == "present"
    assert (log_dir / "leaked.txt").read_text().strip() == "absent"


def test_build_labels_a_dirty_build_distinctly(agent_checkout: Path, tmp_path: Path) -> None:
    clean_result, clean_log = _run_build_sh(tmp_path / "clean-run", cwd=tmp_path, der_agent_src=str(agent_checkout))
    assert clean_result.returncode == 0, clean_result.stderr
    clean_revision = (clean_log / "agent_revision.txt").read_text()
    assert not clean_revision.endswith("-dirty"), clean_revision

    (agent_checkout / "src" / "interoperability" / "agent.py").write_text("# changed\n")
    dirty_result, dirty_log = _run_build_sh(
        tmp_path / "dirty-run", cwd=tmp_path, der_agent_src=str(agent_checkout), allow_dirty="1"
    )
    assert dirty_result.returncode == 0, dirty_result.stderr
    dirty_revision = (dirty_log / "agent_revision.txt").read_text()
    assert dirty_revision == f"{clean_revision}-dirty", dirty_revision
