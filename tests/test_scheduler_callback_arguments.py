"""Test scheduler callback argument passing and event ordering."""

import logging
from datetime import datetime, timedelta

import gevent

from aems.client.agent import Agent

logging.basicConfig(level=logging.DEBUG)
_log = logging.getLogger(__name__)


class CallbackTestAgent(Agent):
    """Test agent for callback argument testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback_results = []

    def callback_with_args(self, event_name: str, value: int, flag: bool = False, **kwargs):
        """Test callback that captures all arguments passed to it."""
        fired_at = datetime.now()
        result = {
            "event_name": event_name,
            "value": value,
            "flag": flag,
            "kwargs": kwargs,
            "fired_at": fired_at
        }
        self.callback_results.append(result)
        _log.info(
            f"🎯 CALLBACK FIRED: {event_name} with args: value={value}, flag={flag}, "
            f"kwargs={kwargs} at {fired_at}"
        )


def test_two_future_events_with_arguments(message_bus_manager_fixture):
    """Test scheduling two future one-time events with different arguments."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.two_future", CallbackTestAgent)
    agent.connect()

    now = datetime.now()

    # Schedule first event 1 second in future
    future_time_1 = now + timedelta(seconds=1)
    agent.core.schedule(
        future_time_1,
        agent.callback_with_args,
        "first_event",  # event_name
        42,             # value
        True,           # flag
        extra_param="first"
    )

    # Schedule second event 2 seconds in future (should fire after first)
    future_time_2 = now + timedelta(seconds=2)
    agent.core.schedule(
        future_time_2,
        agent.callback_with_args,
        "second_event",  # event_name
        100,            # value
        False,          # flag
        extra_param="second"
    )

    _log.info("Scheduled two future events:")
    _log.info(f"  Event 1: {future_time_1} with args ('first_event', 42, True)")
    _log.info(f"  Event 2: {future_time_2} with args ('second_event', 100, False)")

    # Wait for both events to fire
    gevent.sleep(3)

    # Verify both events fired
    assert len(agent.callback_results) == 2, f"Expected 2 events, got {len(agent.callback_results)}"

    # Verify first event
    first_result = agent.callback_results[0]
    assert first_result["event_name"] == "first_event"
    assert first_result["value"] == 42
    assert first_result["flag"] is True
    assert first_result["kwargs"]["extra_param"] == "first"

    # Verify second event
    second_result = agent.callback_results[1]
    assert second_result["event_name"] == "second_event"
    assert second_result["value"] == 100
    assert second_result["flag"] is False
    assert second_result["kwargs"]["extra_param"] == "second"

    # Verify events fired in correct order
    assert first_result["fired_at"] < second_result["fired_at"], "Events did not fire in correct order"

    _log.info("✅ Two future events test passed - events fired in correct order with correct arguments")
    agent.disconnect()


