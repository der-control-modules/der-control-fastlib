"""
VOLTTRON JSON API compatibility shim.

Provides JSON serialization functions.
"""

import json


def dumps(data, **kwargs):
    """
    Serialize data to JSON string.

    Args:
        data: Data to serialize
        **kwargs: Additional arguments passed to json.dumps

    Returns:
        JSON string
    """
    return json.dumps(data, **kwargs)


def dumpb(data, **kwargs):
    """
    Serialize data to JSON bytes.

    Args:
        data: Data to serialize
        **kwargs: Additional arguments passed to json.dumps

    Returns:
        JSON bytes
    """
    return json.dumps(data, **kwargs).encode("utf-8")


def loads(data, **kwargs):
    """
    Deserialize JSON string to Python object.

    Args:
        data: JSON string or bytes to deserialize
        **kwargs: Additional arguments passed to json.loads

    Returns:
        Python object
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, **kwargs)


def loadb(data, **kwargs):
    """
    Deserialize JSON bytes to Python object.

    Args:
        data: JSON bytes to deserialize
        **kwargs: Additional arguments passed to json.loads

    Returns:
        Python object
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, **kwargs)


__all__ = ["dumps", "dumpb", "loads", "loadb"]
