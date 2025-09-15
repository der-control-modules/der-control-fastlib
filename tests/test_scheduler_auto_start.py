"""Test that scheduler auto-starts when past events are scheduled."""

import logging
from datetime import datetime, timedelta

import gevent
import pytest

from aems.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class AutoStartTestAgent(Agent):
    """Test agent for auto-start functionality."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events_fired = []

    def test_callback(self, event_name: str):
        """Test callback that records when it fired."""
        fire_time = datetime.now()
        self.events_fired.append({
            'name': event_name,
            'time': fire_time
        })
        _log.info(f"🎯 Event '{event_name}' fired at {fire_time}")


def test_scheduler_auto_starts_for_past_event(message_bus_manager_fixture):
    """Test that scheduler automatically starts when a past event is scheduled."""
    print("\n" + "="*80)
    print("🔍 TESTING SCHEDULER AUTO-START FOR PAST EVENTS")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.autostart", AutoStartTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Manually stop the scheduler to simulate it not running
    if agent.core._scheduler._scheduler_greenlet:
        print("📛 Manually stopping scheduler to simulate production issue...")
        agent.core._scheduler.stop()
        gevent.sleep(0.5)

        # Verify scheduler is stopped
        is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                     not agent.core._scheduler._scheduler_greenlet.dead)
        print(f"Scheduler running after stop: {is_running}")
        assert not is_running, "Scheduler should be stopped"

    # Now schedule a past event (should auto-start scheduler)
    past_time = datetime.now() - timedelta(minutes=10)
    print(f"\n📅 Scheduling past event for {past_time} (10 minutes ago)")
    print("This should auto-start the scheduler and fire immediately...")

    agent.core.schedule(past_time, agent.test_callback, "past_event_autostart")

    # Give it time to start scheduler and process
    gevent.sleep(2)

    # Check if scheduler is now running
    is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                 not agent.core._scheduler._scheduler_greenlet.dead)
    print(f"\nScheduler running after scheduling past event: {is_running}")

    # Check if event fired
    print(f"Events fired: {len(agent.events_fired)}")
    if agent.events_fired:
        for event in agent.events_fired:
            print(f"  - {event['name']} at {event['time']}")

    assert is_running, "Scheduler should have auto-started"
    assert len(agent.events_fired) == 1, "Past event should have fired"
    assert agent.events_fired[0]['name'] == "past_event_autostart"

    print("\n✅ Scheduler auto-started and processed past event correctly")

    agent.disconnect()


def test_scheduler_handles_past_event_without_running(message_bus_manager_fixture):
    """Test that past events fire even if scheduler wasn't initially running."""
    print("\n" + "="*80)
    print("🔍 TESTING PAST EVENT WITH NON-RUNNING SCHEDULER")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    # Create agent but DON'T call connect (so scheduler won't start)
    agent = AutoStartTestAgent("test.scheduler.nostart", host="127.0.0.1", port=manager.port)

    # Manually connect WebSocket but skip the onstart/periodic tasks
    agent._connect_websocket()
    gevent.sleep(0.5)

    # Verify scheduler is not running
    is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                 not agent.core._scheduler._scheduler_greenlet.dead)
    print(f"Initial scheduler state: {'Running' if is_running else 'Not Running'}")
    assert not is_running, "Scheduler should not be running initially"

    # Schedule multiple events: past, current, and future
    now = datetime.now()
    events_to_schedule = [
        (now - timedelta(hours=2), "past_2hours"),
        (now - timedelta(minutes=30), "past_30min"),
        (now - timedelta(seconds=10), "past_10sec"),
        (now + timedelta(seconds=1), "future_1sec"),
        (now + timedelta(minutes=5), "future_5min"),
    ]

    print("\n📅 Scheduling multiple events (3 past, 2 future):")
    for event_time, event_name in events_to_schedule:
        time_desc = "past" if event_time < now else "future"
        print(f"  - {event_name} ({time_desc}): {event_time}")
        agent.core.schedule(event_time, agent.test_callback, event_name)

    # Wait for processing
    gevent.sleep(3)

    # Check scheduler status
    is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                 not agent.core._scheduler._scheduler_greenlet.dead)
    print(f"\nScheduler state after scheduling: {'Running' if is_running else 'Not Running'}")

    # Check which events fired
    print(f"\nEvents fired: {len(agent.events_fired)}")
    for event in agent.events_fired:
        print(f"  - {event['name']} at {event['time']}")

    # All past events should have fired
    past_event_names = {"past_2hours", "past_30min", "past_10sec"}
    fired_names = {e['name'] for e in agent.events_fired}

    assert is_running, "Scheduler should be running after scheduling past events"
    assert past_event_names.issubset(fired_names), (
        f"All past events should have fired. Expected {past_event_names}, got {fired_names}"
    )

    # Future events should also fire at their time
    gevent.sleep(2)  # Wait for future_1sec to fire

    fired_names_after = {e['name'] for e in agent.events_fired}
    assert "future_1sec" in fired_names_after, "Near future event should have fired"

    print("\n✅ All past events fired correctly with auto-started scheduler")

    agent.disconnect()