def test_past_and_future_events_with_arguments(message_bus_manager_fixture):
    """Test scheduling one past event (should fire immediately) and one future event."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.past_future", CallbackTestAgent)
    agent.connect()

    now = datetime.now()

    # Schedule past event (should fire immediately)
    past_time = now - timedelta(seconds=10)
    agent.core.schedule(
        past_time,
        agent.callback_with_args,
        "past_event",   # event_name
        999,            # value
        True,           # flag
        status="past"
    )

    # Schedule future event
    future_time = now + timedelta(seconds=1.5)
    agent.core.schedule(
        future_time,
        agent.callback_with_args,
        "future_event", # event_name
        123,            # value
        False,          # flag
        status="future"
    )

    _log.info("Scheduled past and future events:")
    _log.info(f"  Past Event: {past_time} with args ('past_event', 999, True)")
    _log.info(f"  Future Event: {future_time} with args ('future_event', 123, False)")

    # Give a moment for past event to fire immediately
    gevent.sleep(0.5)

    # Past event should have fired immediately
    assert len(agent.callback_results) == 1, (
        f"Expected 1 event (past) to have fired immediately, got {len(agent.callback_results)}"
    )

    past_result = agent.callback_results[0]
    assert past_result["event_name"] == "past_event"
    assert past_result["value"] == 999
    assert past_result["flag"] is True
    assert past_result["kwargs"]["status"] == "past"

    # Wait for future event
    gevent.sleep(1.5)

    # Now both events should have fired
    assert len(agent.callback_results) == 2, f"Expected 2 events total, got {len(agent.callback_results)}"

    # Verify future event
    future_result = agent.callback_results[1]
    assert future_result["event_name"] == "future_event"
    assert future_result["value"] == 123
    assert future_result["flag"] is False
    assert future_result["kwargs"]["status"] == "future"

    # Verify timing: past event should have fired much earlier than future event
    time_diff = (future_result["fired_at"] - past_result["fired_at"]).total_seconds()
    assert time_diff >= 1.0, f"Time difference between events should be >= 1 second, got {time_diff}"

    _log.info("✅ Past and future events test passed - past event fired immediately, future event fired later")
    agent.disconnect()


def test_complex_argument_combinations(message_bus_manager_fixture):
    """Test scheduler with complex argument combinations including nested data structures."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.complex_args", CallbackTestAgent)
    agent.connect()

    def complex_callback(event_id, data_dict, data_list, *args, **kwargs):
        """Callback that accepts complex data structures."""
        result = {
            "event_id": event_id,
            "data_dict": data_dict,
            "data_list": data_list,
            "args": args,
            "kwargs": kwargs,
            "fired_at": datetime.now()
        }
        agent.callback_results.append(result)
        _log.info(
            f"🎯 COMPLEX CALLBACK: {event_id} with dict={data_dict}, "
            f"list={data_list}, args={args}, kwargs={kwargs}"
        )

    # Schedule event with complex arguments
    now = datetime.now()
    future_time = now + timedelta(seconds=1)

    test_dict = {"key1": "value1", "nested": {"inner": 42}}
    test_list = [1, 2, {"item": "data"}]

    agent.core.schedule(
        future_time,
        complex_callback,
        "complex_test",      # event_id
        test_dict,           # data_dict
        test_list,           # data_list
        "extra_arg1",        # *args
        "extra_arg2",        # *args
        param1="kwarg1",     # **kwargs
        param2={"nested": "kwarg"}
    )

    _log.info("Scheduled complex event with:")
    _log.info(f"  Dict: {test_dict}")
    _log.info(f"  List: {test_list}")
    _log.info("  Args: ('extra_arg1', 'extra_arg2')")
    _log.info("  Kwargs: param1='kwarg1', param2={'nested': 'kwarg'}")

    # Wait for event
    gevent.sleep(2)

    # Verify event fired with correct arguments
    assert len(agent.callback_results) == 1, f"Expected 1 event, got {len(agent.callback_results)}"

    result = agent.callback_results[0]
    assert result["event_id"] == "complex_test"
    assert result["data_dict"] == test_dict
    assert result["data_list"] == test_list
    assert result["args"] == ("extra_arg1", "extra_arg2")
    assert result["kwargs"]["param1"] == "kwarg1"
    assert result["kwargs"]["param2"] == {"nested": "kwarg"}

    _log.info("✅ Complex arguments test passed - all data structures preserved")
    agent.disconnect()


def test_event_ordering_with_microsecond_precision(message_bus_manager_fixture):
    """Test that events scheduled very close together still fire in correct order."""
    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("test.microsecond_order", CallbackTestAgent)
    agent.connect()

    now = datetime.now()
    base_time = now + timedelta(seconds=1)

    # Schedule 5 events with microsecond differences
    for i in range(5):
        event_time = base_time + timedelta(microseconds=i * 1000)  # 1ms apart
        agent.core.schedule(
            event_time,
            agent.callback_with_args,
            f"event_{i}",     # event_name
            i,               # value (index)
            i % 2 == 0       # flag (alternating True/False)
        )
        _log.info(f"Scheduled event_{i} for {event_time}")

    # Wait for all events
    gevent.sleep(2)

    # Verify all events fired
    assert len(agent.callback_results) == 5, f"Expected 5 events, got {len(agent.callback_results)}"

    # Verify events fired in correct order
    for i, result in enumerate(agent.callback_results):
        assert result["event_name"] == f"event_{i}", f"Event {i} fired out of order: got {result['event_name']}"
        assert result["value"] == i, f"Event {i} had wrong value: got {result['value']}"
        assert result["flag"] == (i % 2 == 0), f"Event {i} had wrong flag: got {result['flag']}"

    # Verify timestamps are in ascending order
    for i in range(1, len(agent.callback_results)):
        prev_time = agent.callback_results[i-1]["fired_at"]
        curr_time = agent.callback_results[i]["fired_at"]
        assert prev_time <= curr_time, f"Events {i-1} and {i} fired out of order"

    _log.info("✅ Microsecond precision ordering test passed - all events fired in correct sequence")
    agent.disconnect()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
