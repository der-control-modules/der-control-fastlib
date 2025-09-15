"""Test scheduler behavior for past events - they should fire immediately."""

import logging
import time
from datetime import datetime, timedelta

import gevent
import pytest

from aems.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class PastEventTestAgent(Agent):
    """Test agent for past event scheduling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events_fired = []
        self.event_times = {}

    def test_callback(self, event_name: str):
        """Callback that records when it fired."""
        fire_time = time.time()
        self.events_fired.append(event_name)
        self.event_times[event_name] = fire_time
        _log.info(f"🎯 Event '{event_name}' fired at {datetime.fromtimestamp(fire_time)}")


def test_past_event_fires_immediately(message_bus_manager_fixture):
    """Test that events scheduled in the past fire immediately."""
    print("\n" + "="*80)
    print("🔍 TESTING PAST EVENT IMMEDIATE FIRING")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.past", PastEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)  # Allow connection to complete

    # Schedule an event 5 seconds in the past
    past_time = datetime.now() - timedelta(seconds=5)
    schedule_time = time.time()

    print(f"📅 Current time: {datetime.now()}")
    print(f"📅 Scheduling event for past time: {past_time} (5 seconds ago)")

    agent.core.schedule(past_time, agent.test_callback, "past_event")

    # Past events should fire almost immediately (within 1 second)
    gevent.sleep(1)

    # Verify the event fired
    assert len(agent.events_fired) == 1, f"Expected 1 event, got {len(agent.events_fired)}"
    assert agent.events_fired[0] == "past_event"

    # Verify it fired quickly after scheduling (within 1 second)
    fire_time = agent.event_times["past_event"]
    time_to_fire = fire_time - schedule_time

    print(f"✅ Past event fired {time_to_fire:.3f} seconds after scheduling")
    assert time_to_fire < 1.0, f"Past event took too long to fire: {time_to_fire:.3f}s"

    agent.disconnect()


def test_multiple_past_events_fire_immediately(message_bus_manager_fixture):
    """Test that multiple past events all fire immediately."""
    print("\n" + "="*80)
    print("🔍 TESTING MULTIPLE PAST EVENTS")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.multipast", PastEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    schedule_time = time.time()

    # Schedule multiple events in the past
    past_events = [
        (datetime.now() - timedelta(seconds=10), "past_10s"),
        (datetime.now() - timedelta(seconds=5), "past_5s"),
        (datetime.now() - timedelta(seconds=1), "past_1s"),
    ]

    for past_time, event_name in past_events:
        print(f"📅 Scheduling {event_name} for {past_time}")
        agent.core.schedule(past_time, agent.test_callback, event_name)

    # All past events should fire within 1 second
    gevent.sleep(1)

    # Verify all events fired
    assert len(agent.events_fired) == 3, f"Expected 3 events, got {len(agent.events_fired)}"
    assert set(agent.events_fired) == {"past_10s", "past_5s", "past_1s"}

    # Verify they all fired quickly
    for event_name in agent.events_fired:
        fire_time = agent.event_times[event_name]
        time_to_fire = fire_time - schedule_time
        print(f"✅ {event_name} fired {time_to_fire:.3f} seconds after scheduling")
        assert time_to_fire < 1.0, f"{event_name} took too long to fire: {time_to_fire:.3f}s"

    agent.disconnect()


def test_past_event_added_after_agent_startup(message_bus_manager_fixture):
    """Test that past events added after initial agent startup still fire immediately."""
    print("\n" + "="*80)
    print("🔍 TESTING PAST EVENT AFTER AGENT STARTUP (Production Scenario)")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.production", PastEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Simulate production scenario: agent has been running for a while
    print("⏳ Simulating agent running for 2 seconds...")
    gevent.sleep(2)

    # Now schedule a past event (like in production after initial startup)
    past_time = datetime.now() - timedelta(seconds=3)
    schedule_time = time.time()

    print(f"📅 After agent has been running, scheduling past event for {past_time}")
    agent.core.schedule(past_time, agent.test_callback, "production_past_event")

    # The past event should still fire immediately
    gevent.sleep(1)

    # Verify the event fired
    assert len(agent.events_fired) == 1, f"Expected 1 event, got {len(agent.events_fired)}"
    assert agent.events_fired[0] == "production_past_event"

    # Verify it fired quickly
    fire_time = agent.event_times["production_past_event"]
    time_to_fire = fire_time - schedule_time

    print(f"✅ Production past event fired {time_to_fire:.3f} seconds after scheduling")
    assert time_to_fire < 1.0, f"Production past event took too long: {time_to_fire:.3f}s"

    agent.disconnect()


def test_mixed_past_and_future_events(message_bus_manager_fixture):
    """Test scheduling a mix of past and future events."""
    print("\n" + "="*80)
    print("🔍 TESTING MIXED PAST AND FUTURE EVENTS")
    print("="*80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.mixed", PastEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Schedule a mix of events
    events = [
        (datetime.now() - timedelta(seconds=2), "past_event"),
        (datetime.now() + timedelta(seconds=1), "future_1s"),
        (datetime.now() + timedelta(seconds=2), "future_2s"),
    ]

    for event_time, event_name in events:
        time_desc = "past" if event_time < datetime.now() else "future"
        print(f"📅 Scheduling {event_name} ({time_desc}): {event_time}")
        agent.core.schedule(event_time, agent.test_callback, event_name)

    # Wait a bit for past event
    gevent.sleep(0.5)

    # Past event should have fired
    assert "past_event" in agent.events_fired
    print("✅ Past event fired immediately")

    # Future events should not have fired yet
    assert "future_1s" not in agent.events_fired
    assert "future_2s" not in agent.events_fired

    # Wait for future events
    gevent.sleep(2.5)

    # All events should have fired
    assert len(agent.events_fired) == 3
    assert set(agent.events_fired) == {"past_event", "future_1s", "future_2s"}
    print("✅ All events fired in correct order")

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
