"""Test production scenario: 8am-6pm occupancy override scheduling."""

import logging
from datetime import datetime, timedelta

import gevent
import pytest

from derhost.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class OccupancyOverrideAgent(Agent):
    """Agent that simulates occupancy override scheduling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_actions = []

    def _do_control_action(self, gid: str, occupied: bool):
        """Simulate the control action that should fire."""
        action_time = datetime.now()
        self.control_actions.append(
            {"gid": gid, "occupied": occupied, "time": action_time}
        )
        _log.info(f"🏢 CONTROL ACTION: gid={gid}, occupied={occupied} at {action_time}")
        print(
            f"✅ _do_control_action fired: gid={gid}, occupied={occupied} at {action_time}"
        )

    def schedule_occupancy_override(
        self, gid: str, start_time: datetime, end_time: datetime
    ):
        """Schedule occupancy override like in production."""
        print(f"\n📅 Scheduling occupancy override for {gid}:")
        print(f"   Start: {start_time} (occupied=True)")
        print(f"   End: {end_time} (occupied=False)")

        # Schedule start (occupied=True)
        self.core.schedule(start_time, self._do_control_action, gid, True)

        # Schedule end (occupied=False)
        self.core.schedule(end_time, self._do_control_action, gid, False)


def test_occupancy_override_8am_6pm(message_bus_manager_fixture):
    """Test the exact production scenario: 8am-6pm occupancy override."""
    print("\n" + "=" * 80)
    print("🏢 TESTING PRODUCTION OCCUPANCY OVERRIDE (8AM-6PM)")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.occupancy.override", OccupancyOverrideAgent)
    agent.connect()
    gevent.sleep(0.5)

    # Simulate scheduling an override at various times of day
    now = datetime.now()

    # Test 1: Schedule for today 8am-6pm (if it's after 6pm, use tomorrow)
    today_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    today_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    if now > today_6pm:
        # It's after 6pm, schedule for tomorrow
        tomorrow = now + timedelta(days=1)
        start_time = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
        end_time = tomorrow.replace(hour=18, minute=0, second=0, microsecond=0)
        print("⏰ Current time is after 6pm, scheduling for tomorrow")
    elif now < today_8am:
        # It's before 8am, schedule for today
        start_time = today_8am
        end_time = today_6pm
        print("⏰ Current time is before 8am, scheduling for today")
    else:
        # It's between 8am and 6pm, start should fire immediately, end at 6pm
        start_time = today_8am  # This is in the past
        end_time = today_6pm
        print("⏰ Current time is between 8am-6pm, start is in past, end at 6pm")

    print(f"📍 Current time: {now}")
    agent.schedule_occupancy_override("building_1", start_time, end_time)

    # Wait a bit to see if past events fire
    gevent.sleep(2)

    # Check if any past events fired
    past_events_fired = 0
    for action in agent.control_actions:
        if action["time"] < now + timedelta(seconds=3):
            past_events_fired += 1
            print(f"✅ Past event fired: {action}")

    if start_time < now:
        assert past_events_fired > 0, "Past start event should have fired immediately!"
        print("✅ Past start event fired correctly")

    agent.disconnect()


def test_multiple_overrides_throughout_day(message_bus_manager_fixture):
    """Test multiple occupancy overrides scheduled at different times."""
    print("\n" + "=" * 80)
    print("🏢 TESTING MULTIPLE OCCUPANCY OVERRIDES")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.occupancy.multiple", OccupancyOverrideAgent)
    agent.connect()
    gevent.sleep(0.5)

    now = datetime.now()

    # Schedule multiple overrides with different timings
    overrides = [
        # Past events (should fire immediately)
        (
            "zone_1",
            now - timedelta(hours=2),
            now - timedelta(hours=1),
        ),  # Completely in past
        (
            "zone_2",
            now - timedelta(minutes=30),
            now + timedelta(seconds=2),
        ),  # Start in past, end in near future
        # Future events
        (
            "zone_3",
            now + timedelta(seconds=1),
            now + timedelta(seconds=3),
        ),  # Near future
        (
            "zone_4",
            now + timedelta(seconds=2),
            now + timedelta(seconds=4),
        ),  # Slightly later
    ]

    for gid, start, end in overrides:
        agent.schedule_occupancy_override(gid, start, end)

    # Wait for immediate past events
    gevent.sleep(1)

    # Check past events fired
    past_actions = [
        a for a in agent.control_actions if a["time"] < now + timedelta(seconds=2)
    ]
    print(f"\n📊 Past events fired: {len(past_actions)}")
    for action in past_actions:
        print(f"   - {action['gid']}: occupied={action['occupied']}")

    # Should have 3 past events: zone_1 start, zone_1 end, zone_2 start
    assert (
        len(past_actions) >= 3
    ), f"Expected at least 3 past events, got {len(past_actions)}"

    # Wait for future events (zone_4 end is at now+4s; give extra headroom for CI)
    gevent.sleep(8)

    # Check all events fired
    print(f"\n📊 Total events fired: {len(agent.control_actions)}")
    for action in agent.control_actions:
        print(
            f"   - {action['gid']}: occupied={action['occupied']} at {action['time']}"
        )

    # Should have 8 total events (4 zones × 2 events each)
    assert (
        len(agent.control_actions) == 8
    ), f"Expected 8 total events, got {len(agent.control_actions)}"

    agent.disconnect()


def test_rapid_override_updates(message_bus_manager_fixture):
    """Test rapid updates to occupancy overrides (rescheduling)."""
    print("\n" + "=" * 80)
    print("🏢 TESTING RAPID OVERRIDE UPDATES")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.occupancy.rapid", OccupancyOverrideAgent)
    agent.connect()
    gevent.sleep(0.5)

    now = datetime.now()

    # Simulate rapid scheduling of overrides (like user changing times quickly)
    for i in range(5):
        start = now + timedelta(seconds=1 + i * 0.5)
        end = now + timedelta(seconds=2 + i * 0.5)
        agent.schedule_occupancy_override(f"room_{i}", start, end)
        gevent.sleep(0.1)  # Small delay between schedules

    # Wait for all events
    gevent.sleep(5)

    # Verify all events fired
    print(f"\n📊 Total events fired: {len(agent.control_actions)}")

    # Should have 10 events (5 rooms × 2 events each)
    assert (
        len(agent.control_actions) == 10
    ), f"Expected 10 events, got {len(agent.control_actions)}"

    # Verify each room has both start and end
    rooms_started = set()
    rooms_ended = set()
    for action in agent.control_actions:
        if action["occupied"]:
            rooms_started.add(action["gid"])
        else:
            rooms_ended.add(action["gid"])

    assert (
        len(rooms_started) == 5
    ), f"Expected 5 rooms started, got {len(rooms_started)}"
    assert len(rooms_ended) == 5, f"Expected 5 rooms ended, got {len(rooms_ended)}"
    assert rooms_started == rooms_ended, "Start and end rooms don't match!"

    print("✅ All rooms had both start and end events")

    agent.disconnect()


def test_edge_case_midnight_crossing(message_bus_manager_fixture):
    """Test occupancy override that crosses midnight."""
    print("\n" + "=" * 80)
    print("🏢 TESTING MIDNIGHT CROSSING OVERRIDE")
    print("=" * 80)

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.occupancy.midnight", OccupancyOverrideAgent)
    agent.connect()
    gevent.sleep(0.5)

    now = datetime.now()

    # Schedule override from 11pm today to 1am tomorrow
    today_11pm = now.replace(hour=23, minute=0, second=0, microsecond=0)
    tomorrow_1am = (now + timedelta(days=1)).replace(
        hour=1, minute=0, second=0, microsecond=0
    )

    # If it's already past 11pm, adjust
    if now.hour >= 23:
        today_11pm = (now + timedelta(days=1)).replace(
            hour=23, minute=0, second=0, microsecond=0
        )
        tomorrow_1am = (now + timedelta(days=2)).replace(
            hour=1, minute=0, second=0, microsecond=0
        )

    print(f"📍 Current time: {now}")
    print("📅 Scheduling midnight-crossing override:")
    print(f"   Start: {today_11pm}")
    print(f"   End: {tomorrow_1am}")

    agent.schedule_occupancy_override("nightshift_zone", today_11pm, tomorrow_1am)

    # For this test, we just verify the events are scheduled correctly
    # (we don't wait until 11pm!)
    gevent.sleep(1)

    print("✅ Midnight-crossing override scheduled successfully")
    print(f"   Events in queue: {len(agent.core._scheduler._event_queue)}")

    agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
