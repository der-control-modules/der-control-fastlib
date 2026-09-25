"""
VOLTTRON subsystems compatibility shim.

Re-exports individual subsystems for imports like:
    from volttron.platform.vip.agent.subsystems.query import Query
    from volttron.platform.vip.agent.subsystems import RPC
"""

from aems.compat.shims.heartbeat import Heartbeat
from aems.compat.shims.query import Query
from aems.compat.shims.vip_agent import RPC

__all__ = ["Query", "Heartbeat", "RPC"]
