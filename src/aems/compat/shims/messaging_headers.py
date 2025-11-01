"""
VOLTTRON messaging headers compatibility shim.

Provides standard message header constants used throughout VOLTTRON.
"""

# Standard header constants
DATE = "Date"
TIMESTAMP = "TimeStamp"
SYNC_TIMESTAMP = "SynchronizedTimeStamp"
CONTENT_TYPE = "Content-Type"
FROM = "From"
TO = "To"
REQUESTER_ID = "requesterID"
COOKIE = "Cookie"

# Export all constants
__all__ = [
    "DATE",
    "TIMESTAMP",
    "SYNC_TIMESTAMP",
    "CONTENT_TYPE",
    "FROM",
    "TO",
    "REQUESTER_ID",
    "COOKIE",
]
