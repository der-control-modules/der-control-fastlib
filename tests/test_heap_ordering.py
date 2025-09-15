"""Test to verify heap ordering behavior with past events."""

import heapq
import time
from datetime import datetime


class SimpleEvent:
    """Simple event for testing heap behavior."""

    def __init__(self, name, next_time):
        self.name = name
        self.next_time = next_time

    def __lt__(self, other):
        return self.next_time < other.next_time

    def __repr__(self):
        return f"Event({self.name}, {datetime.fromtimestamp(self.next_time)})"


def test_heap_ordering():
    """Test that heapq properly orders events even when past event is added later."""
    now = time.time()

    # Create initial queue with future events
    queue = []

    # Add some future events
    event1 = SimpleEvent("future_1min", now + 60)
    event2 = SimpleEvent("future_2min", now + 120)
    event3 = SimpleEvent("future_3min", now + 180)

    heapq.heappush(queue, event1)
    heapq.heappush(queue, event2)
    heapq.heappush(queue, event3)

    print("Initial queue (3 future events):")
    for i, event in enumerate(queue):
        print(f"  Position {i}: {event}")
    print(f"  Top of heap: {queue[0]}")

    # Now add a past event
    past_event = SimpleEvent("past_5min", now - 300)
    heapq.heappush(queue, past_event)

    print("\nAfter adding past event:")
    for i, event in enumerate(queue):
        print(f"  Position {i}: {event}")
    print(f"  Top of heap: {queue[0]}")

    # Verify the past event is at position 0
    assert queue[0] == past_event, f"Past event should be at top of heap, but got {queue[0]}"
    print("\n✅ Past event correctly moved to top of heap")

    # Now test with many events
    print("\n" + "="*60)
    print("Testing with large queue (like production):")

    large_queue = []
    base_time = time.time()

    # Add 10 future events at various times
    for i in range(10):
        event = SimpleEvent(f"future_{i}", base_time + (i + 1) * 30)
        heapq.heappush(large_queue, event)

    print("\nLarge queue with 10 future events:")
    print(f"  Queue size: {len(large_queue)}")
    print("  Top 3 events:")
    for i in range(min(3, len(large_queue))):
        print(f"    {i}: {large_queue[i]}")

    # Add a past event
    past = SimpleEvent("past_event", base_time - 100)
    heapq.heappush(large_queue, past)

    print("\nAfter adding past event to large queue:")
    print(f"  Queue size: {len(large_queue)}")
    print("  Top 3 events:")
    for i in range(min(3, len(large_queue))):
        print(f"    {i}: {large_queue[i]}")

    # Verify past event is at top
    assert large_queue[0] == past, f"Past event should be at top, got {large_queue[0]}"
    print("\n✅ Past event correctly at top even with large queue")

    # Test processing due events
    print("\n" + "="*60)
    print("Testing processing of due events:")

    process_queue = []
    current = time.time()

    # Add mix of past, current, and future
    heapq.heappush(process_queue, SimpleEvent("future_10s", current + 10))
    heapq.heappush(process_queue, SimpleEvent("past_5s", current - 5))
    heapq.heappush(process_queue, SimpleEvent("past_10s", current - 10))
    heapq.heappush(process_queue, SimpleEvent("future_5s", current + 5))
    heapq.heappush(process_queue, SimpleEvent("past_1s", current - 1))

    print("Queue after adding mixed events:")
    for i, event in enumerate(process_queue):
        is_due = event.next_time <= current
        print(f"  {i}: {event.name} - {'DUE' if is_due else 'FUTURE'}")

    # Process all due events
    due_events = []
    while process_queue and process_queue[0].next_time <= current:
        event = heapq.heappop(process_queue)
        due_events.append(event.name)
        print(f"  Processing: {event.name}")

    print(f"\nProcessed {len(due_events)} due events: {due_events}")
    print(f"Remaining {len(process_queue)} future events")

    # Verify all past events were processed
    assert "past_10s" in due_events
    assert "past_5s" in due_events
    assert "past_1s" in due_events
    assert "future_10s" not in due_events
    assert "future_5s" not in due_events

    print("✅ All past events processed correctly")


if __name__ == "__main__":
    test_heap_ordering()
