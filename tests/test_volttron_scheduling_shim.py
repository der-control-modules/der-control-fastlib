"""Tests for cron and periodic in the VOLTTRON scheduling compatibility shim."""

import time
from datetime import datetime, timedelta

import gevent
import pytest

from derhost.client.agent import CronTimer
from derhost.compat.import_hook import install_volttron_compatibility
from derhost.compat.shims.scheduling import cron as shim_cron, periodic as shim_periodic


def _import_compat():
    install_volttron_compatibility()
    from volttron.platform.scheduling import cron, periodic
    from volttron.platform.vip.agent import Agent

    return Agent, cron, periodic


def test_periodic_fires_on_schedule(message_bus_manager_fixture):
    """periodic() from the shim schedules a repeating interval that fires."""
    Agent, _cron, periodic = _import_compat()

    manager = message_bus_manager_fixture
    manager.start_bus()
    agent = manager.create_agent("test.scheduling.periodic", agent_class=Agent)
    agent.connect()

    fire_times = []
    agent.core.schedule(periodic(0.2), lambda: fire_times.append(time.time()))

    deadline = time.time() + 3
    while len(fire_times) < 3 and time.time() < deadline:
        gevent.sleep(0.1)

    assert len(fire_times) >= 3, f"expected periodic to fire at least 3 times, got {fire_times}"

    agent.disconnect()


def test_cron_fires_on_schedule(monkeypatch, message_bus_manager_fixture):
    """cron() from the shim schedules a cron event that fires."""
    Agent, cron, _periodic = _import_compat()

    # derhost's CronTimer has minute granularity, so a real 5-field cron
    # expression fires no more than once a minute. Patch get_next so the
    # test observes repeated firing within a bounded wait instead of the
    # wall clock.
    def fast_next(self, now=None):
        return datetime.now() + timedelta(milliseconds=100)

    monkeypatch.setattr(CronTimer, "get_next", fast_next)

    manager = message_bus_manager_fixture
    manager.start_bus()
    agent = manager.create_agent("test.scheduling.cron", agent_class=Agent)
    agent.connect()

    fire_times = []
    agent.core.schedule(cron("* * * * *"), lambda: fire_times.append(time.time()))

    deadline = time.time() + 3
    while len(fire_times) < 2 and time.time() < deadline:
        gevent.sleep(0.1)

    assert len(fire_times) >= 2, f"expected cron to fire at least twice, got {fire_times}"

    agent.disconnect()


def test_unknown_name_raises_import_error():
    """An unknown name from the scheduling module still raises ImportError."""
    install_volttron_compatibility()

    with pytest.raises(ImportError):
        exec("from volttron.platform.scheduling import not_a_real_export", {})


def test_cron_rejects_non_string_schedule():
    """cron() type-checks before constructing a CronTimer (scheduling.py:27-28)."""
    with pytest.raises(TypeError):
        shim_cron(60)


def test_periodic_rejects_start():
    """derhost's scheduler has no delayed-start equivalent, so a start value fails fast."""
    with pytest.raises(NotImplementedError):
        shim_periodic(1.0, start=datetime.now())


def test_periodic_rejects_count():
    """derhost's scheduler has no fire-count-limit equivalent, so a count value fails fast."""
    with pytest.raises(NotImplementedError):
        shim_periodic(1.0, count=5)


def test_periodic_rejects_non_number_period():
    """periodic() type-checks the period once start/count are ruled out."""
    with pytest.raises(TypeError):
        shim_periodic("not-a-number")


def test_periodic_converts_timedelta_to_seconds():
    """periodic() converts a timedelta period to the seconds Core.schedule expects."""
    assert shim_periodic(timedelta(minutes=2)) == 120.0
