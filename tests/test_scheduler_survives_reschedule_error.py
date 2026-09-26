"""The scheduler loop must survive a failing reschedule (issue #32).

`compute_next_time()` used to run outside any try/except in
`_scheduler_loop`. A cron pattern whose `CronTimer.get_next` raises (no
match in its search window, or any other defect) ended the scheduler
greenlet: every other scheduled event, cron or interval, stopped firing.
"""

import logging
import time

import gevent
import pytest

from derhost.client.agent import Scheduler


class _FakeAgent:
    """Stand-in for Agent: Scheduler only reads `identity` for logging."""

    identity = "test.scheduler.survives"


def test_loop_survives_failing_reschedule(caplog):
    """A cron event whose reschedule fails is disabled; the loop keeps going."""
    scheduler = Scheduler(_FakeAgent())
    interval_calls = []
    cron_calls = []

    scheduler.schedule(lambda: interval_calls.append(time.time()), 0.03, name="interval_event")
    scheduler.schedule(lambda: cron_calls.append(time.time()), "* * * * *", name="cron_event")

    cron_event = scheduler._events["cron_event"]

    def raise_on_reschedule(*_args, **_kwargs):
        raise ValueError("no match within search window")

    # The constructor's own get_next() call already ran (unpatched) above;
    # this replaces the call compute_next_time() makes when the event fires.
    cron_event.cron_timer.get_next = raise_on_reschedule
    # Make the cron event due immediately rather than waiting for a real
    # minute boundary; _rebuild_queue keeps the heap consistent with the
    # mutated next_time (see Scheduler._rebuild_queue).
    cron_event.next_time = time.time()
    scheduler._rebuild_queue()

    try:
        with caplog.at_level(logging.ERROR):
            gevent.sleep(0.3)

        assert not scheduler._scheduler_greenlet.dead, "scheduler greenlet must survive the failure"
        assert len(interval_calls) >= 3, "interval event must keep firing after the cron failure"

        assert cron_event.running is False
        assert scheduler.list_events()["cron_event"]["running"] is False

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR and "cron_event" in r.getMessage()]
        assert len(error_records) == 1, f"expected exactly one ERROR record naming the event, got {error_records}"
    finally:
        scheduler.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
