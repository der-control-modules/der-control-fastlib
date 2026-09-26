"""The scheduler loop must yield once per pass (issue #30).

`_scheduler_loop` only yielded when `sleep_time > 0`. A due event whose
reschedule immediately became due again (a sub-millisecond interval) kept
`sleep_time` at 0 forever, so the pop loop never returned control to the
gevent hub: every other greenlet, the scheduler's own stop signal included,
starved. The fix runs the busy case in a subprocess with an external
timeout, because a true zero-yield loop cannot be interrupted from inside
the same process.
"""

import subprocess
import sys

import pytest

_CHILD_SCRIPT = """
import time

import gevent

from derhost.client.agent import Scheduler


class _FakeAgent:
    identity = "test.scheduler.yield-per-pass"


ticks = []
calls = []


def _tick():
    deadline = time.time() + 0.2
    while time.time() < deadline:
        ticks.append(time.time())
        gevent.sleep(0.01)


scheduler = Scheduler(_FakeAgent())
scheduler.schedule(lambda: calls.append(time.time()), 1e-6, name="fast_interval")
ticker = gevent.spawn(_tick)
ticker.join(timeout=1)
scheduler.stop()

print(f"TICKS={len(ticks)} CALLS={len(calls)}")
"""


def test_scheduler_yields_once_per_pass():
    """A sub-millisecond interval must not starve the gevent hub."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"scheduler loop spun without yielding: {exc}")

    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]
    ticks_str, calls_str = line.split()
    ticks = int(ticks_str.split("=")[1])
    calls = int(calls_str.split("=")[1])
    assert ticks > 0, "ticker greenlet never ran: the scheduler loop starved the hub"
    assert calls > 0, "fast-interval callback never ran: the scheduler loop starved the hub"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
