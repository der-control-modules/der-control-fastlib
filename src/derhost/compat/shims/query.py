"""
VOLTTRON Query subsystem compatibility shim.

Provides: Query class for querying platform information
"""

import logging

_log = logging.getLogger(__name__)


class Query:
    """
    VOLTTRON Query subsystem compatibility.

    In VOLTTRON, Query is used to query platform information.
    This provides a minimal implementation for compatibility.
    """

    def __init__(self, core):
        """
        Initialize Query subsystem.

        Args:
            core: Agent core subsystem
        """
        self.core = core
        self._agent = getattr(core, "_owner", None)

    def query(self, key):
        """
        Query platform for information.

        Args:
            key: Information key to query (e.g., 'serverkey', 'addresses')

        Returns:
            QueryResult: Result object with .get() method
        """
        _log.debug(f"Query requested: {key}")

        # Return a result object that mimics VOLTTRON behavior
        return QueryResult(key, self._get_value(key))

    def _get_value(self, key):
        """Get the value for a query key."""
        # Common VOLTTRON query keys
        if key == "serverkey":
            # In AEMS, we don't use ZMQ server keys
            return "aems-server-key-not-applicable"

        elif key == "addresses":
            # Return the AEMS server address
            if self._agent and hasattr(self._agent, "address"):
                return [self._agent.address]
            return ["ws://localhost:8000"]

        elif key == "identity":
            if self._agent and hasattr(self._agent, "identity"):
                return self._agent.identity
            return "unknown"

        elif key == "version":
            if self.core and hasattr(self.core, "_version"):
                return self.core._version
            return "1.0.0"

        else:
            _log.warning(f"Unknown query key: {key}")
            return None


class QueryResult:
    """
    Query result wrapper (VOLTTRON compatibility).

    In VOLTTRON, query().get() returns the result.
    """

    def __init__(self, key, value):
        self.key = key
        self.value = value

    def get(self, timeout=None):
        """
        Get the query result.

        Args:
            timeout: Timeout in seconds (ignored in AEMS)

        Returns:
            Query result value
        """
        return self.value


__all__ = ["Query"]
