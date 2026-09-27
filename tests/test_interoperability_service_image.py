"""Static content checks for docker/interoperability-service (#68).

test_docker_layout.py's generic rules cover compose-file hardening; these
cover the property specific to this agent: the image never pulls in
volttron-core or the agent's own package. Each check is exercised against
a violating string too, so a pass here is not a check that could never fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEROP_DIR = REPO_ROOT / "docker" / "interoperability-service"


def _uncommented_lines(text: str) -> str:
    """Both files explain the volttron-core exclusion in a '#' comment; the
    check below must read the same install/dependency lines pip and docker
    build read, not the prose that names the excluded package on purpose."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def check_no_volttron_core(label: str, text: str) -> None:
    code = _uncommented_lines(text)
    if "volttron-core" in code or "volttron_core" in code:
        raise AssertionError(f"{label}: names volttron-core, which fastlib's shim does not cover")


def test_dockerfile_never_names_volttron_core() -> None:
    check_no_volttron_core("Dockerfile", (INTEROP_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_requirements_never_names_volttron_core() -> None:
    check_no_volttron_core("requirements.txt", (INTEROP_DIR / "requirements.txt").read_text(encoding="utf-8"))


def test_check_no_volttron_core_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="volttron-core"):
        check_no_volttron_core("fixture", "volttron-core>=2.0.0rc30")


def check_never_installs_agent_package(text: str) -> None:
    # The only "pip install" line in the Dockerfile must install exactly the
    # pinned requirements file, nothing appended: a trailing extra argument
    # (a bare "." for the build context) would pull in the agent's own
    # package, and transitively volttron-core, despite the check above.
    for line in text.splitlines():
        if "pip install" not in line:
            continue
        normalized = line.strip().rstrip("\\").strip()
        if not normalized.endswith("pip install -r requirements.txt"):
            raise AssertionError(f"unexpected pip install line: {line!r}")


def test_dockerfile_never_installs_the_agent_package() -> None:
    check_never_installs_agent_package((INTEROP_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_check_never_installs_agent_package_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="pip install line"):
        check_never_installs_agent_package("RUN /app/.venv/bin/pip install .\n")


def test_check_never_installs_agent_package_fixture_fails_on_appended_context() -> None:
    # Control for #68 review M4: "-r requirements.txt" is still present, but
    # a bare "." is appended, which also installs the build context itself.
    with pytest.raises(AssertionError, match="pip install line"):
        check_never_installs_agent_package("RUN /app/.venv/bin/pip install -r requirements.txt .\n")


COPY_FROM_AGENT_SRC = "COPY --from=agent_src --chown=root:root src/interoperability ./interoperability"


def check_copy_from_agent_src_is_scoped(text: str) -> None:
    # The only line copying from the agent_src build context must copy
    # exactly src/interoperability, never the whole context: agent_src is
    # the agent's checkout (or, once archived, its committed tree, #68
    # review F2), and a broader copy would ship whatever else lives there.
    for line in text.splitlines():
        if "--from=agent_src" not in line:
            continue
        if line.strip() != COPY_FROM_AGENT_SRC:
            raise AssertionError(f"unexpected COPY --from=agent_src line: {line!r}")


def test_dockerfile_copy_from_agent_src_is_scoped() -> None:
    check_copy_from_agent_src_is_scoped((INTEROP_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_check_copy_from_agent_src_is_scoped_fixture_fails() -> None:
    # Control for #68 review M5: copying the whole context instead of the
    # named subdirectory.
    with pytest.raises(AssertionError, match="COPY --from=agent_src line"):
        check_copy_from_agent_src_is_scoped("COPY --from=agent_src . ./\n")
