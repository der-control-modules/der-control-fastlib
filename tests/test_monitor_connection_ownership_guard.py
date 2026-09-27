"""
Test issue #80 (monitor path): a displaced monitor socket must not evict its
replacement.

`connect_monitor` has no early refusal for a duplicate `monitor_id`
(connection_manager.py:363-367): it overwrites the entry unconditionally, so
`disconnect_monitor`'s ownership check at connection_manager.py:377 is the
map's only guard against the displaced socket's own cleanup deleting the
entry that replaced it.
"""

from derhost.server.connection_manager import ConnectionManager


class TestMonitorDisconnectGuardRejectsDisplacedSocket:
    """Issue #80 class: reject a displaced monitor socket's own cleanup."""

    def test_displaced_monitor_cleanup_leaves_the_replacement_registered(self):
        manager = ConnectionManager()
        monitor_id = "ownership-guard-monitor"
        displaced_socket = object()
        replacement_socket = object()

        # A duplicate monitor_id replaces the first entry outright.
        manager.monitor_connections[monitor_id] = displaced_socket
        manager.monitor_connections[monitor_id] = replacement_socket

        # The displaced socket's own cleanup must not remove the entry that
        # replaced it.
        manager.disconnect_monitor(monitor_id, displaced_socket)

        assert manager.monitor_connections[monitor_id] is replacement_socket
