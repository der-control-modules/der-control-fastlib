"""
VOLTTRON scheduling compatibility shim.

Provides: cron, periodic
Maps to: derhost.client.agent.Core.schedule's built-in cron and interval
support (Core.schedule already treats a five-field cron string as a cron
schedule and a plain number as an interval in seconds).
"""

from datetime import datetime, timedelta
from numbers import Number

from derhost.client.agent import CronTimer

__all__ = ["cron", "periodic"]


def cron(schedule: str) -> str:
    """
    Return a cron schedule for derhost's Core.schedule.

    VOLTTRON's cron() parses the expression at call time rather than at
    schedule() time; this does the same by constructing a CronTimer here so
    an invalid pattern fails at the cron() call, not later inside the
    scheduler loop.
    """
    if not isinstance(schedule, str):
        raise TypeError(f"cron() expects a cron expression string, got {type(schedule).__name__}")
    CronTimer(schedule)  # raises ValueError on an invalid pattern
    return schedule


def periodic(period: float | int | timedelta, start: datetime | None = None, count: int | None = None) -> float | int:
    """
    Return the interval, in seconds, for derhost's Core.schedule.

    `start` and `count` are VOLTTRON's delayed-start and fire-count-limit
    options. derhost's scheduler has no equivalent for either, so a caller
    that passes one fails fast here rather than silently getting a schedule
    that never delays its start or never stops repeating.
    """
    if start is not None or count is not None:
        raise NotImplementedError("periodic() start/count are not supported by derhost's scheduler")
    if isinstance(period, timedelta):
        period = period.total_seconds()
    if not isinstance(period, Number) or isinstance(period, bool):
        raise TypeError(f"periodic() expects a number of seconds or a timedelta, got {type(period).__name__}")
    return period
