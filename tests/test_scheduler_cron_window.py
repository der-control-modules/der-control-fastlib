"""Pin the shrunk CronTimer search window and the Feb-29-plus-weekday
refusal (issue #65).

`CronTimer.get_next` searched up to 400 years ahead, far past what any
accepted pattern needs. Every pattern except Feb 29 combined with a
day-of-week restriction has a same-weekday recurrence gap of at most 4382
days (about 12 years, short of a full 12 due to a non-leap century in the
gap), computed over every (month, day) singleton crossed with every single
weekday across two full 400-year Gregorian cycles: a larger
day-of-month/month/day-of-week set can only add candidate match dates, so
singletons bound every pattern's worst case. Feb 29 combined with a
day-of-week restriction can wait up to 40 years, past the shrunk window, so
it is refused at construction instead of searched from a fixed start date
(the trap: a fixed-start search can pass at construction and still exceed
the window from a different start).
"""

from datetime import datetime, timedelta

import pytest

from derhost.client.agent import CronTimer


def test_search_window_is_thirteen_years():
    """13 is the smallest whole-year window that covers every accepted
    pattern's worst-case gap (the Jan-1-on-Monday case below needs exactly
    12 years of headroom past its start year; 12 whole years is not enough,
    verified separately against the real search loop).
    """
    assert CronTimer._SEARCH_WINDOW_YEARS == 13


@pytest.mark.parametrize(
    "dow",
    ["0", "1", "5", "1,3", "1-5", "0-5"],
)
def test_feb29_with_weekday_restriction_refused(dow):
    """Feb 29 with any day-of-week restriction, single, list, range, or the
    six-day boundary one short of a full week, is refused at construction:
    its wait can exceed the window regardless of which weekday(s) are named.
    """
    with pytest.raises(ValueError, match="Feb 29"):
        CronTimer(f"0 0 29 2 {dow}")


@pytest.mark.parametrize("dow", ["0-6", "*"])
def test_feb29_with_full_week_accepted(dow):
    """Control at the boundary above: naming all 7 weekdays is the same as
    no day-of-week restriction, so it is not refused.
    """
    CronTimer(f"0 0 29 2 {dow}")


def test_feb29_without_weekday_restriction_still_accepted():
    """Control: plain Feb 29 (no day-of-week restriction) is not refused;
    its own worst-case gap (8 years) fits the window.
    """
    CronTimer("0 0 29 2 *")


def test_plain_feb29_from_2097_returns_2104():
    """The longest gap a plain (unrestricted) Feb 29 can wait: 2100 is not
    a leap year (divisible by 100, not by 400), so the next Feb 29 after
    2096 is 2104, an 8-year gap.
    """
    timer = CronTimer("0 0 29 2 *")
    assert timer.get_next(datetime(2097, 3, 1)) == datetime(2104, 2, 29, 0, 0)


def test_jan1_sunday_from_2023_returns_2034():
    """Jan 1 restricted to Sunday: 2023-01-01 and 2034-01-01 are the only
    Sundays landing on Jan 1 in that span (checked directly against
    date.weekday() for every year 2023-2035), an 11-year gap.
    """
    timer = CronTimer("0 0 1 1 0")
    assert timer.get_next(datetime(2023, 1, 2)) == datetime(2034, 1, 1, 0, 0)


# Patterns representative of the shapes the window must cover: a date plus a
# single weekday at its own worst alignment, a weekday range, a multi-value
# day-of-month including Feb 29 alongside another day, a month range plus a
# weekday, and plain unrestricted Feb 29.
ACCEPTED_PATTERNS = [
    "0 0 1 1 1",  # Jan 1 restricted to Monday: the global worst-case pattern
    "0 9 * * 1-5",  # weekday range, common and low-risk
    "0 0 15,29 2 1",  # Feb 15 or 29, Monday: Feb 15 bounds the wait
    "0 0 1 1-3 1",  # day 1 of Jan-Mar, Monday: a month range plus a weekday
    "0 0 29 2 *",  # plain Feb 29, unrestricted
]


@pytest.mark.parametrize("pattern", ACCEPTED_PATTERNS)
def test_accepted_pattern_never_fails_within_window(pattern):
    """Every pattern accepted at construction must resolve `get_next` for
    a start anywhere in a full 400-year cycle, never raising, and the
    returned date must actually satisfy the pattern's own month,
    day-of-month, and day-of-week sets rather than merely being later than
    the start. Start dates are spread across the cycle (a
    non-divisor-of-7 step keeps them off a fixed weekday alignment) so
    this is not just testing one lucky start.
    """
    timer = CronTimer(pattern)
    for year in range(2000, 2400, 11):
        for day_of_year_step in (1, 100, 200, 300):
            start = datetime(year, 1, 1) + timedelta(days=day_of_year_step)
            result = timer.get_next(start)
            assert result > start
            assert result.month in timer.months
            assert result.day in timer.days_of_month
            # weekday() is 0=Monday; days_of_week is stored in cron's
            # 0=Sunday numbering, so shift before comparing.
            assert (result.weekday() + 1) % 7 in timer.days_of_week
