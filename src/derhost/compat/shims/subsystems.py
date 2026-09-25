"""
VOLTTRON subsystems compatibility shim.

Re-exports individual subsystems for imports like:
    from volttron.platform.vip.agent.subsystems.query import Query
    from volttron.platform.vip.agent.subsystems import RPC
"""

from derhost.compat.shims.heartbeat import Heartbeat
from derhost.compat.shims.query import Query
from derhost.compat.shims.vip_agent import RPC

__all__ = ["Query", "Heartbeat", "RPC"]
