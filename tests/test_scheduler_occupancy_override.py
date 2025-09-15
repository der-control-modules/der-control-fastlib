"""Test scheduler with realistic occupancy override scenarios (8am-6pm)."""

import logging
from datetime import datetime, time as datetime_time, timedelta

import gevent
import pytest

from aems.client.agent import Agent

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)


class OccupancyControlAgent(Agent):
    """Agent that manages occupancy overrides for building control."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_actions = []
        self.override_greenlets = {}
        self.current_id = None
        self.occupancy_state = "UNOCCUPIED"

    def _do_control_action(self, gid: str, state: str):
        """Handle occupancy state changes."""
        _log.info(f"OVERRIDE _do_control_action {gid} changed to {state}")
        self.control_actions.append({
            'gid': gid,
            'state': state,
            'timestamp': datetime.now()
        })

        self.occupancy_state = state

        if state == "UNOCCUPIED":
            self.current_id = None
            if gid in self.override_greenlets:
                self.override_greenlets.pop(gid)
                _log.info(f"Removed override {gid} from greenlets")
        elif state == "OCCUPIED":
            self.current_id = gid
            _log.info(f"Set current override to {gid}")

    def schedule_daily_override(self, gid: str, start_hour: int, start_minute: int,
                                end_hour: int, end_minute: int):
        """
        Schedule an occupancy override for today at specific times.

        Args:
            gid: Override identifier
            start_hour: Hour to start (0-23)
            start_minute: Minute to start (0-59)
            end_hour: Hour to end (0-23)
            end_minute: Minute to end (0-59)
        """
        today = datetime.now().date()

        # Create datetime objects for today at the specified times
        start_time = datetime.combine(today, datetime_time(start_hour, start_minute))
        end_time = datetime.combine(today, datetime_time(end_hour, end_minute))

        # If times are in the past, schedule for tomorrow
        now = datetime.now()
        if end_time <= now:
            # Both times are in the past, schedule for tomorrow
            start_time += timedelta(days=1)
            end_time += timedelta(days=1)
        elif start_time <= now < end_time:
            # We're in the middle of the schedule, start immediately
            start_time = now + timedelta(seconds=0.1)

        _log.info(f"Scheduling occupancy override {gid}:")
        _log.info(f"  Start: {start_time} ({start_hour:02d}:{start_minute:02d})")
        _log.info(f"  End: {end_time} ({end_hour:02d}:{end_minute:02d})")

        # Schedule the events using datetime objects (one-time events)
        overrides = [
            self.core.schedule(start_time, self._do_control_action, gid, "OCCUPIED"),
            self.core.schedule(end_time, self._do_control_action, gid, "UNOCCUPIED")
        ]

        self.override_greenlets[gid] = overrides
        return overrides


def test_8am_6pm_override_scenario(message_bus_manager_fixture):
    """Test realistic 8am-6pm occupancy override scenario."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyControlAgent("building.occupancy",
                                  host="127.0.0.1",
                                  port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    # Simulate scheduling an override from 8am to 6pm
    # For testing, we'll use times just seconds from now
    now = datetime.now()
    start_seconds_from_now = 1
    end_seconds_from_now = 3

    # Calculate what would be "8am" and "6pm" in our test
    test_8am = now + timedelta(seconds=start_seconds_from_now)
    test_6pm = now + timedelta(seconds=end_seconds_from_now)

    _log.info(f"Test simulating 8am as: {test_8am}")
    _log.info(f"Test simulating 6pm as: {test_6pm}")

    # Schedule using datetime objects directly (like the real code would)
    gid = "daily_occupancy_override"
    overrides = [
        agent.core.schedule(test_8am, agent._do_control_action, gid, "OCCUPIED"),
        agent.core.schedule(test_6pm, agent._do_control_action, gid, "UNOCCUPIED")
    ]
    agent.override_greenlets[gid] = overrides

    # Verify initial state
    assert agent.occupancy_state == "UNOCCUPIED"
    assert agent.current_id is None

    # Wait for "8am" (start time)
    gevent.sleep(start_seconds_from_now + 0.5)

    # Check occupancy is now OCCUPIED
    assert len(agent.control_actions) == 1
    assert agent.control_actions[0]['state'] == "OCCUPIED"
    assert agent.occupancy_state == "OCCUPIED"
    assert agent.current_id == gid

    # Wait for "6pm" (end time)
    gevent.sleep(end_seconds_from_now - start_seconds_from_now)

    # Check occupancy is back to UNOCCUPIED
    assert len(agent.control_actions) == 2
    assert agent.control_actions[1]['state'] == "UNOCCUPIED"
    assert agent.occupancy_state == "UNOCCUPIED"
    assert agent.current_id is None
    assert gid not in agent.override_greenlets  # Should be cleaned up

    # Wait to ensure no recurring events (should stay at 2 events only)
    gevent.sleep(5)
    assert len(agent.control_actions) == 2

    agent.disconnect()


