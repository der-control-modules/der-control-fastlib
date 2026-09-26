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
    # A near-term leap day: the next Feb 29 after 2027 is 2028 (2028 = 2027 + 1,
    # a plain 4-year leap year, no century skip involved).
    ("0 0 29 2 *", datetime(2027, 1, 1)),
    # Day-of-month values that do not exist in every month: April has 30 days
    # (no 31st), so day 31 lands in May; February has at most 29 (no 30th), so
    # day 30 lands in March.
    ("0 0 31 * *", datetime(2026, 4, 1)),
    ("0 0 30 * *", datetime(2027, 2, 1)),
    # Day-of-month AND day-of-week both restricted: the classic Friday-the-13th.
    ("0 9 13 * 5", datetime(2026, 1, 1)),
    # Start at 23:59 on the last day of a month: the +1-minute step must cross
    # both midnight and the month boundary before the search begins.
    ("* * * * *", datetime(2026, 1, 31, 23, 59)),
]

# Patterns rare enough that the oracle's 1,000,000-minute window (under two
# years) cannot reach the answer; the calendar search resolves them directly.
# Each expected date carries how it was derived, so a future maintainer can
# recheck it without re-running the search.
RARE_CASES = [
    # No day-of-week filter: the next leap year after 2028 is 2032 (+4, no
    # intervening century skip), whatever weekday Feb 29 falls on that year.
    ("0 0 29 2 *", datetime(2028, 3, 1), datetime(2032, 2, 29, 0, 0)),
    # Filtered to Friday (cron dow=5): 2032-02-29 is a Sunday, so the next
    # Friday Feb 29 is 2036 (both confirmed via `date(year, 2, 29).weekday()`).
    ("0 0 29 2 5", datetime(2028, 3, 1), datetime(2036, 2, 29, 0, 0)),
    # Filtered to Monday (cron dow=1): 2072-02-29 and 2112-02-29 are both
    # Mondays. This is the longest gap (40 years) between same-weekday Feb 29s
    # anywhere in the 400-year Gregorian cycle, found by checking every
    # leap-year Feb 29 across two full cycles (800 years) and taking the
    # largest year-to-year gap per weekday. A search bound of 41 years already
    # finds this exact match; the shipped 400-year bound is far more generous
    # than any satisfiable pattern needs.
    ("0 0 29 2 1", datetime(2072, 3, 1), datetime(2112, 2, 29, 0, 0)),
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
