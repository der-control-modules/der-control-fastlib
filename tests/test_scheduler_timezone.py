"""Test scheduler timezone handling."""

import logging
import time
from datetime import datetime, timedelta, timezone

import gevent
import pytz

from aems.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class TZTestAgent(Agent):
    """Test agent for timezone testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events_fired = []

    def test_callback(self, event_name: str):
        """Test callback that logs when it fires."""
        now = datetime.now()
        _log.info(f"🎯 CALLBACK FIRED: {event_name} at {now}")
        self.events_fired.append((event_name, now))


def test_naive_vs_aware_datetime(message_bus_manager_fixture):
    """Test scheduler with naive vs timezone-aware datetime objects."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = TZTestAgent("test.tz", host="127.0.0.1", port=manager.port)
    agent.connect()

    # Get current times in different forms
    naive_now = datetime.now()
    aware_now_utc = datetime.now(timezone.utc)
    aware_now_local = datetime.now(pytz.timezone('US/Eastern'))  # or your local TZ

    _log.info(f"Naive now: {naive_now}")
    _log.info(f"Aware UTC: {aware_now_utc}")
    _log.info(f"Aware Local: {aware_now_local}")

    # Test what happens when we schedule with each type
    future_naive = naive_now + timedelta(seconds=1)
    future_aware_utc = aware_now_utc + timedelta(seconds=1)
    future_aware_local = aware_now_local + timedelta(seconds=1)

    _log.info("\n=== Testing conversions ===")

    # Check what mktime does with each
    try:
        naive_timestamp = time.mktime(future_naive.timetuple())
        _log.info(f"Naive -> mktime: {naive_timestamp} ({datetime.fromtimestamp(naive_timestamp)})")
    except Exception as e:
        _log.error(f"Naive mktime failed: {e}")

    try:
        aware_utc_timestamp = time.mktime(future_aware_utc.timetuple())
        _log.info(f"Aware UTC -> mktime: {aware_utc_timestamp} ({datetime.fromtimestamp(aware_utc_timestamp)})")
    except Exception as e:
        _log.error(f"Aware UTC mktime failed: {e}")

    try:
        aware_local_timestamp = time.mktime(future_aware_local.timetuple())
        _log.info(f"Aware Local -> mktime: {aware_local_timestamp} ({datetime.fromtimestamp(aware_local_timestamp)})")
    except Exception as e:
        _log.error(f"Aware Local mktime failed: {e}")

    # Schedule events
    try:
        agent.core.schedule(future_naive, agent.test_callback, "naive_event")
        _log.info("✓ Scheduled naive datetime")
    except Exception as e:
        _log.error(f"Failed to schedule naive datetime: {e}")

    try:
        agent.core.schedule(future_aware_utc, agent.test_callback, "aware_utc_event")
        _log.info("✓ Scheduled aware UTC datetime")
    except Exception as e:
        _log.error(f"Failed to schedule aware UTC datetime: {e}")

    try:
        agent.core.schedule(future_aware_local, agent.test_callback, "aware_local_event")
        _log.info("✓ Scheduled aware local datetime")
    except Exception as e:
        _log.error(f"Failed to schedule aware local datetime: {e}")

    # Wait for events
    gevent.sleep(2)

    _log.info(f"\nEvents fired: {agent.events_fired}")

    agent.disconnect()


def test_timezone_conversion_issue():
    """Test the timezone conversion issue directly."""

    # Create a specific time (e.g., 3pm local time)
    naive_3pm = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)

    # Create the same time but timezone-aware
    eastern = pytz.timezone('US/Eastern')
    aware_3pm_eastern = eastern.localize(naive_3pm)

    # If the server is in UTC, this could be very different!
    utc_3pm = aware_3pm_eastern.astimezone(pytz.UTC)

    _log.info(f"Naive 3pm: {naive_3pm}")
    _log.info(f"Aware 3pm Eastern: {aware_3pm_eastern}")
    _log.info(f"Same time in UTC: {utc_3pm}")

    # What does mktime give us?
    naive_ts = time.mktime(naive_3pm.timetuple())
    aware_ts = time.mktime(aware_3pm_eastern.timetuple())

    _log.info("\nTimestamps:")
    _log.info(f"Naive mktime: {naive_ts} -> {datetime.fromtimestamp(naive_ts)}")
    _log.info(f"Aware mktime: {aware_ts} -> {datetime.fromtimestamp(aware_ts)}")

    # The issue: mktime() ignores timezone info!
    # It always interprets the time as local time
    _log.warning(f"mktime ignores timezone! Both give same result: {naive_ts == aware_ts}")

    # Correct way to handle timezone-aware datetimes
    if aware_3pm_eastern.tzinfo:
        correct_timestamp = aware_3pm_eastern.timestamp()
        _log.info(f"Correct timestamp() for aware: {correct_timestamp} -> {datetime.fromtimestamp(correct_timestamp)}")


def test_scheduler_timezone_fix(message_bus_manager_fixture):
    """Test a potential fix for timezone handling."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = TZTestAgent("test.tz.fix", host="127.0.0.1", port=manager.port)
    agent.connect()

    # Schedule something for 8am Eastern time
    eastern = pytz.timezone('US/Eastern')
    now_eastern = datetime.now(eastern)
    tomorrow_8am_eastern = (now_eastern + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )

    _log.info(f"Scheduling for 8am Eastern tomorrow: {tomorrow_8am_eastern}")
    _log.info(f"In UTC that's: {tomorrow_8am_eastern.astimezone(pytz.UTC)}")
    _log.info(f"Local naive equivalent: {tomorrow_8am_eastern.replace(tzinfo=None)}")

    # The current implementation will use mktime which ignores timezone
    # This means it will interpret as 8am LOCAL time, not 8am Eastern!

    try:
        agent.core.schedule(tomorrow_8am_eastern, agent.test_callback, "8am_eastern")

        # Check what was actually scheduled
        if agent.core._scheduler._event_queue:
            event = agent.core._scheduler._event_queue[0]
            scheduled_time = datetime.fromtimestamp(event.next_time)
            _log.info(f"Actually scheduled for: {scheduled_time} (local time)")
    except Exception as e:
        _log.error(f"Failed to schedule: {e}")

    agent.disconnect()


if __name__ == "__main__":
    import pytest

    # Run the conversion test directly
    print("\n" + "="*60)
    print("TIMEZONE CONVERSION ISSUE DEMONSTRATION")
    print("="*60)
    test_timezone_conversion_issue()

    # Run pytest tests
    pytest.main([__file__, "-v", "-s", "-k", "test_naive_vs_aware"])