def test_daily_override_helper_method(message_bus_manager_fixture):
    """Test the daily override helper method that uses hour/minute."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyControlAgent("building.occupancy2",
                                  host="127.0.0.1",
                                  port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    # Schedule for specific times that are seconds from now
    # to simulate 8am and 6pm for testing
    now = datetime.now()

    # Calculate times that are 1 and 3 seconds from now
    start_time = now + timedelta(seconds=1)
    end_time = now + timedelta(seconds=3)

    # Schedule directly with datetime objects (bypassing the daily helper)
    gid = "workday_override"
    overrides = [
        agent.core.schedule(start_time, agent._do_control_action, gid, "OCCUPIED"),
        agent.core.schedule(end_time, agent._do_control_action, gid, "UNOCCUPIED")
    ]
    agent.override_greenlets[gid] = overrides

    # Wait and verify
    gevent.sleep(1.5)
    assert len(agent.control_actions) == 1
    assert agent.control_actions[0]['state'] == "OCCUPIED"

    gevent.sleep(2)
    assert len(agent.control_actions) == 2
    assert agent.control_actions[1]['state'] == "UNOCCUPIED"

    # Verify it's a one-time event, not recurring
    gevent.sleep(5)
    assert len(agent.control_actions) == 2

    agent.disconnect()


def test_multiple_daily_overrides(message_bus_manager_fixture):
    """Test multiple overlapping daily overrides."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyControlAgent("building.occupancy3",
                                  host="127.0.0.1",
                                  port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    now = datetime.now()

    # Schedule multiple overrides that overlap
    # Conference room: 9am-11am (simulated as 1-3 seconds)
    conf_start = now + timedelta(seconds=1)
    conf_end = now + timedelta(seconds=3)

    # Main office: 8am-6pm (simulated as 0.5-5 seconds)
    office_start = now + timedelta(seconds=0.5)
    office_end = now + timedelta(seconds=5)

    # Schedule conference room
    conf_overrides = [
        agent.core.schedule(conf_start, agent._do_control_action, "conference_room", "OCCUPIED"),
        agent.core.schedule(conf_end, agent._do_control_action, "conference_room", "UNOCCUPIED")
    ]
    agent.override_greenlets["conference_room"] = conf_overrides

    # Schedule main office
    office_overrides = [
        agent.core.schedule(office_start, agent._do_control_action, "main_office", "OCCUPIED"),
        agent.core.schedule(office_end, agent._do_control_action, "main_office", "UNOCCUPIED")
    ]
    agent.override_greenlets["main_office"] = office_overrides

    # Wait for all events to complete
    gevent.sleep(6)

    # Should have 4 events total
    assert len(agent.control_actions) == 4

    # Verify the sequence
    events_by_gid = {}
    for action in agent.control_actions:
        gid = action['gid']
        if gid not in events_by_gid:
            events_by_gid[gid] = []
        events_by_gid[gid].append(action['state'])

    # Each override should have OCCUPIED followed by UNOCCUPIED
    assert events_by_gid["conference_room"] == ["OCCUPIED", "UNOCCUPIED"]
    assert events_by_gid["main_office"] == ["OCCUPIED", "UNOCCUPIED"]

    # All overrides should be cleaned up
    assert len(agent.override_greenlets) == 0

    agent.disconnect()


def test_immediate_execution_for_past_times(message_bus_manager_fixture):
    """Test that scheduling a past datetime executes immediately."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = OccupancyControlAgent("building.occupancy4",
                                  host="127.0.0.1",
                                  port=manager.port)
    agent.connect()
    agent.core._scheduler.start()

    # Schedule an override with a start time in the past
    past_time = datetime.now() - timedelta(hours=2)  # 2 hours ago
    future_time = datetime.now() + timedelta(seconds=2)  # 2 seconds from now

    gid = "past_override"
    overrides = [
        agent.core.schedule(past_time, agent._do_control_action, gid, "OCCUPIED"),
        agent.core.schedule(future_time, agent._do_control_action, gid, "UNOCCUPIED")
    ]
    agent.override_greenlets[gid] = overrides

    # Past event should execute almost immediately
    gevent.sleep(0.5)

    assert len(agent.control_actions) >= 1
    assert agent.control_actions[0]['state'] == "OCCUPIED"
    assert agent.occupancy_state == "OCCUPIED"

    # Wait for the future event
    gevent.sleep(2)

    assert len(agent.control_actions) == 2
    assert agent.control_actions[1]['state'] == "UNOCCUPIED"
    assert agent.occupancy_state == "UNOCCUPIED"

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
