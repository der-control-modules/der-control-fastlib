# -*- coding: utf-8 -*-
"""
JSON-RPC 2.0 error handling compatible with volttron-core.
"""

# JSON-RPC error codes from volttron-core
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Implementation-defined server errors from volttron-core
UNHANDLED_EXCEPTION = -32000
UNAUTHORIZED = -32001
UNABLE_TO_REGISTER_INSTANCE = -32002
DISCOVERY_ERROR = -32003
UNABLE_TO_UNREGISTER_INSTANCE = -32004
UNAVAILABLE_PLATFORM = -32005
UNAVAILABLE_AGENT = -32006


class Error(Exception):
    """Raised when a recoverable JSON-RPC protocol error occurs."""

    def __init__(self, code, message, data=None):
        args = (code, message, data) if data is not None else (code, message)
        super(Error, self).__init__(*args)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self):
        try:
            return str(self.data["detail"])
        except (AttributeError, KeyError, TypeError):
            return str(self.message)


class MethodNotFound(Error):
    """Raised when remote method is not implemented."""
    pass


class RemoteError(Exception):
    """Report the details of an error which occurred remotely.

    Instances of this exception are usually created by
    exception_from_json(), which uses the 'detail' element of the
    JSON-RPC error for message, if it is set, otherwise the JSON-RPC
    error message.  The exc_info argument is set from the 'exception.py'
    element associated with an error code of -32000
    (UNHANDLED_EXCEPTION). Typical keys in exc_info are exc_type,
    exc_args, and exc_tb (if tracebacks are allowed) which are
    stringified versions of the tuple returned from sys.exc_info().
    """

    def __init__(self, message, **exc_info):
        if exc_info:
            try:
                exc_type = exc_info["exc_type"]
                exc_args = exc_info["exc_args"]
            except KeyError:
                msg = message
            else:
                args = ", ".join(repr(arg) for arg in exc_args)
                msg = "%s(%s)" % (exc_type, args)
        else:
            msg = message
        super(RemoteError, self).__init__(msg)
        self.message = message
        self.exc_info = exc_info

    def __repr__(self):
        exc_type = self.exc_info.get("exc_type", "<unknown>")
        try:
            exc_args = ", ".join(repr(arg) for arg in self.exc_info["exc_args"])
        except KeyError:
            exc_args = "..."
        return "%s(%s)" % (exc_type, exc_args)


def exception_from_json(code, message, data=None):
    """Return an exception suitable for raising in a caller.

    This follows the volttron-core pattern for converting JSON-RPC errors
    into appropriate Python exceptions.
    """
    if code == UNHANDLED_EXCEPTION:
        return RemoteError(data.get("detail", message), **data.get("exception.py", {}))
    elif code == METHOD_NOT_FOUND:
        return MethodNotFound(code, message, data)
    return Error(code, message, data)


def create_error_from_response(error_data):
    """Create an exception from an RPC error response.

    Args:
        error_data: Can be a string (simple error message) or dict (JSON-RPC error)

    Returns:
        Exception: Appropriate exception type for the error
    """
    if isinstance(error_data, str):
        # Simple string error - create generic error
        return Exception(error_data)
    elif isinstance(error_data, dict):
        # JSON-RPC style error with code, message, data
        code = error_data.get("code", INTERNAL_ERROR)
        message = error_data.get("message", "Unknown error")
        data = error_data.get("data")
        return exception_from_json(code, message, data)
    else:
        # Unknown error format
        return Exception(f"Unknown error: {error_data}")
