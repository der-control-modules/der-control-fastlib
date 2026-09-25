"""Test scheduler handling of datetime objects for one-time events."""

import logging
from datetime import datetime, timedelta

import gevent
import pytest

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class OccupancyAgent(Agent):
    """Test agent that simulates occupancy override scheduling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_actions = []
        self.override_greenlets = {}

    def _do_control_action(self, gid: str, state: str):
        """Simulate control action for occupancy changes."""
        _log.info(f"OVERRIDE _do_control_action {gid} changed to {state}")
        self.control_actions.append((gid, state, datetime.now()))

        if state == "UNOCCUPIED":
            # Clean up the greenlet references when ending override
            if gid in self.override_greenlets:
                self.override_greenlets.pop(gid)

    def schedule_override(self, gid: str, start_time: datetime, end_time: datetime):
        """Schedule an occupancy override with start and end times."""
        _log.info(f"Scheduling override {gid}: start={start_time}, end={end_time}")

        # Schedule start and end events
        overrides = [
            self.core.schedule(start_time, self._do_control_action, gid, "OCCUPIED"),
            self.core.schedule(end_time, self._do_control_action, gid, "UNOCCUPIED"),
        ]

        _log.info(f"Current OVRR - {gid} -- {overrides}")
        self.override_greenlets[gid] = overrides
        return overrides


def test_datetime_one_time_scheduling(message_bus_manager_fixture):
    """Test that datetime objects schedule one-time events, not recurring cron jobs."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    # Create and connect the agent
    agent = OccupancyAgent("test.occupancy", host="127.0.0.1", port=manager.port)
    agent.connect()
    agent.core._scheduler.start()  # Start the scheduler

    # Schedule events 2 seconds and 4 seconds from now
    now = datetime.now()
    start_time = now + timedelta(seconds=2)
    end_time = now + timedelta(seconds=4)

    gid = "test_override_1"
    schedulers = agent.schedule_override(gid, start_time, end_time)

    # Verify schedulers were created
    assert len(schedulers) == 2
    assert gid in agent.override_greenlets

    # Wait for start event to fire
    gevent.sleep(2.5)

    # Check that start event fired
    assert len(agent.control_actions) == 1
    assert agent.control_actions[0][0] == gid
    assert agent.control_actions[0][1] == "OCCUPIED"

    # Wait for end event to fire
    gevent.sleep(2)

    # Check that end event fired
    assert len(agent.control_actions) == 2
    assert agent.control_actions[1][0] == gid
    assert agent.control_actions[1][1] == "UNOCCUPIED"

    # Verify greenlet was cleaned up
    assert gid not in agent.override_greenlets

    # Wait a bit more to ensure no recurring events
    gevent.sleep(3)

    # Should still only have 2 events (not recurring)
    assert len(agent.control_actions) == 2

    agent.disconnect()


def test_specific_datetime_scheduling(message_bus_manager_fixture):
    """Test scheduling with specific datetime objects (like 8am and 6pm today)."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyAgent("test.occupancy2", host="127.0.0.1", port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    # Create specific times for today (but in the near future for testing)
    now = datetime.now()

    # Schedule 1 second from now as "8am" and 3 seconds from now as "6pm"
    start_time = now + timedelta(seconds=1)
    end_time = now + timedelta(seconds=3)

    gid = "daily_override"
    _log.info(f"Scheduling daily override: 8am={start_time}, 6pm={end_time}")

    agent.schedule_override(gid, start_time, end_time)

    # Wait for events to fire
    gevent.sleep(1.5)
    assert len(agent.control_actions) == 1
    assert agent.control_actions[0][1] == "OCCUPIED"

    gevent.sleep(2)
    assert len(agent.control_actions) == 2
    assert agent.control_actions[1][1] == "UNOCCUPIED"

    # Verify this was a one-time event, not recurring
    # Wait for what would be the next day's 8am (in our test, just wait a bit)
    gevent.sleep(5)

    # Should still only have 2 events
    assert len(agent.control_actions) == 2

    agent.disconnect()


def test_multiple_overrides(message_bus_manager_fixture):
    """Test multiple overlapping overrides."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyAgent("test.occupancy3", host="127.0.0.1", port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    now = datetime.now()

    # Schedule multiple overrides
    gid1 = "override_1"
    agent.schedule_override(
        gid1, now + timedelta(seconds=1), now + timedelta(seconds=3)
    )

    gid2 = "override_2"
    agent.schedule_override(
        gid2, now + timedelta(seconds=2), now + timedelta(seconds=4)
    )

    # Wait for all events
    gevent.sleep(5)

    # Should have 4 events total (2 starts, 2 ends)
    assert len(agent.control_actions) == 4

    # Verify the order and greenlet cleanup
    assert gid1 not in agent.override_greenlets
    assert gid2 not in agent.override_greenlets

    agent.disconnect()


def test_past_datetime_handling(message_bus_manager_fixture):
    """Test that scheduling a datetime in the past fires immediately or is handled properly."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyAgent("test.occupancy4", host="127.0.0.1", port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    # Schedule an event in the past
    past_time = datetime.now() - timedelta(seconds=10)
    future_time = datetime.now() + timedelta(seconds=2)

    gid = "past_override"
    agent.schedule_override(gid, past_time, future_time)

    # Past event should fire immediately or be handled appropriately
    gevent.sleep(0.5)

    # Check if past event fired immediately
    assert len(agent.control_actions) >= 1
    assert agent.control_actions[0][1] == "OCCUPIED"

    # Wait for end event
    gevent.sleep(2)

    assert len(agent.control_actions) == 2
    assert agent.control_actions[1][1] == "UNOCCUPIED"

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
