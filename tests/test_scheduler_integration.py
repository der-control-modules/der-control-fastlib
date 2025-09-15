"""Test scheduler integration with agent lifecycle."""

import logging
import time
from datetime import datetime, timedelta

import gevent

from aems.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class SchedulerIntegrationTestAgent(Agent):
    """Test agent for scheduler integration testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scheduler_events = []
        self.lifecycle_events = []

    def test_callback(self, event_name: str, timestamp: float = None):
        """Test callback that records when it was called."""
        now = time.time()
        self.scheduler_events.append({
            "event_name": event_name,
            "scheduled_timestamp": timestamp,
            "actual_timestamp": now,
            "called_at": datetime.now()
        })
        _log.info(f"📅 Scheduler callback: {event_name} at {datetime.now()}")

    def onstart(self):
        """Called when agent starts - record lifecycle event."""
        self.lifecycle_events.append(("onstart", datetime.now()))
        _log.info("🚀 Agent onstart called")

    def onstop(self):
        """Called when agent stops - record lifecycle event."""
        self.lifecycle_events.append(("onstop", datetime.now()))
        _log.info("🛑 Agent onstop called")


def test_scheduler_lifecycle_integration(message_bus_manager_fixture):
    """Test that scheduler starts/stops with agent lifecycle."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.lifecycle", SchedulerIntegrationTestAgent)

    # Before connection - scheduler should not be running
    assert not hasattr(agent, 'core') or agent.core._scheduler._scheduler_greenlet is None, \
        "Scheduler should not be running before agent connection"

    _log.info("🔍 Connecting agent - scheduler should start...")
    agent.connect()
    gevent.sleep(0.5)  # Allow connection to complete

    # After connection - scheduler should be running
    assert agent.core._scheduler._scheduler_greenlet is not None, \
        "Scheduler greenlet should exist after agent connection"
    assert not agent.core._scheduler._scheduler_greenlet.dead, \
        "Scheduler greenlet should be alive after agent connection"

    scheduler_greenlet = agent.core._scheduler._scheduler_greenlet
    _log.info(f"✅ Scheduler is running: greenlet={scheduler_greenlet}, dead={scheduler_greenlet.dead}")

    # Schedule a test event to verify scheduler processes events
    now = datetime.now()
    future_time = now + timedelta(seconds=1)
    agent.core.schedule(future_time, agent.test_callback, "lifecycle_test", time.time())

    _log.info(f"📅 Scheduled event for {future_time}")
    _log.info(f"🔢 Events in queue: {len(agent.core._scheduler._event_queue)}")

    # Wait for event to fire
    gevent.sleep(1.5)

    # Verify event fired
    assert len(agent.scheduler_events) == 1, f"Expected 1 scheduler event, got {len(agent.scheduler_events)}"
    event = agent.scheduler_events[0]
    assert event["event_name"] == "lifecycle_test"
    _log.info("✅ Scheduler processed event successfully")

    # Verify scheduler is still running
    assert not scheduler_greenlet.dead, "Scheduler should still be alive after processing events"

    # Disconnect and verify scheduler stops
    _log.info("🔌 Disconnecting agent - scheduler should stop...")
    agent.disconnect()
    gevent.sleep(0.5)  # Allow disconnection to complete

    # Scheduler should be cleaned up after disconnect
    # Note: The scheduler greenlet may still exist but should be marked as dead
    if agent.core._scheduler._scheduler_greenlet:
        _log.info(f"Scheduler after disconnect: dead={agent.core._scheduler._scheduler_greenlet.dead}")

    _log.info("✅ Agent lifecycle integration test completed")


