"""Debug test for production scheduler issue - past events not firing after startup."""

import logging
import time
from datetime import datetime, timedelta

import gevent

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class ProductionDebugAgent(Agent):
    """Agent to debug production scheduling issue."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_actions = []

    def _do_control_action(self, gid: str, state: str):
        """The actual control action that should fire."""
        action_time = datetime.now()
        self.control_actions.append({"gid": gid, "state": state, "time": action_time})
        print(f"CONTROL ACTION FIRED: gid={gid}, state={state} at {action_time}")
        _log.info(f"_do_control_action executed: gid={gid}, state={state}")


def test_scheduler_8am_6pm_simulation(message_bus_manager_fixture):
    """Simulate the exact production scenario with 8am-6pm occupancy override."""
    print("\n" + "=" * 80)
    print("PRODUCTION DEBUG: 8AM-6PM OCCUPANCY OVERRIDE")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("manager.rtu02", ProductionDebugAgent)
    agent.connect()

    # Let the scheduler run for a bit to simulate production
    print("\nAgent started, scheduler running...")
    print("Scheduler info:")
    print(f"   - Scheduler greenlet: {agent.core._scheduler._scheduler_greenlet}")
    greenlet = agent.core._scheduler._scheduler_greenlet
    print(f"   - Greenlet dead? {greenlet.dead if greenlet else 'N/A'}")
    print(f"   - Queue size: {len(agent.core._scheduler._event_queue)}")

    gevent.sleep(2)  # Simulate agent running for a while

    # Now simulate adding occupancy override like in production
    print("\nSimulating occupancy override schedule (like production)...")
    print("   This simulates: {'2025-09-12': [{'start': '08:00', 'end': '18:00'}]}")

    # Past and future relative to "now", not a fixed wall-clock hour (#18): a
    # fixed 08:00/18:00 is only in the past/future depending on when the test
    # runs, which made the assertion below flaky by time of day.
    current_time = datetime.now()
    past_event_time = current_time - timedelta(hours=1)
    future_event_time = current_time + timedelta(hours=1)
    print(f"\nCurrent time: {current_time}")
    print(f"Past event time: {past_event_time} (is past? {past_event_time < current_time})")
    print(f"Future event time: {future_event_time} (is past? {future_event_time < current_time})")

    # Schedule the override exactly like production code
    print("\nCalling agent.core.schedule() for the past event (should fire immediately)...")
    agent.core.schedule(past_event_time, agent._do_control_action, "2025-09-12_0", "occupied")

    print("Calling agent.core.schedule() for the future event...")
    agent.core.schedule(
        future_event_time, agent._do_control_action, "2025-09-12_0", "unoccupied"
    )

    print("\nAfter scheduling:")
    print(f"   - Queue size: {len(agent.core._scheduler._event_queue)}")
    print(f"   - Control actions fired so far: {len(agent.control_actions)}")

    # Wait to see if past event fires
    print("\nWaiting 3 seconds for past event to fire...")
    gevent.sleep(3)

    print("\nFinal status:")
    print(f"   - Queue size: {len(agent.core._scheduler._event_queue)}")
    print(f"   - Control actions fired: {len(agent.control_actions)}")

    if agent.control_actions:
        print("\nControl actions that fired:")
        for action in agent.control_actions:
            print(f"   - {action['gid']}: {action['state']} at {action['time']}")
    else:
        print("\nNO CONTROL ACTIONS FIRED!")

    # Check if the past event fired (it's in the past so should fire immediately)
    past_event_fired = any(
        action["state"] == "occupied" for action in agent.control_actions
    )

    if not past_event_fired:
        print("\nPRODUCTION BUG REPRODUCED: Past event did not fire!")
        print("   The event was added to queue but scheduler didn't process it.")
    else:
        print("\nPast event fired correctly")

    agent.disconnect()

    # Assert to make test fail if bug is present
    assert past_event_fired, "Past event (occupancy override) should have fired immediately!"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-s"])

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class TestSchedulerAgent(Agent):
    """Test agent for debugging scheduler issues."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events_fired = []

    def test_callback(self, event_name: str):
        """Test callback that logs when it fires."""
        now = datetime.now()
        _log.info(f"CALLBACK FIRED: {event_name} at {now}")
        self.events_fired.append((event_name, now))


