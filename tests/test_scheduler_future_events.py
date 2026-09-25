"""Test that future scheduled events actually fire at the correct time."""

import logging
import time
from datetime import datetime, timedelta

import gevent
import pytest

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class FutureEventTestAgent(Agent):
    """Test agent for future event scheduling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events_fired = []
        self.event_times = {}

    def test_callback(self, event_name: str, expected_time: float = None):
        """Callback that records when it fired."""
        fire_time = time.time()
        self.events_fired.append(event_name)
        self.event_times[event_name] = {
            "fired_at": fire_time,
            "expected_at": expected_time,
            "error": fire_time - expected_time if expected_time else None,
        }
        _log.info(
            f"🎯 Event '{event_name}' fired at {datetime.fromtimestamp(fire_time)}"
        )
        if expected_time:
            error = fire_time - expected_time
            _log.info(
                f"   Expected: {datetime.fromtimestamp(expected_time)}, Error: {error:.3f}s"
            )


def test_single_future_event_fires_on_time(message_bus_manager_fixture):
    """Test that a single future event fires at the correct time."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SINGLE FUTURE EVENT TIMING")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.future", FutureEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)  # Allow connection to complete

    # Schedule an event 2 seconds in the future
    schedule_time = time.time()
    future_time = datetime.now() + timedelta(seconds=2)
    expected_fire_time = schedule_time + 2

    print(f"📅 Current time: {datetime.now()}")
    print(f"📅 Scheduling event for: {future_time} (2 seconds from now)")

    agent.core.schedule(
        future_time, agent.test_callback, "future_event", expected_fire_time
    )

    # Wait for the event to fire (should take ~2 seconds)
    gevent.sleep(3)

    # Verify the event fired
    assert (
        len(agent.events_fired) == 1
    ), f"Expected 1 event, got {len(agent.events_fired)}"
    assert agent.events_fired[0] == "future_event"

    # Verify timing accuracy (within 100ms tolerance)
    event_info = agent.event_times["future_event"]
    timing_error = abs(event_info["error"])

    print(f"✅ Future event fired with {timing_error:.3f}s timing error")
    assert (
        timing_error < 0.1
    ), f"Future event timing error too large: {timing_error:.3f}s"

    agent.disconnect()


def test_multiple_future_events_fire_in_order(message_bus_manager_fixture):
    """Test that multiple future events fire in the correct order at the right times."""
    print("\n" + "=" * 80)
    print("🔍 TESTING MULTIPLE FUTURE EVENTS")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.multi_future", FutureEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    schedule_time = time.time()

    # Schedule multiple future events
    events = [
        (1.0, "event_1s"),
        (2.0, "event_2s"),
        (3.0, "event_3s"),
    ]

    for delay, event_name in events:
        future_time = datetime.now() + timedelta(seconds=delay)
        expected_time = schedule_time + delay
        print(f"📅 Scheduling {event_name} for {future_time} ({delay}s from now)")
        agent.core.schedule(future_time, agent.test_callback, event_name, expected_time)

    # Wait for all events to fire
    gevent.sleep(4)

    # Verify all events fired
    assert (
        len(agent.events_fired) == 3
    ), f"Expected 3 events, got {len(agent.events_fired)}"

    # Verify they fired in the correct order
    assert agent.events_fired == [
        "event_1s",
        "event_2s",
        "event_3s",
    ], f"Events fired in wrong order: {agent.events_fired}"

    # Verify timing accuracy for each event
    for _delay, event_name in events:
        event_info = agent.event_times[event_name]
        timing_error = abs(event_info["error"])
        print(f"✅ {event_name} fired with {timing_error:.3f}s timing error")
        assert (
            timing_error < 0.1
        ), f"{event_name} timing error too large: {timing_error:.3f}s"

    agent.disconnect()


