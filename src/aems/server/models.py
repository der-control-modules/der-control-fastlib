# models.py

from abc import ABC, abstractmethod
from dataclasses import dataclass


class JSONSerializable:
    """Base class for JSON serializable objects."""

    def __json__(self):
        return self.__dict__


@dataclass(frozen=True, kw_only=True)
class Credentials(JSONSerializable):
    """Credentials for authentication."""

    identity: str

    @staticmethod
    def create(*, identity: str) -> "Credentials":
        return Credentials(identity=identity)


class Message:
    """Message object for VIP communication."""

    def __init__(self, **kwargs):
        self.__dict__ = kwargs

    def __repr__(self):
        attrs = ", ".join(
            f"{name!r}: {list(value) if isinstance(value, list | tuple) else value!r}"
            for name, value in self.__dict__.items()
        )
        return f"{self.__class__.__name__}(**{{{attrs}}})"

    @staticmethod
    def create_message(*, peer: str, user: str, subsystem: str, msg_id: str, args: list = None) -> "Message":
        if args is None:
            args = []
        return Message(peer=peer, subsystem=subsystem, msg_id=msg_id, user=user, args=args)


class MessageBusStopHandler(ABC):
    """Handler for message bus shutdown events."""

    @abstractmethod
    def message_bus_shutdown(self):
        """Handle message bus shutdown."""
        pass


class MessageBus(ABC):
    """Abstract base class for message bus implementations."""

    # This should be set so it is called for the main
    # program clean up when either the `stop` method is
    # called.
    _stop_handler: MessageBusStopHandler

    @abstractmethod
    def start(self):
        """Start the message bus."""
        pass

    @abstractmethod
    def stop(self):
        """Stop the message bus."""
        pass

    def set_stop_handler(self, value: MessageBusStopHandler):
        """Set the stop handler for the message bus."""
        self._stop_handler = value

    def get_stop_handler(self) -> MessageBusStopHandler | None:
        """Get the stop handler for the message bus."""
        return self._stop_handler

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the message bus is running."""
        pass

    @abstractmethod
    def send_vip_message(self, message: Message):
        """Send a VIP message."""
        pass

    @abstractmethod
    def receive_vip_message(self) -> Message:
        """Receive a VIP message."""
        pass