def test_production_scenario_with_stopped_scheduler(message_bus_manager_fixture):
    """Simulate exact production scenario: scheduler stopped, then occupancy override scheduled."""
    print("\n" + "="*80)
    print("🔍 SIMULATING PRODUCTION SCENARIO: STOPPED SCHEDULER + OCCUPANCY OVERRIDE")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.production.stopped", AutoStartTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Add some existing events to the queue
    now = datetime.now()
    for i in range(10):
        future_time = now + timedelta(hours=i+1)
        agent.core.schedule(future_time, agent.test_callback, f"existing_{i}")

    print(f"Added 10 future events to queue, size: {len(agent.core._scheduler._event_queue)}")

    # Now stop the scheduler (simulating a crash or issue)
    print("\n📛 Stopping scheduler to simulate production issue...")
    agent.core._scheduler.stop()
    gevent.sleep(0.5)

    # Verify stopped
    is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                 not agent.core._scheduler._scheduler_greenlet.dead)
    print(f"Scheduler state after stop: {'Running' if is_running else 'Not Running'}")
    assert not is_running, "Scheduler should be stopped"

    # Now simulate occupancy override being scheduled (8am today, which is past)
    today_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    today_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    is_8am_past = today_8am < now
    is_6pm_past = today_6pm < now

    print("\n📅 Simulating occupancy override schedule:")
    print(f"  Current time: {now}")
    print(f"  8AM: {today_8am} (past: {is_8am_past})")
    print(f"  6PM: {today_6pm} (past: {is_6pm_past})")

    # Schedule the override
    agent.core.schedule(today_8am, agent.test_callback, "occupancy_start")
    agent.core.schedule(today_6pm, agent.test_callback, "occupancy_end")

    # Wait for processing
    gevent.sleep(2)

    # Check scheduler restarted
    is_running = (agent.core._scheduler._scheduler_greenlet is not None and
                 not agent.core._scheduler._scheduler_greenlet.dead)
    print(f"\nScheduler state after occupancy override: {'Running' if is_running else 'Not Running'}")

    # Check which events fired
    print(f"\nEvents fired: {len(agent.events_fired)}")
    for event in agent.events_fired:
        print(f"  - {event['name']} at {event['time']}")

    # Check that past occupancy events fired
    if is_8am_past:
        assert any(e['name'] == 'occupancy_start' for e in agent.events_fired), \
            "8AM occupancy start should have fired (it's past)"

    if is_6pm_past:
        assert any(e['name'] == 'occupancy_end' for e in agent.events_fired), \
            "6PM occupancy end should have fired (it's past)"

    print("\n✅ Production scenario handled correctly - scheduler auto-started for past events")

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
