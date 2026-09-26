"""Pin CronTimer.get_next's bounded calendar search against the minute scan
it replaces (issue #32).

The pre-fix `get_next` stepped minute by minute for up to 1,000,000 minutes
(under two years) and raised ValueError when nothing matched, which blocked
the gevent hub for a fraction of a second and lost a valid but rare pattern
such as day 29 of February. The calendar search below walks months, then
days, then the first matching hour and minute, bounded by an exact 400-year
Gregorian cycle, and rejects a pattern that can never match at construction.
"""

import time
from datetime import datetime, timedelta

import pytest

from derhost.client.agent import CronTimer


def _oracle_get_next(minutes, hours, days_of_month, months, days_of_week, now):
    """The pre-#32 minute-by-minute scan, kept as the equivalence oracle."""
    next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(1000000):
        if (
            next_time.month in months
            and next_time.day in days_of_month
            and next_time.hour in hours
            and next_time.minute in minutes
            and (next_time.weekday() + 1) % 7 in days_of_week
        ):
            return next_time
        next_time += timedelta(minutes=1)
    raise ValueError("Could not find next scheduled time within reasonable limits")


def _components(pattern):
    timer = CronTimer(pattern)
    return timer.minutes, timer.hours, timer.days_of_month, timer.months, timer.days_of_week


# Patterns the oracle CAN resolve within its 1,000,000-minute window, used
# to check the two searches agree on the ordinary path.
AGREEING_CASES = [
    ("* * * * *", datetime(2026, 1, 1)),
    ("0 * * * *", datetime(2026, 1, 1, 5, 30)),
    ("30 1 * * *", datetime(2026, 1, 1)),
    ("0 0 1 1 *", datetime(2026, 6, 15)),
    ("0 0 15 6 1-5", datetime(2026, 1, 1)),
    ("*/15 8-17 * * 1-5", datetime(2026, 1, 1)),
]

# Patterns rare enough that the oracle's 1,000,000-minute window (under two
# years) cannot reach the answer; the calendar search resolves them directly.
RARE_CASES = [
    ("0 0 29 2 *", datetime(2028, 3, 1), datetime(2032, 2, 29, 0, 0)),
    ("0 0 29 2 5", datetime(2028, 3, 1), datetime(2036, 2, 29, 0, 0)),
]


@pytest.mark.parametrize(("pattern", "start", "expected"), RARE_CASES)
def test_calendar_search_finds_rare_pattern_the_oracle_misses(pattern, start, expected):
    # Control: the oracle cannot find the match within its window and raises
    # after a real, measurable delay.
    oracle_start = time.perf_counter()
    with pytest.raises(ValueError):
        _oracle_get_next(*_components(pattern), start)
    assert time.perf_counter() - oracle_start > 0.5

    search_start = time.perf_counter()
    result = CronTimer(pattern).get_next(start)
    assert time.perf_counter() - search_start < 0.1
    assert result == expected
    assert result.tzinfo is None


@pytest.mark.parametrize(("pattern", "start"), AGREEING_CASES)
def test_calendar_search_matches_oracle_on_resolvable_patterns(pattern, start):
    expected = _oracle_get_next(*_components(pattern), start)
    actual = CronTimer(pattern).get_next(start)
    assert actual == expected
    assert actual.tzinfo is None


def test_never_matching_pattern_rejected_at_construction():
    start = time.perf_counter()
    with pytest.raises(ValueError):
        CronTimer("0 0 31 2 *")
    assert time.perf_counter() - start < 0.1