def test_scheduler_with_various_times(message_bus_manager_fixture):
    """Test scheduler with past, current, and future times."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = TestSchedulerAgent(
        "test.scheduler.debug", host="127.0.0.1", port=manager.port
    )
    agent.connect()

    # Manually start the scheduler
    agent.core._scheduler.start()

    now = datetime.now()
    _log.info(f"Current time: {now}")
    _log.info(
        f"Scheduler running: {agent.core._scheduler._scheduler_greenlet is not None}"
    )

    # Test 1: Schedule event in the past (should fire immediately)
    past_time = now - timedelta(seconds=10)
    _log.info(f"Scheduling past event for: {past_time}")
    agent.core.schedule(past_time, agent.test_callback, "past_event")

    # Test 2: Schedule event 2 seconds in the future
    future_time = now + timedelta(seconds=2)
    _log.info(f"Scheduling future event for: {future_time}")
    agent.core.schedule(future_time, agent.test_callback, "future_event")

    # Test 3: Schedule event for exact current time
    _log.info(f"Scheduling current event for: {now}")
    agent.core.schedule(now, agent.test_callback, "current_event")

    # Let events process
    _log.info("Waiting for events to fire...")
    gevent.sleep(0.5)  # Wait for immediate events

    assert (
        len(agent.events_fired) >= 2
    ), f"Expected at least 2 events (past and current), got {len(agent.events_fired)}"
    _log.info(f"Events fired so far: {agent.events_fired}")

    # Wait for future event
    gevent.sleep(2)

    assert (
        len(agent.events_fired) == 3
    ), f"Expected 3 events total, got {len(agent.events_fired)}"
    _log.info(f"All events fired: {agent.events_fired}")

    agent.disconnect()


def test_scheduler_8am_6pm_simulation_duplicate(message_bus_manager_fixture):
    """Simulate the actual 8am-6pm scheduling scenario (duplicate test)."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = TestSchedulerAgent(
        "test.scheduler.8am6pm", host="127.0.0.1", port=manager.port
    )
    agent.connect()
    agent.core._scheduler.start()

    # Create times for today at 8am and 6pm
    today = datetime.now().date()
    time_8am = datetime.combine(today, datetime.strptime("08:00", "%H:%M").time())
    time_6pm = datetime.combine(today, datetime.strptime("18:00", "%H:%M").time())

    now = datetime.now()
    _log.info(f"Current time: {now}")
    _log.info(f"8am time: {time_8am} (is past: {time_8am < now})")
    _log.info(f"6pm time: {time_6pm} (is past: {time_6pm < now})")

    # Check what timestamps we get
    timestamp_8am = time.mktime(time_8am.timetuple())
    timestamp_6pm = time.mktime(time_6pm.timetuple())
    timestamp_now = time.time()

    _log.info(
        f"8am timestamp: {timestamp_8am} ({datetime.fromtimestamp(timestamp_8am)})"
    )
    _log.info(
        f"6pm timestamp: {timestamp_6pm} ({datetime.fromtimestamp(timestamp_6pm)})"
    )
    _log.info(
        f"Now timestamp: {timestamp_now} ({datetime.fromtimestamp(timestamp_now)})"
    )

    # Schedule the events
    agent.core.schedule(time_8am, agent.test_callback, "8am_event")
    agent.core.schedule(time_6pm, agent.test_callback, "6pm_event")

    # Check scheduler queue
    _log.info(f"Scheduler queue size: {len(agent.core._scheduler._event_queue)}")
    if agent.core._scheduler._event_queue:
        next_event = agent.core._scheduler._event_queue[0]
        _log.info(f"Next event: {next_event}")

    # Wait a bit to see if past events fire
    gevent.sleep(1)

    # Check which events fired
    _log.info(f"Events fired: {agent.events_fired}")

    # If current time is between 8am and 6pm, we expect 8am to have fired
    if time_8am < now < time_6pm:
        assert any(
            "8am" in evt[0] for evt in agent.events_fired
        ), "8am event should have fired (time is past)"

    # If current time is after 6pm, both should have fired
    if now > time_6pm:
        assert any(
            "8am" in evt[0] for evt in agent.events_fired
        ), "8am event should have fired"
        assert any(
            "6pm" in evt[0] for evt in agent.events_fired
        ), "6pm event should have fired"

    agent.disconnect()


def test_scheduler_not_started(message_bus_manager_fixture):
    """Test what happens if scheduler is not explicitly started."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = TestSchedulerAgent(
        "test.scheduler.notstarted", host="127.0.0.1", port=manager.port
    )
    agent.connect()

    # Note: NOT calling agent.core._scheduler.start()
    # The scheduler should auto-start via agent.connect() -> start_periodic_tasks()

    # Check if scheduler is running
    _log.info(
        f"Scheduler greenlet exists: {agent.core._scheduler._scheduler_greenlet is not None}"
    )
    if agent.core._scheduler._scheduler_greenlet:
        _log.info(
            f"Scheduler greenlet dead: {agent.core._scheduler._scheduler_greenlet.dead}"
        )

    # Try to schedule something
    now = datetime.now()
    future_time = now + timedelta(seconds=1)
    agent.core.schedule(future_time, agent.test_callback, "test_event")

    # Wait for event
    gevent.sleep(1.5)

    # Check if it fired
    _log.info(f"Events fired: {agent.events_fired}")
    assert (
        len(agent.events_fired) == 1
    ), "Event should have fired even without explicit start"

    agent.disconnect()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-s"])
