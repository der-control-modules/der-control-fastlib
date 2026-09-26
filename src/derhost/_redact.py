# _redact.py

"""Shared credential redaction for log records.

The client, server, and compat packages each log RPC and pub/sub payloads
that may carry authentication data; this is the one place that redaction
logic is defined, so client and server do not drift into separately
maintained (and separately incomplete) copies.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"
SECRET_LOG_FIELDS = {"authentication", "authorization", "token", "key", "password"}
_SECRET_LOG_FIELD_RE = re.compile(
    r'"(' + "|".join(re.escape(field) for field in SECRET_LOG_FIELDS) + r')"\s*:\s*"(?:[^"\\]|\\.)*"',
    re.IGNORECASE,
)


# value/return are Any: this redacts an arbitrary log payload (dict, list,
# or scalar) with no fixed schema, recursing into nested dicts and lists.
def redact_secrets(value: Any) -> Any:
    """Replace secret-bearing dict values with a fixed marker before logging.

    Applied only at log call sites; stored and forwarded messages keep their
    real values, since this must not change wire or bus behavior.
    """
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in SECRET_LOG_FIELDS else redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


def redact_secrets_in_text(text: str) -> str:
    """Redact secret-bearing JSON string values without parsing the text.

    Used on hot paths where a log call must not add a parse-and-reserialize
    of the message; nothing stored or forwarded uses this value.
    """
    return _SECRET_LOG_FIELD_RE.sub(lambda m: f'"{m.group(1)}": "{REDACTED}"', text)


def truncate_for_log(value: Any, max_length: int = 200) -> str:
    """Redact secrets, then truncate a value's string form for debug logging.

    Redaction runs before truncation: truncating first can cut a marker in
    half and let an unredacted prefix (e.g. `"authentication": "s3c`) through.
    """
    text = str(redact_secrets(value)) if isinstance(value, dict | list) else redact_secrets_in_text(str(value))
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
