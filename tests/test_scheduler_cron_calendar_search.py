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


def _oracle_get_next(minutes, hours, days_of_month, months, days_of_week, now, steps=None):
    """The pre-#32 minute-by-minute scan, kept as the equivalence oracle.

    ``steps``, when given a one-item list, receives the number of minutes
    the scan walked, so a caller can assert on work done instead of on
    wall-clock time (issue #98: elapsed time depends on the host).
    """
    next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for i in range(1000000):
        if (
            next_time.month in months
            and next_time.day in days_of_month
            and next_time.hour in hours
            and next_time.minute in minutes
            and (next_time.weekday() + 1) % 7 in days_of_week
        ):
            if steps is not None:
                steps[0] = i + 1
            return next_time
        next_time += timedelta(minutes=1)
    if steps is not None:
        steps[0] = 1000000
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
#
# Feb 29 combined with a day-of-week restriction used to belong here too
# (dow=5 waiting to 2036, dow=1 waiting to 2112, the longest gap in the
# 400-year cycle at 40 years): both are now refused at construction instead
# (issue #65, see test_scheduler_cron_window.py), so building either pattern
# here would raise before the oracle ever ran.
RARE_CASES = [
    # No day-of-week filter: the next leap year after 2028 is 2032 (+4, no
    # intervening century skip), whatever weekday Feb 29 falls on that year.
    ("0 0 29 2 *", datetime(2028, 3, 1), datetime(2032, 2, 29, 0, 0)),
]


@pytest.mark.parametrize(("pattern", "start", "expected"), RARE_CASES)
def test_calendar_search_finds_rare_pattern_the_oracle_misses(pattern, start, expected):
    # Control: the oracle walks its full 1,000,000-minute window without a
    # match, proven by a work count rather than elapsed time (issue #98: a
    # fast CI runner cleared the old 0.5s bound in under 0.4s).
    oracle_steps = [0]
    with pytest.raises(ValueError):
        _oracle_get_next(*_components(pattern), start, steps=oracle_steps)
    assert oracle_steps[0] == 1000000

    # The calendar search itself stays bounded by a generous wall-clock
    # ceiling: it never needs to touch the oracle's 1,000,000-minute path,
    # so even a loaded host clears this without the search being fast.
    search_start = time.perf_counter()
    result = CronTimer(pattern).get_next(start)
    assert time.perf_counter() - search_start < 5.0
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


def test_feb29_monday_no_longer_resolves_to_2112_but_is_refused():
    """The 2072-to-2112 case above (the longest gap in the whole 400-year
    cycle, 40 years) used to be a RARE_CASES entry resolving to 2112-02-29;
    it is refused at construction instead (issue #65), and the rejection
    is immediate, not a search out to 2112.
    """
    start = time.perf_counter()
    with pytest.raises(ValueError, match="Feb 29"):
        CronTimer("0 0 29 2 1")
    assert time.perf_counter() - start < 0.1
