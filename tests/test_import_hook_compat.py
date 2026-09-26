"""Tests for the VOLTTRON import redirector's package-marking fix (#69).

Covers two properties: an unknown attribute under a leaf redirect (one with
no deeper REDIRECT_MAP entry) still raises ImportError instead of resolving
to a fabricated submodule, and the multi-level imports grid-signals and
realtime-control-agent actually make through this redirector still resolve.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

from derhost.compat.import_hook import VolttronImportRedirector, install_volttron_compatibility

_REDIRECT_MAP = VolttronImportRedirector.REDIRECT_MAP

# base_weather's own shim loads a real VOLTTRON checkout from a hardcoded
# filesystem path and raises FileNotFoundError when none is present; that is
# an environment dependency of the shim itself, unrelated to the __path__
# fix under test here.
_ENV_DEPENDENT_LEAVES = frozenset({"volttron.platform.agent.base_weather"})


def _has_deeper_entry(key: str) -> bool:
    return any(other != key and other.startswith(key + ".") for other in _REDIRECT_MAP)


def _declares_own_path(target: str) -> bool:
    """True when `target`'s own source sets `__path__` at module level."""
    spec = importlib.util.find_spec(target)
    tree = ast.parse(Path(spec.origin).read_text())
    return any(
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__path__" for t in node.targets)
        for node in tree.body
    )


def _leaves_that_lose_the_fallback():
    """REDIRECT_MAP keys with no deeper key, whose own shim doesn't already
    set __path__: import_hook.py's fix stops treating these as packages, so
    an unknown attribute under one should raise ImportError rather than
    resolving via the parent-match fallback as a fabricated submodule."""
    return sorted(
        key
        for key in _REDIRECT_MAP
        if key not in _ENV_DEPENDENT_LEAVES and not _has_deeper_entry(key) and not _declares_own_path(_REDIRECT_MAP[key])
    )


_LEAVES = _leaves_that_lose_the_fallback()


def test_leaf_sweep_is_not_empty():
    """Guards the parametrize below against silently matching nothing."""
    assert len(_LEAVES) >= 10, _LEAVES


@pytest.mark.parametrize("leaf", _LEAVES)
def test_unknown_name_under_leaf_raises_import_error(leaf):
    install_volttron_compatibility()
    with pytest.raises(ImportError):
        exec(f"from {leaf} import __not_a_real_export_xyz__", {})


# The exact multi-level imports grid-signals and realtime-control-agent make
# through this redirector. format_timestamp/get_aware_utc_now is the one
# proven by mutation to depend on "volttron.platform.agent" keeping
# __path__: excluding only that key from the condition above leaves this
# case raising ModuleNotFoundError while every other current test stays green.
_CONSUMER_IMPORTS = [
    pytest.param(
        "from volttron.platform.agent import utils",
        {"utils": lambda v: isinstance(v, type)},
        id="grid-signals-agent-import-utils-module",
    ),
    pytest.param(
        "from volttron.platform.agent.utils import format_timestamp, get_aware_utc_now",
        {"format_timestamp": callable, "get_aware_utc_now": callable},
        id="grid-signals-and-rt-control-agent-utils-functions",
    ),
    pytest.param(
        "from volttron.platform.agent.utils import setup_logging",
        {"setup_logging": callable},
        id="rt-control-agent-utils-setup-logging",
    ),
    pytest.param(
        "from volttron.platform.messaging import topics",
        {"topics": lambda v: isinstance(v, type)},
        id="grid-signals-messaging-topics",
    ),
    pytest.param(
        "from volttron.platform.messaging.health import STATUS_GOOD",
        {"STATUS_GOOD": lambda v: isinstance(v, str)},
        id="grid-signals-messaging-health-status-good",
    ),
    pytest.param(
        "from volttron.platform.vip.agent import Agent, Core, PubSub",
        {
            "Agent": lambda v: isinstance(v, type),
            "Core": lambda v: isinstance(v, type),
            "PubSub": lambda v: isinstance(v, type),
        },
        id="grid-signals-vip-agent-classes",
    ),
    pytest.param(
        "from volttron.platform.vip.agent import Agent, Core, RPC",
        {
            "Agent": lambda v: isinstance(v, type),
            "Core": lambda v: isinstance(v, type),
            "RPC": lambda v: isinstance(v, type),
        },
        id="rt-control-vip-agent-classes",
    ),
    pytest.param(
        "from volttron.platform.scheduling import cron",
        {"cron": callable},
        id="grid-signals-scheduling-cron",
    ),
    pytest.param(
        "from volttron.platform.scheduling import periodic",
        {"periodic": callable},
        id="rt-control-scheduling-periodic",
    ),
    pytest.param(
        "from volttron.platform.jsonrpc import RemoteError",
        {"RemoteError": lambda v: isinstance(v, type) and issubclass(v, Exception)},
        id="grid-signals-jsonrpc-remote-error",
    ),
]


@pytest.mark.parametrize("import_stmt, checks", _CONSUMER_IMPORTS)
def test_consumer_multi_level_import_still_works(import_stmt, checks):
    install_volttron_compatibility()
    namespace: dict = {}
    exec(import_stmt, namespace)
    for name, predicate in checks.items():
        value = namespace.get(name)
        assert value is not None, f"{import_stmt!r} bound {name} to None"
        assert predicate(value), f"{import_stmt!r} bound {name} to an unexpected value: {value!r}"
