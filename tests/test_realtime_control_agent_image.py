"""Static content checks for docker/realtime-control-agent (#68).

test_docker_layout.py's generic rules cover compose-file hardening; these
cover the property specific to this agent: the image never pulls in
volttron, volttron-core or julia, and never installs the agent's own
package. Each check is exercised against a violating string too, so a pass
here is not a check that could never fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "docker" / "realtime-control-agent"


def _uncommented_lines(text: str) -> str:
    """The requirements file and Dockerfile both explain the exclusion in a
    '#' comment; the check below must read the same install/dependency
    lines pip and docker build read, not the prose naming what is excluded
    on purpose."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


_EXCLUDED_PACKAGE_MARKERS = ("volttron-core", "volttron_core", "volttron", "julia")


def check_no_excluded_packages(label: str, text: str) -> None:
    code = _uncommented_lines(text)
    for marker in _EXCLUDED_PACKAGE_MARKERS:
        if marker in code:
            raise AssertionError(f"{label}: names {marker}, which this native-modes-only image must not install")


def test_dockerfile_never_names_excluded_packages() -> None:
    check_no_excluded_packages("Dockerfile", (AGENT_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_requirements_never_names_excluded_packages() -> None:
    check_no_excluded_packages("requirements.txt", (AGENT_DIR / "requirements.txt").read_text(encoding="utf-8"))


def test_check_no_excluded_packages_fixture_fails_volttron_core() -> None:
    with pytest.raises(AssertionError, match="volttron-core"):
        check_no_excluded_packages("fixture", "volttron-core>=2.0.0rc30")


def test_check_no_excluded_packages_fixture_fails_volttron() -> None:
    with pytest.raises(AssertionError, match="volttron"):
        check_no_excluded_packages("fixture", "volttron>=11.0.0rc1")


def test_check_no_excluded_packages_fixture_fails_julia() -> None:
    with pytest.raises(AssertionError, match="julia"):
        check_no_excluded_packages("fixture", "julia>=0.6.2")


def check_never_installs_agent_package(text: str) -> None:
    # The only "pip install" line in the Dockerfile must install exactly the
    # pinned requirements file, nothing appended: a trailing extra argument
    # (a bare "." for the build context) would pull in the agent's own
    # package, and transitively volttron/julia, despite the check above.
    for line in text.splitlines():
        if "pip install" not in line:
            continue
        normalized = line.strip().rstrip("\\").strip()
        if not normalized.endswith("pip install -r requirements.txt"):
            raise AssertionError(f"unexpected pip install line: {line!r}")


def test_dockerfile_never_installs_the_agent_package() -> None:
    check_never_installs_agent_package((AGENT_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_check_never_installs_agent_package_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="pip install line"):
        check_never_installs_agent_package("RUN /app/.venv/bin/pip install .\n")


def test_check_never_installs_agent_package_fixture_fails_on_appended_context() -> None:
    # Control: "-r requirements.txt" is still present, but a bare "." is
    # appended too, which also installs the build context itself (#68).
    with pytest.raises(AssertionError, match="pip install line"):
        check_never_installs_agent_package("RUN /app/.venv/bin/pip install -r requirements.txt .\n")


COPY_FROM_AGENT_SRC = "COPY --from=agent_src --chown=root:root rt_control ./rt_control"


def check_copy_from_agent_src_is_scoped(text: str) -> None:
    # The only line copying from the agent_src build context must copy
    # exactly rt_control, never the whole context: agent_src is the agent's
    # checkout (or, once archived, its committed tree), and a broader copy
    # would ship whatever else lives there (#68).
    for line in text.splitlines():
        if "--from=agent_src" not in line:
            continue
        if line.strip() != COPY_FROM_AGENT_SRC:
            raise AssertionError(f"unexpected COPY --from=agent_src line: {line!r}")


def test_dockerfile_copy_from_agent_src_is_scoped() -> None:
    check_copy_from_agent_src_is_scoped((AGENT_DIR / "Dockerfile").read_text(encoding="utf-8"))


def test_check_copy_from_agent_src_is_scoped_fixture_fails() -> None:
    # Control: copying the whole context instead of the named subdirectory (#68).
    with pytest.raises(AssertionError, match="COPY --from=agent_src line"):
        check_copy_from_agent_src_is_scoped("COPY --from=agent_src . ./\n")


def test_config_example_sets_no_mode_to_use_julia() -> None:
    # The baked-in config must not itself flip a mode into the Julia path
    # this image has no interpreter for (#68).
    text = (AGENT_DIR / "config.example.json").read_text(encoding="utf-8")
    assert "use_julia" not in text, text
    assert "ctrl_eval_engine_app_path" not in text, text


def test_config_example_use_cases_is_empty() -> None:
    # The agent's own shipped configs (sample_config.json, es_control_test.json)
    # all configure a PeakLimiting use case that omits realtime_power_point,
    # a required constructor argument with no default
    # (rt_control/use_cases/peak_limiting.py:7 at 16804d8): loading one of
    # those configs raises inside configure_main before the periodic control
    # loop is ever scheduled. Left empty here rather than reproducing that
    # defect; not a change to the agent's own source.
    config = json.loads((AGENT_DIR / "config.example.json").read_text(encoding="utf-8"))
    assert config["use_cases"] == []


def test_config_example_modes_is_empty() -> None:
    # Every mode class in rt_control.modes.active/reactive/emergency calls
    # importlib.metadata.version('volttron') at module import time with no
    # PackageNotFoundError guard (e.g. active_power_response.py imports
    # active/__init__.py, which imports active_power_limit.py:4 at 16804d8),
    # so the whole subpackage fails to import wherever volttron itself is
    # not pip-installed, which this image deliberately never does. Every
    # class in rt_control.modes.novel imports the julia package
    # unconditionally at module scope (es_control_mode.py:2 at 16804d8), so
    # that subpackage fails to import in this image too, by the operator's
    # own no-Julia decision. No configured mode is currently reachable in
    # this container; left empty rather than reproducing either defect.
    config = json.loads((AGENT_DIR / "config.example.json").read_text(encoding="utf-8"))
    assert config["modes"] == []


def test_dockerfile_bakes_config_at_the_path_start_legacy_resolves() -> None:
    # start-legacy.py resolves --config relative to --agent-dir first
    # (rt_control/agent.py:191 at 16804d8 calls vip_main(RTControlAgent), so
    # RTControlAgent's config_path comes from that flag): the CMD must pass
    # "config.json" and the Dockerfile must copy the example to that exact
    # name under /agent, or the container starts with no config at all.
    dockerfile = (AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chown=root:root config.example.json ./config.json" in dockerfile, dockerfile
    assert '"--config", "config.json"' in dockerfile, dockerfile


def test_dockerfile_names_the_der_rtc_identity() -> None:
    # The agent's own README installs it as der.rtc; the container's CMD
    # must register the same identity (#68).
    dockerfile = (AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert '"--identity", "der.rtc"' in dockerfile, dockerfile


def test_dockerfile_cmd_enables_debug_logging() -> None:
    # The periodic control tick (rt_control/agent.py's loop()) and the
    # scheduler's own per-event trace both log at DEBUG only
    # (src/derhost/client/agent.py:3258 at b748a1f); without --debug,
    # start-legacy.py's setup_logging() leaves the root logger at INFO and
    # no tick ever appears in the container's log (#68).
    dockerfile = (AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert '"--debug"' in dockerfile, dockerfile
