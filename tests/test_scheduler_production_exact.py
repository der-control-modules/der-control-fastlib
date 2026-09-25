"""Test that exactly replicates the production scenario with existing queue."""

import logging
import time
from datetime import datetime, timedelta

import gevent
import pytest

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class ProductionScenarioAgent(Agent):
    """Agent that simulates the exact production scenario."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_actions = []
        self.other_events = []

    def _do_control_action(self, gid: str, state: str):
        """The control action that should fire."""
        action_time = datetime.now()
        self.control_actions.append(
            {"gid": gid, "state": state, "time": action_time, "timestamp": time.time()}
        )
        print(f"CONTROL ACTION FIRED: gid={gid}, state={state} at {action_time}")

    def some_other_task(self, task_id: str):
        """Some other scheduled task."""
        self.other_events.append(
            {"task_id": task_id, "time": datetime.now(), "timestamp": time.time()}
        )
        print(f"Other task executed: {task_id}")


def test_production_scenario_with_existing_queue(message_bus_manager_fixture):
    """Test the exact production scenario: agent with existing scheduled events, then add past event."""
    print("\n" + "=" * 80)
    print("PRODUCTION SCENARIO: EXISTING QUEUE + PAST EVENT")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.production.exact", ProductionScenarioAgent)
    agent.connect()
    gevent.sleep(0.5)

    now = datetime.now()
    current_timestamp = time.time()

    # Step 1: Add multiple future events to simulate production queue (11-12 events)
    print("\nStep 1: Creating production-like queue with 12 future events...")

    # Schedule various future events like in production
    for i in range(12):
        # Mix of near and far future events
        if i < 4:
            # Near future (1-5 minutes)
            delay = 60 + i * 60
        elif i < 8:
            # Medium future (10-30 minutes)
            delay = 600 + (i - 4) * 300
        else:
            # Far future (1-2 hours)
            delay = 3600 + (i - 8) * 900

        future_time = now + timedelta(seconds=delay)
        event_name = f"task_{i}"
        agent.core.schedule(future_time, agent.some_other_task, event_name)
        print(f"  Scheduled {event_name} for {future_time} (+{delay}s)")

    # Check queue state
    queue_size = len(agent.core._scheduler._event_queue)
    print("\nQueue state after adding future events:")
    print(f"  Queue size: {queue_size}")
    if agent.core._scheduler._event_queue:
        next_event = agent.core._scheduler._event_queue[0]
        print(
            f"  Next event: {next_event.name} at {datetime.fromtimestamp(next_event.next_time)}"
        )

    # Let the scheduler run for a bit
    print("\nStep 2: Let agent run for 2 seconds (simulating production runtime)...")
    gevent.sleep(2)

    # Step 3: Now add the occupancy override with past start time (like in production)
    print("\nStep 3: Adding occupancy override with PAST start time (8am today)...")

    # Create 8am and 6pm times for today
    today_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    today_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    print(f"  Current time: {now}")
    print(f"  8AM time: {today_8am} (is past? {today_8am < now})")
    print(f"  6PM time: {today_6pm} (is past? {today_6pm < now})")

    # Record state before scheduling
    actions_before = len(agent.control_actions)
    queue_before = len(agent.core._scheduler._event_queue)

    # Schedule the occupancy override (8am should be in the past)
    print("\nCalling agent.core.schedule() for 8AM (past event)...")
    agent.core.schedule(today_8am, agent._do_control_action, "zone_1", "occupied")

    print("Calling agent.core.schedule() for 6PM...")
    agent.core.schedule(today_6pm, agent._do_control_action, "zone_1", "unoccupied")

    # Check immediate queue state
    queue_after = len(agent.core._scheduler._event_queue)
    print("\nQueue state immediately after scheduling:")
    print(f"  Queue size: {queue_before} -> {queue_after}")
    if agent.core._scheduler._event_queue:
        next_event = agent.core._scheduler._event_queue[0]
        print(
            f"  Next event: {next_event.name} at {datetime.fromtimestamp(next_event.next_time)}"
        )
        print(f"  Is it the 8AM event? {next_event.name == 'zone_1'}")

    # Wait for past event to fire
    print("\nStep 4: Waiting 3 seconds for past event to fire...")
    gevent.sleep(3)

    # Check results
    actions_after = len(agent.control_actions)
    print("\nFinal results:")
    print(f"  Control actions before: {actions_before}")
    print(f"  Control actions after: {actions_after}")
    print(f"  Queue size: {len(agent.core._scheduler._event_queue)}")

    if agent.control_actions:
        print("\nControl actions that fired:")
        for action in agent.control_actions:
            time_diff = action["timestamp"] - current_timestamp
            print(
                f"  - {action['gid']}: {action['state']} at {action['time']} (+{time_diff:.2f}s from test start)"
            )
    else:
        print("\nNO CONTROL ACTIONS FIRED!")

    # Check for the specific 8AM event
    past_event_fired = any(
        action["state"] == "occupied" and action["gid"] == "zone_1"
        for action in agent.control_actions
    )

    if not past_event_fired:
        print("\nBUG REPRODUCED: Past 8AM event did not fire with existing queue!")

        # Debug info
        print("\nDebug information:")
        print(
            f"  Scheduler greenlet exists: {agent.core._scheduler._scheduler_greenlet is not None}"
        )
        if agent.core._scheduler._scheduler_greenlet:
            print(
                f"  Scheduler greenlet dead: {agent.core._scheduler._scheduler_greenlet.dead}"
            )
        print(f"  Events in queue: {len(agent.core._scheduler._event_queue)}")

        # Check if the event is still in the queue
        for i, event in enumerate(agent.core._scheduler._event_queue):
            if "zone_1" in event.name or "_do_control_action" in str(event.function):
                event_time = datetime.fromtimestamp(event.next_time)
                print(
                    f"  Found control event at position {i}: {event.name}, time: {event_time}"
                )
    else:
        print("\nPast event fired correctly even with existing queue")

    agent.disconnect()

    # Assert to make test fail if bug is present
    assert (
        past_event_fired
    ), "Past 8AM event should have fired immediately even with existing queue!"


def test_verify_scheduler_wakeup_with_queue(message_bus_manager_fixture):
    """Verify the scheduler wakes up when a past event is added to existing queue."""
    print("\n" + "=" * 80)
    print("TESTING SCHEDULER WAKEUP WITH EXISTING QUEUE")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.wakeup.queue", ProductionScenarioAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Add some far future events
    now = datetime.now()
    for i in range(5):
        future_time = now + timedelta(hours=i + 1)
        agent.core.schedule(future_time, agent.some_other_task, f"future_{i}")

    print(
        f"Added 5 future events, queue size: {len(agent.core._scheduler._event_queue)}"
    )

    # Let scheduler settle
    gevent.sleep(1)

    # Now add a past event
    past_time = now - timedelta(minutes=10)
    print(f"\nAdding past event for {past_time}")

    actions_before = len(agent.control_actions)
    agent.core.schedule(past_time, agent._do_control_action, "past_zone", "test")

    # Give it time to process
    gevent.sleep(2)

    actions_after = len(agent.control_actions)
    print(f"Actions: {actions_before} -> {actions_after}")

    assert actions_after > actions_before, "Past event should have fired!"
    print("Scheduler correctly woke up and processed past event")

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
