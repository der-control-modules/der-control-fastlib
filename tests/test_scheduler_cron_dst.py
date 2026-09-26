"""CronTimer.next_fire_epoch must give a DST-correct next instant (issue #30).

`compute_next_time` used to get the next cron time from
`get_utc_seconds_from_epoch(CronTimer.get_next(...))`, which maps a naive
wall time with `replace(tzinfo=...)` and always picks the pre-transition
offset. During a fall-back's repeated hour that reschedules the event
about 3540 s in the past, which spins the scheduler loop without
yielding. `next_fire_epoch` resolves each candidate wall time with PEP
495 fold instead.
"""

import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from derhost.client.agent import CronTimer, ScheduledEvent, get_utc_seconds_from_epoch

# 2026 US DST transitions: spring forward on 2026-03-08, fall back on
# 2026-11-01. Both are exercised below under America/New_York.
_FALL_BACK_DAY = datetime(2026, 11, 1)
_SPRING_FORWARD_DAY = datetime(2026, 3, 8)


def _utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


@contextmanager
def _tz(name):
    """Run under the named zone, restoring TZ and the C tzset() rules after.

    monkeypatch's own teardown runs after a dependent fixture's teardown,
    so a fixture that calls monkeypatch.setenv("TZ", ...) and then
    time.tzset() in its own teardown still restores the OLD zone's rules,
    not the original: monkeypatch has not reverted the env var yet. That
    left every later test in the session running under the last zone set
    here. Managing TZ and tzset() together, in one place, closes that gap.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


@pytest.fixture
def eastern_time():
    """Run the test under America/New_York, restored afterward."""
    with _tz("America/New_York"):
        yield


def test_next_fire_epoch_every_minute_is_one_minute_later(eastern_time):
    after = _utc(2026, 11, 1, 6, 10)
    assert CronTimer("* * * * *").next_fire_epoch(after) == _utc(2026, 11, 1, 6, 11)


def test_next_fire_epoch_hourly_fires_on_both_fall_back_occurrences(eastern_time):
    """An hour field of "*" covers both the fold-0 and fold-1 1 a.m."""
    timer = CronTimer("0 * * * *")
    first = timer.next_fire_epoch(_utc(2026, 11, 1, 4, 30))
    second = timer.next_fire_epoch(first)
    third = timer.next_fire_epoch(second)
    assert (first, second, third) == (
        _utc(2026, 11, 1, 5, 0),
        _utc(2026, 11, 1, 6, 0),
        _utc(2026, 11, 1, 7, 0),
    )


def test_next_fire_epoch_fixed_hour_fires_once_on_fall_back_day(eastern_time):
    """A restricted hour field fires once, not on both fold occurrences."""
    timer = CronTimer("30 1 * * *")
    first = timer.next_fire_epoch(_utc(2026, 11, 1, 4, 0))
    assert first == _utc(2026, 11, 1, 5, 30)

    second = timer.next_fire_epoch(first)
    assert second != _utc(2026, 11, 1, 6, 30), "fold-1 1:30 must not fire for a fixed-hour pattern"
    assert second == _utc(2026, 11, 2, 6, 30)


def test_next_fire_epoch_23_of_24_hours_excludes_fold1_but_24_of_24_includes_it(eastern_time):
    """The fold-1 duplicate needs every one of the 24 hours, not merely most
    of them: an hour field covering 23 of 24 hours excludes it exactly like
    a single fixed hour does, and only all 24 hours includes it.
    """
    almost_every_hour = CronTimer("30 0-22 * * *")
    first = almost_every_hour.next_fire_epoch(_utc(2026, 11, 1, 5, 0))
    assert first == _utc(2026, 11, 1, 5, 30)
    second = almost_every_hour.next_fire_epoch(first)
    assert second != _utc(2026, 11, 1, 6, 30), "fold-1 1:30 must not fire when hour 23 is excluded"
    assert second == _utc(2026, 11, 1, 7, 30)

    every_hour = CronTimer("30 * * * *")
    first_every = every_hour.next_fire_epoch(_utc(2026, 11, 1, 5, 0))
    second_every = every_hour.next_fire_epoch(first_every)
    assert (first_every, second_every) == (
        _utc(2026, 11, 1, 5, 30),
        _utc(2026, 11, 1, 6, 30),
    )


def test_next_fire_epoch_spring_forward_gap_maps_after_the_gap(eastern_time):
    """2:30 a.m. does not exist on the gap day; it fires once, after the gap."""
    timer = CronTimer("30 2 * * *")
    assert timer.next_fire_epoch(_utc(2026, 3, 8, 4, 0)) == _utc(2026, 3, 8, 7, 30)


@pytest.mark.parametrize(
    "pattern", ["* * * * *", "0 * * * *", "30 1 * * *", "30 2 * * *", "*/15 * * * *"]
)
@pytest.mark.parametrize("transition_day", [_SPRING_FORWARD_DAY, _FALL_BACK_DAY])
def test_next_fire_epoch_always_after_across_both_transitions(eastern_time, pattern, transition_day):
    """Every minute across both 2026 transition days: next must be > after."""
    timer = CronTimer(pattern)
    for minute in range(0, 24 * 60, 5):
        after = (transition_day + timedelta(minutes=minute)).replace(tzinfo=timezone.utc).timestamp()
        assert timer.next_fire_epoch(after) > after


def test_next_fire_epoch_stops_at_first_match_on_an_ordinary_day(eastern_time):
    """On a day without an offset change, `next_fire_epoch` must stop at the
    first qualifying wall time instead of scanning the whole day: the full
    scan measured about 5.5 ms/call for "* * * * *" (1440 candidates a
    call); the fix measures about 0.33 ms/call. The bound below is 1.5 ms,
    roughly 4x the measured fix cost and under a third of the old cost, to
    absorb CI variance without letting the full scan back in.
    """
    timer = CronTimer("* * * * *")
    after = _utc(2026, 6, 15, 12, 0)  # an ordinary day: no DST transition
    calls = 200

    start = time.perf_counter()
    for _ in range(calls):
        after = timer.next_fire_epoch(after)
    elapsed = time.perf_counter() - start

    per_call = elapsed / calls
    assert per_call < 0.0015, f"{per_call * 1e6:.1f} us/call, expected under 1500 us/call"


def test_compute_next_time_fall_back_no_longer_reschedules_into_the_past(eastern_time, monkeypatch):
    """Reproduces issue #30's probe: 06:10Z is 01:10 EST (fold 1). The old
    get_utc_seconds_from_epoch path rescheduled ~3540 s in the past; the
    fix gives the same +60 s a UTC host gets.
    """
    fixed_now = _utc(2026, 11, 1, 6, 10)
    monkeypatch.setattr(time, "time", lambda: fixed_now)

    event = ScheduledEvent(lambda: None, "* * * * *")
    event.compute_next_time()

    assert event.next_time - fixed_now == pytest.approx(60.0)

    # Control: the path this PR removes gives the documented -3540 s.
    old_next_wall = event.cron_timer.get_next(datetime.fromtimestamp(fixed_now))
    old_epoch = get_utc_seconds_from_epoch(old_next_wall)
    assert old_epoch - fixed_now == pytest.approx(-3540.0)


def test_cron_under_utc_is_unchanged(monkeypatch):
    """Cron under a zone without DST gives the same instants as before."""
    with _tz("UTC"):
        fixed_now = _utc(2026, 6, 15, 12, 0)
        monkeypatch.setattr(time, "time", lambda: fixed_now)

        event = ScheduledEvent(lambda: None, "* * * * *")
        event.compute_next_time()

        assert event.next_time - fixed_now == pytest.approx(60.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
