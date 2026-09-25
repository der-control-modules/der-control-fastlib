"""Test scheduler tracing with yellow background output."""

import logging
from datetime import datetime, timedelta

import gevent

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class TracingTestAgent(Agent):
    """Test agent for scheduler tracing verification."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback_results = []

    def test_callback(self, event_name: str, metadata: dict = None):
        """Test callback that records execution."""
        result = {
            "event_name": event_name,
            "metadata": metadata or {},
            "executed_at": datetime.now(),
        }
        self.callback_results.append(result)
        print(f"🎯 Callback executed: {event_name} with metadata: {metadata}")

    def error_callback(self, event_name: str):
        """Callback that throws an error for testing error tracing."""
        self.callback_results.append({"event_name": event_name, "status": "error"})
        print(f"💥 Error callback called: {event_name}")
        raise ValueError(f"Intentional error in {event_name}")


def test_scheduler_tracing_one_time_events(message_bus_manager_fixture):
    """Test scheduler tracing for one-time events."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SCHEDULER TRACING - ONE-TIME EVENTS")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.tracing", TracingTestAgent)

    print("📝 Expected yellow/black trace messages:")
    print("  1. SCHEDULE CALLED")
    print("  2. EVENT CREATION START")
    print("  3. ONE-TIME EVENT CREATED")
    print("  4. EVENT ADDED TO QUEUE")
    print("  5. SCHEDULE COMPLETED")
    print("  6. EVENT EXECUTION START")
    print("  7. EVENT GREENLET SPAWNED")
    print("  8. ONE-TIME EVENT COMPLETED")
    print()

    agent.connect()
    gevent.sleep(0.5)

    # Schedule a one-time event
    now = datetime.now()
    future_time = now + timedelta(seconds=1)

    print(f"📅 Scheduling one-time event for {future_time}")
    agent.core.schedule(
        future_time, agent.test_callback, "trace_test", {"key": "value"}
    )

    # Wait for event to execute
    gevent.sleep(2)

    # Verify callback executed
    assert (
        len(agent.callback_results) == 1
    ), f"Expected 1 callback, got {len(agent.callback_results)}"
    assert agent.callback_results[0]["event_name"] == "trace_test"

    print("✅ One-time event tracing test completed")
    agent.disconnect()


def test_scheduler_tracing_cron_events(message_bus_manager_fixture):
    """Test scheduler tracing for cron events."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SCHEDULER TRACING - CRON EVENTS")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.cron", TracingTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    print("📝 Expected yellow/black trace messages:")
    print("  1. SCHEDULE CALLED (cron)")
    print("  2. EVENT CREATION START")
    print("  3. CRON EVENT CREATED")
    print("  4. EVENT ADDED TO QUEUE")
    print("  5. SCHEDULE COMPLETED")
    print("  6. EVENT EXECUTION START")
    print("  7. EVENT GREENLET SPAWNED")
    print("  8. EVENT RESCHEDULED")
    print()

    # Schedule a cron event (every minute for testing - but we'll only wait briefly)
    print("📅 Scheduling cron event: '*/1 * * * *' (every minute)")
    agent.core.schedule(
        "*/1 * * * *", agent.test_callback, "cron_test", {"type": "cron"}
    )

    # Wait briefly (cron events get rescheduled)
    gevent.sleep(1)

    print("✅ Cron event tracing test completed")
    agent.disconnect()


def test_scheduler_tracing_periodic_events(message_bus_manager_fixture):
    """Test scheduler tracing for periodic (interval) events."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SCHEDULER TRACING - PERIODIC EVENTS")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.periodic", TracingTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    print("📝 Expected yellow/black trace messages:")
    print("  1. SCHEDULE CALLED (periodic)")
    print("  2. EVENT CREATION START")
    print("  3. PERIODIC EVENT CREATED")
    print("  4. EVENT ADDED TO QUEUE")
    print("  5. SCHEDULE COMPLETED")
    print("  6. EVENT EXECUTION START")
    print("  7. EVENT GREENLET SPAWNED")
    print("  8. EVENT RESCHEDULED (multiple times)")
    print()

    # Schedule a periodic event (every 0.8 seconds)
    print("📅 Scheduling periodic event: 0.8 second interval")
    agent.core.schedule(0.8, agent.test_callback, "periodic_test", {"type": "periodic"})

    # Wait for multiple executions
    gevent.sleep(2.5)

    # Should have executed at least 2 times
    assert (
        len(agent.callback_results) >= 2
    ), f"Expected at least 2 callbacks, got {len(agent.callback_results)}"

    print(
        f"✅ Periodic event tracing test completed - {len(agent.callback_results)} executions"
    )
    agent.disconnect()


def test_scheduler_tracing_error_handling(message_bus_manager_fixture):
    """Test scheduler tracing when events throw errors."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SCHEDULER TRACING - ERROR HANDLING")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.errors", TracingTestAgent)
    agent.connect()
    gevent.sleep(0.5)

    print("📝 Expected yellow/black trace messages:")
    print("  1. SCHEDULE CALLED")
    print("  2. EVENT CREATION START")
    print("  3. ONE-TIME EVENT CREATED")
    print("  4. EVENT ADDED TO QUEUE")
    print("  5. SCHEDULE COMPLETED")
    print("  6. EVENT EXECUTION START")
    print("  7. EVENT GREENLET SPAWNED")
    print("  8. ONE-TIME EVENT COMPLETED (event completes even if callback errors)")
    print()

    # Schedule an event that will throw an error
    now = datetime.now()
    future_time = now + timedelta(seconds=1)

    print("📅 Scheduling error-throwing event")
    agent.core.schedule(future_time, agent.error_callback, "error_test")

    # Wait for event to execute (and fail)
    gevent.sleep(2)

    # Verify callback was called (even though it errored)
    assert (
        len(agent.callback_results) == 1
    ), f"Expected 1 callback, got {len(agent.callback_results)}"
    assert agent.callback_results[0]["status"] == "error"

    print("✅ Error handling tracing test completed")
    agent.disconnect()


def test_scheduler_tracing_lifecycle(message_bus_manager_fixture):
    """Test scheduler lifecycle tracing (start/stop)."""
    print("\n" + "=" * 80)
    print("🔍 TESTING SCHEDULER TRACING - LIFECYCLE")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.scheduler.lifecycle", TracingTestAgent)

    print("📝 Expected yellow/black trace messages:")
    print("  1. SCHEDULER STARTING")
    print("  2. SCHEDULER STARTED")
    print("  3. SCHEDULER STOPPING")
    print("  4. SCHEDULER STOPPED")
    print()

    print("🔌 Connecting agent (should start scheduler)")
    agent.connect()
    gevent.sleep(0.5)

    print("🔌 Disconnecting agent (should stop scheduler)")
    agent.disconnect()
    gevent.sleep(0.5)

    print("✅ Scheduler lifecycle tracing test completed")


if __name__ == "__main__":
    import pytest

    print("\n" + "🟨" * 80)
    print("SCHEDULER TRACING TEST SUITE")
    print("Look for YELLOW background with BLACK text messages!")
    print("🟨" * 80)

    pytest.main([__file__, "-v", "-s"])
