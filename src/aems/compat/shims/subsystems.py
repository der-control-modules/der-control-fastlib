"""
VOLTTRON subsystems compatibility shim.

Re-exports individual subsystems for imports like:
    from volttron.platform.vip.agent.subsystems.query import Query
"""

from aems.compat.shims.heartbeat import Heartbeat
from aems.compat.shims.query import Query

__all__ = ["Query", "Heartbeat"]
