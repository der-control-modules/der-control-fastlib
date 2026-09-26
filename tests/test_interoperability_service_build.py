"""docker/interoperability-service/check-clean.sh: refuse a dirty agent
checkout unless ALLOW_DIRTY=1 (#68)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_CLEAN = REPO_ROOT / "docker" / "interoperability-service" / "check-clean.sh"


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
