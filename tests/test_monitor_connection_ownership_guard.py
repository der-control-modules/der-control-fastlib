"""
Test issue #80 (monitor path): a stale monitor socket's cleanup must not evict
the socket registered for the same id.

`connect_monitor` refuses a duplicate `monitor_id` while the holder is live, so
this state arises only after the holder disconnected and its id was reused: the
old handler's late `disconnect_monitor` call must find an entry that is not its
own. The test sets that state directly on the map, since no live path can hold
two sockets at once.
"""

from derhost.server.connection_manager import ConnectionManager


class TestMonitorDisconnectGuardRejectsDisplacedSocket:
    """Issue #80 class: reject a displaced monitor socket's own cleanup."""

    def test_displaced_monitor_cleanup_leaves_the_replacement_registered(self):
        manager = ConnectionManager()
        monitor_id = "ownership-guard-monitor"
        displaced_socket = object()
        replacement_socket = object()

        # The id was reused after the first socket disconnected.
        manager.monitor_connections[monitor_id] = displaced_socket
        manager.monitor_connections[monitor_id] = replacement_socket

        # The stale socket's own cleanup must not remove the entry that
        # replaced it.
        manager.disconnect_monitor(monitor_id, displaced_socket)

        assert manager.monitor_connections[monitor_id] is replacement_socket
