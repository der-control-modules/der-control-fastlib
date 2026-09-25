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
