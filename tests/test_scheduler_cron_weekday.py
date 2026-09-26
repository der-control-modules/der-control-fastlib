"""Pin CronTimer day-of-week numbering against Python's weekday() offset.

CronTimer._parse_pattern stores 0=Sunday, matching cron convention, but
get_next compared it against datetime.weekday() (0=Monday) without
converting between the two numbering schemes. Every day-of-week rule fired
one day late (issue #31).
"""

from datetime import datetime

import pytest

from derhost.client.agent import CronTimer

# Friday 2026-09-25 09:30, fixed so every case below has a known answer.
FRIDAY = datetime(2026, 9, 25, 9, 30)


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        # Sunday, by cron number (0) and by its alias (7), and by name.
        ("0 9 * * 0", datetime(2026, 9, 27, 9, 0)),
        ("0 9 * * 7", datetime(2026, 9, 27, 9, 0)),
        ("0 9 * * sun", datetime(2026, 9, 27, 9, 0)),
        # Monday, by number and by name.
        ("0 9 * * 1", datetime(2026, 9, 28, 9, 0)),
        ("0 9 * * mon", datetime(2026, 9, 28, 9, 0)),
        # Tuesday, by number and by name.
        ("0 9 * * 2", datetime(2026, 9, 29, 9, 0)),
        ("0 9 * * tue", datetime(2026, 9, 29, 9, 0)),
        # Wednesday, by number and by name.
        ("0 9 * * 3", datetime(2026, 9, 30, 9, 0)),
        ("0 9 * * wed", datetime(2026, 9, 30, 9, 0)),
        # Thursday, by number and by name.
        ("0 9 * * 4", datetime(2026, 10, 1, 9, 0)),
        ("0 9 * * thu", datetime(2026, 10, 1, 9, 0)),
        # Friday, by number and by name. FRIDAY's own weekday, so the next
        # match is the following week, not the reference day itself.
        ("0 9 * * 5", datetime(2026, 10, 2, 9, 0)),
        ("0 9 * * fri", datetime(2026, 10, 2, 9, 0)),
        # Saturday, by number and by name.
        ("0 9 * * 6", datetime(2026, 9, 26, 9, 0)),
        ("0 9 * * sat", datetime(2026, 9, 26, 9, 0)),
    ],
)
def test_get_next_matches_cron_day_of_week(pattern, expected):
    """`get_next` must land on the day the cron pattern names, not one day off."""
    timer = CronTimer(pattern)
    assert timer.get_next(FRIDAY) == expected


def test_get_next_monday_from_friday_2026_09_25():
    """The issue's own reproduction: Monday's rule from a Friday lands on Monday."""
    timer = CronTimer("0 9 * * 1")
    assert timer.get_next(FRIDAY) == datetime(2026, 9, 28, 9, 0)


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        # Range Mon-Fri (1-5): pre-fix compares weekday() (Mon=0) directly
        # against {1..5}, so Saturday (weekday()==5) wrongly matches; the
        # fixed code lands on the following Monday.
        ("0 9 * * 1-5", datetime(2026, 9, 28, 9, 0)),
        # List Sun,Sat (0,6): pre-fix compares weekday() (Mon=0) directly
        # against {0,6}, so Sunday (weekday()==6) wrongly matches; the
        # fixed code lands on the intervening Saturday.
        ("0 9 * * 0,6", datetime(2026, 9, 26, 9, 0)),
    ],
)
def test_get_next_matches_cron_day_of_week_range_and_list(pattern, expected):
    """The range and list forms feed the same days_of_week set the fix corrected."""
    timer = CronTimer(pattern)
    assert timer.get_next(FRIDAY) == expected