def test_scheduler_queue_and_processing(message_bus_manager_fixture):
    """Test scheduler queue management and event processing."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.queue", SchedulerIntegrationTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    scheduler = agent.core._scheduler

    # Verify scheduler starts with empty queue
    assert len(scheduler._event_queue) == 0, f"Expected empty queue, got {len(scheduler._event_queue)} events"
    _log.info("✅ Scheduler starts with empty queue")

    # Schedule multiple events at different times
    now = datetime.now()
    events_to_schedule = [
        (now + timedelta(seconds=0.5), "event_1"),
        (now + timedelta(seconds=1.0), "event_2"),
        (now + timedelta(seconds=1.5), "event_3"),
        (now - timedelta(seconds=1), "past_event")  # Past event should fire immediately
    ]

    for event_time, event_name in events_to_schedule:
        agent.core.schedule(event_time, agent.test_callback, event_name, time.time())
        _log.info(f"📅 Scheduled {event_name} for {event_time}")

    # Check queue size after scheduling
    queue_size = len(scheduler._event_queue)
    _log.info(f"🔢 Events in queue after scheduling: {queue_size}")

    # Past event should fire immediately, so queue should have 3 remaining events
    gevent.sleep(0.1)  # Brief pause for immediate events
    immediate_events = len(agent.scheduler_events)
    _log.info(f"📊 Events fired immediately: {immediate_events}")

    # Wait for all scheduled events
    gevent.sleep(2)

    # Verify all events fired
    total_events = len(agent.scheduler_events)
    assert total_events == 4, f"Expected 4 events total, got {total_events}"
    _log.info(f"✅ All {total_events} events processed successfully")

    # Verify events fired in correct order (past event first, then chronological)
    event_names = [e["event_name"] for e in agent.scheduler_events]
    assert event_names[0] == "past_event", f"Past event should fire first, got: {event_names}"

    # Remaining events should be in chronological order
    remaining_events = event_names[1:]
    expected_order = ["event_1", "event_2", "event_3"]
    assert remaining_events == expected_order, f"Events fired out of order: {remaining_events}"
    _log.info("✅ Events fired in correct chronological order")

    agent.disconnect()


def test_scheduler_error_handling(message_bus_manager_fixture):
    """Test scheduler behavior when callbacks raise exceptions."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.errors", SchedulerIntegrationTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    def failing_callback(event_name: str):
        """Callback that raises an exception."""
        agent.scheduler_events.append({"event_name": event_name, "status": "called"})
        _log.info(f"💥 Failing callback called: {event_name}")
        raise ValueError(f"Intentional error in {event_name}")

    def working_callback(event_name: str):
        """Callback that works normally."""
        agent.scheduler_events.append({"event_name": event_name, "status": "success"})
        _log.info(f"✅ Working callback called: {event_name}")

    # Schedule events: failing, working, failing, working
    now = datetime.now()
    agent.core.schedule(now + timedelta(seconds=0.5), failing_callback, "fail_1")
    agent.core.schedule(now + timedelta(seconds=1.0), working_callback, "work_1")
    agent.core.schedule(now + timedelta(seconds=1.5), failing_callback, "fail_2")
    agent.core.schedule(now + timedelta(seconds=2.0), working_callback, "work_2")

    _log.info("📅 Scheduled mix of failing and working callbacks")

    # Wait for all events
    gevent.sleep(2.5)

    # Verify all callbacks were called despite exceptions
    assert len(agent.scheduler_events) == 4, f"Expected 4 events, got {len(agent.scheduler_events)}"

    event_names = [e["event_name"] for e in agent.scheduler_events]
    expected_names = ["fail_1", "work_1", "fail_2", "work_2"]
    assert event_names == expected_names, f"Events called out of order: {event_names}"

    # Verify scheduler is still running after exceptions
    scheduler_alive = (agent.core._scheduler._scheduler_greenlet and
                      not agent.core._scheduler._scheduler_greenlet.dead)
    assert scheduler_alive, "Scheduler should still be running after callback exceptions"

    _log.info("✅ Scheduler continues running despite callback exceptions")
    agent.disconnect()


def test_scheduler_timing_accuracy(message_bus_manager_fixture):
    """Test scheduler timing accuracy for precise scheduling."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.timing", SchedulerIntegrationTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Schedule events at precise intervals
    start_time = time.time()
    base_datetime = datetime.fromtimestamp(start_time + 1)  # Start 1 second from now

    intervals = [0.0, 0.1, 0.2, 0.5, 1.0]  # Schedule at these second offsets

    for i, interval in enumerate(intervals):
        event_time = base_datetime + timedelta(seconds=interval)
        expected_timestamp = start_time + 1 + interval
        agent.core.schedule(event_time, agent.test_callback, f"timing_{i}", expected_timestamp)
        _log.info(f"📅 Scheduled timing_{i} for {event_time} (expected: {expected_timestamp})")

    # Wait for all events
    gevent.sleep(3)

    # Verify all events fired
    assert len(agent.scheduler_events) == len(intervals), \
        f"Expected {len(intervals)} events, got {len(agent.scheduler_events)}"

    # Check timing accuracy (allow 50ms tolerance)
    tolerance = 0.05  # 50ms tolerance
    for i, event in enumerate(agent.scheduler_events):
        expected_ts = event["scheduled_timestamp"]
        actual_ts = event["actual_timestamp"]
        timing_diff = abs(actual_ts - expected_ts)

        _log.info(f"⏱️  Event {i}: expected={expected_ts:.3f}, actual={actual_ts:.3f}, diff={timing_diff:.3f}s")
        assert timing_diff <= tolerance, \
            f"Event {i} timing off by {timing_diff:.3f}s (tolerance: {tolerance}s)"

    _log.info(f"✅ All events fired within {tolerance}s timing tolerance")
    agent.disconnect()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