def test_rapid_future_events(message_bus_manager_fixture):
    """Test scheduling many events in rapid succession."""
    print("\n" + "=" * 80)
    print("🔍 TESTING RAPID FUTURE EVENT SCHEDULING")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.rapid", FutureEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Schedule 10 events, each 200ms apart
    base_time = time.time()
    num_events = 10
    interval = 0.2  # 200ms

    for i in range(num_events):
        delay = (i + 1) * interval
        future_time = datetime.fromtimestamp(base_time + delay)
        expected_time = base_time + delay
        event_name = f"rapid_{i}"
        agent.core.schedule(future_time, agent.test_callback, event_name, expected_time)

    print(f"📅 Scheduled {num_events} events, {interval}s apart")

    # Wait for all events
    gevent.sleep(num_events * interval + 1)

    # Verify all events fired
    assert (
        len(agent.events_fired) == num_events
    ), f"Expected {num_events} events, got {len(agent.events_fired)}"

    # Check timing accuracy
    max_error = 0
    for i in range(num_events):
        event_name = f"rapid_{i}"
        if event_name in agent.event_times:
            error = abs(agent.event_times[event_name]["error"])
            max_error = max(max_error, error)

    print(f"✅ All {num_events} rapid events fired, max timing error: {max_error:.3f}s")
    assert max_error < 0.15, f"Max timing error too large: {max_error:.3f}s"

    agent.disconnect()


def test_far_future_event(message_bus_manager_fixture):
    """Test scheduling an event far in the future (10 seconds)."""
    print("\n" + "=" * 80)
    print("🔍 TESTING FAR FUTURE EVENT")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.far_future", FutureEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Schedule an event 10 seconds in the future
    schedule_time = time.time()
    delay = 10
    future_time = datetime.now() + timedelta(seconds=delay)
    expected_time = schedule_time + delay

    print(f"📅 Scheduling event for {future_time} ({delay}s from now)")
    print("⏳ This will take 10 seconds...")

    agent.core.schedule(future_time, agent.test_callback, "far_future", expected_time)

    # Check that it hasn't fired yet after 5 seconds
    gevent.sleep(5)
    assert len(agent.events_fired) == 0, "Event fired too early!"
    print("✅ 5 seconds passed, event hasn't fired yet (correct)")

    # Wait for the rest and verify it fires
    gevent.sleep(6)  # Total 11 seconds

    assert (
        len(agent.events_fired) == 1
    ), f"Expected 1 event, got {len(agent.events_fired)}"

    # Check timing
    event_info = agent.event_times["far_future"]
    timing_error = abs(event_info["error"])
    print(f"✅ Far future event fired with {timing_error:.3f}s timing error")
    assert timing_error < 0.2, f"Timing error too large: {timing_error:.3f}s"

    agent.disconnect()


def test_mixed_timing_events(message_bus_manager_fixture):
    """Test a mix of immediate, near, and far future events."""
    print("\n" + "=" * 80)
    print("🔍 TESTING MIXED TIMING EVENTS")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.mixed_timing", FutureEventTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    base_time = time.time()

    # Schedule events with various timings
    events = [
        (0.1, "immediate"),  # Almost immediate
        (0.5, "half_second"),  # Half second
        (1.5, "one_half_sec"),  # 1.5 seconds
        (3.0, "three_sec"),  # 3 seconds
        (5.0, "five_sec"),  # 5 seconds
    ]

    for delay, event_name in events:
        future_time = datetime.fromtimestamp(base_time + delay)
        expected_time = base_time + delay
        print(f"📅 Scheduling {event_name} for +{delay}s")
        agent.core.schedule(future_time, agent.test_callback, event_name, expected_time)

    # Wait for all events
    gevent.sleep(6)

    # Verify all events fired
    assert len(agent.events_fired) == len(
        events
    ), f"Expected {len(events)} events, got {len(agent.events_fired)}"

    # Verify order
    expected_order = [name for _, name in events]
    assert (
        agent.events_fired == expected_order
    ), f"Events fired in wrong order. Expected: {expected_order}, Got: {agent.events_fired}"

    # Check timing for all events
    for delay, event_name in events:
        event_info = agent.event_times[event_name]
        timing_error = abs(event_info["error"])
        print(f"✅ {event_name} (+{delay}s) fired with {timing_error:.3f}s error")
        assert (
            timing_error < 0.2
        ), f"{event_name} timing error too large: {timing_error:.3f}s"

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
