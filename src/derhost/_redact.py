# _redact.py

"""Shared credential redaction for log records.

The client, server, and compat packages each log RPC and pub/sub payloads
that may carry authentication data; this is the one place that redaction
logic is defined, so client and server do not drift into separately
maintained (and separately incomplete) copies.
"""

from typing import Any

REDACTED = "[REDACTED]"

# Field names redacted by exact match, case-insensitively.
SECRET_LOG_FIELDS = {"authentication", "authorization", "token", "key", "password"}

# Substrings redacted after normalizing a field name (lowercase, "-"/"_"
# removed), so "API-Key", "api_key", and "apikey" all match one entry, and
# "Authorization"/"authentication" both match "auth" without listing every
# spelling. Deliberately excludes a bare "key" substring: that would also
# redact non-secret identifiers such as "primary_key" or "foreign_key".
_SECRET_LOG_FIELD_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "auth",
    "cookie",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
)

def _normalize_field_name(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "")


def is_secret_field(key: Any) -> bool:
    """True when key is a field name whose value must be redacted before logging.

    A non-string key (an int result key from a downstream RPC call) is never
    a secret field; returning False here, rather than raising, is what lets
    redact_secrets recurse into a dict with a non-string key.
    """
    if not isinstance(key, str):
        return False
    if key.lower() in SECRET_LOG_FIELDS:
        return True
    normalized = _normalize_field_name(key)
    return any(word in normalized for word in _SECRET_LOG_FIELD_SUBSTRINGS)


# value/return are Any: this redacts an arbitrary log payload (dict, list,
# tuple, or scalar) with no fixed schema, recursing into nested containers.
def redact_secrets(value: Any) -> Any:
    """Replace secret-bearing dict values with a fixed marker before logging.

    Applied only at log call sites; stored and forwarded messages keep their
    real values, since this must not change wire or bus behavior.
    """
    if isinstance(value, dict):
        return {k: (REDACTED if is_secret_field(k) else redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(v) for v in value)
    return value


def _find_json_value_end(text: str, start: int) -> int:
    """Return the index just past the JSON value that begins at start.

    Scans forward from the value's own start rather than parsing the whole
    message, so a hot-path caller pays only for the value it redacts. Handles
    a string, an object or array (by bracket depth, skipping quoted content),
    or a bare number/true/false/null (by scanning to the next `,`, `}`, `]`).
    """
    i, n = start, len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return i
    ch = text[i]
    if ch == '"':
        i += 1
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                return i + 1
            i += 1
        return n
    if ch in "{[":
        depth = 1
        i += 1
        while i < n and depth:
            c = text[i]
            if c == '"':
                i += 1
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == '"':
                        i += 1
                        break
                    i += 1
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
            i += 1
        return i
    while i < n and text[i] not in ",}]":
        i += 1
    return i


def redact_secrets_in_text(text: str) -> str:
    """Redact secret-bearing JSON field values without parsing the text.

    Used on hot paths where a log call must not add a parse-and-reserialize
    of the message; nothing stored or forwarded uses this value. A secret
    field's value is redacted whatever its JSON type (string, object, array,
    number, boolean, or null): the object/array case is why this scans
    forward for a matching bracket instead of matching the value with a
    single regex.

    A single forward pass over text: every character is either advanced
    over by the outer loop or consumed once by the inner "find the closing
    quote" scan below, so the whole call is O(len(text)). An earlier version
    used a regex that restarted its own forward scan at every candidate
    quote, which made text dense with escaped quotes (`\\"`) quadratic.
    """
    out = []
    n = len(text)
    pos = 0
    i = 0
    while i < n:
        if text[i] != '"':
            i += 1
            continue
        j = i + 1
        while j < n:
            if text[j] == "\\" and j + 1 < n:
                j += 2
                continue
            if text[j] == '"':
                break
            j += 1
        else:
            j = n
        if j >= n:
            break  # unterminated quoted string: no further field name to find
        name = text[i + 1 : j]
        k = j + 1
        while k < n and text[k] in " \t\r\n":
            k += 1
        if k < n and text[k] == ":":
            k += 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if is_secret_field(name):
                value_end = _find_json_value_end(text, k)
                out.append(text[pos:k])
                out.append(f'"{REDACTED}"')
                pos = value_end
                i = value_end
                continue
        i = j + 1
    out.append(text[pos:])
    return "".join(out)


def _iter_secret_leaf_values(value: Any):
    """Yield each value stored under a secret-named key, recursing through
    nested dicts, lists, and tuples."""
    if isinstance(value, dict):
        for k, v in value.items():
            if is_secret_field(k):
                yield v
            else:
                yield from _iter_secret_leaf_values(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _iter_secret_leaf_values(v)


def redact_known_secret_values(text: str, *payloads: Any) -> str:
    """Strip any literal occurrence of a secret value carried by payloads.

    For exception text this code did not build from a fixed template (a
    validator's own repr of its rejected input), a key-based scan over the
    text cannot see the value's original shape, since the text has no field
    names to key on. This instead looks up the actual secret values from the
    request payload (args, kwargs) and blanks any occurrence of them, so the
    check is on the value itself rather than on the shape of the text.
    """
    for payload in payloads:
        for secret in _iter_secret_leaf_values(payload):
            secret_text = str(secret)
            if secret_text:
                text = text.replace(secret_text, REDACTED)
    return text


# value is Any: truncates the same arbitrary log payload redact_secrets
# accepts, with no fixed schema.
def truncate_for_log(value: Any, max_length: int = 200) -> str:
    """Redact secrets, then truncate a value's string form for debug logging.

    Redaction runs before truncation: truncating first can cut a marker in
    half and let an unredacted prefix (e.g. `"authentication": "s3c`) through.
    """
    text = str(redact_secrets(value)) if isinstance(value, dict | list) else redact_secrets_in_text(str(value))
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
