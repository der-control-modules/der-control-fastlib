# _redact.py

"""Shared credential redaction for log records.

The client, server, and compat packages each log RPC and pub/sub payloads
that may carry authentication data; this is the one place that redaction
logic is defined, so client and server do not drift into separately
maintained (and separately incomplete) copies.

Structured values (dict, list, tuple) are redacted by walking them and
replacing any secret-named key's value outright. Text is redacted by
parsing it as JSON when it looks like JSON, and otherwise (or when parsing
fails) by finding the first "name: value" or "name=value" pair whose name
is a secret field and dropping everything from its separator onward: no
attempt is made to find where that one value ends, since a value can be
free text with its own quotes and colons (see #36's quote-pairing leak,
where tracking quote pairs to find a value's end got the pairing wrong on a
stray quote earlier in the text). Every public function fails
closed: on any exception, including RecursionError from pathological
nesting, it returns "[REDACTED]" rather than let a partially-built value
or an unredacted string escape.
"""

import json
import re
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

REDACTED = "[REDACTED]"

# Field names redacted on an exact, fully-normalized match.
_SECRET_EXACT_NAMES = {
    "auth",
    "authentication",
    "authorization",
    "authheader",
    "key",
    "pass",
    "pwd",
    "passwd",
    "pin",
    "pincode",
    "otp",
    "salt",
    "psk",
    "session",
    "sessionid",
    "sessionkey",
    "dsn",
    "dburl",
    "databaseurl",
    "connectionstring",
}

# Field names redacted when contained anywhere in the normalized name.
# Deliberately excludes a bare "auth" or "key" substring: those also match
# non-secret identifiers such as "author" or "primary_key".
_SECRET_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passphrase",
    "cookie",
    "credential",
    "apikey",
    "accesskey",
    "privatekey",
    "encryptionkey",
    "signingkey",
    "hmackey",
    "sshkey",
    "masterkey",
    "jwt",
    "bearer",
    "signature",
    "authentication",
    "authorization",
)

# Field names redacted when the normalized name ENDS WITH one of these,
# unlike _SECRET_SUBSTRINGS which matches anywhere: a suffix match is precise
# enough for these three short forms ("db_pass", "user_passwd") without also
# catching an unrelated word that merely contains them mid-string.
_SECRET_SUFFIXES = ("passwd", "pass", "pwd")

# Names that would otherwise match above but name a diagnostic, not a
# credential; hiding them would hide auth-failure or metadata information
# from an operator reading the log. Checked before the substring match, so
# an exemption wins over a substring hit ("token_count" over "token").
_SECRET_EXEMPT_EXACT = {"authenticated", "authorized"}
_SECRET_EXEMPT_SUFFIXES = (
    "count",
    "type",
    "id",
    "expiresat",
    "expiry",
    "ttl",
    "length",
    "policy",
    "required",
    "enabled",
)
_SECRET_EXEMPT_PREFIXES = ("max", "min", "num")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_SEGMENT_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


@lru_cache(maxsize=4096)
def _normalize_field_name(name: str) -> str:
    return _NON_ALNUM_RE.sub("", name.lower())


@lru_cache(maxsize=4096)
def _first_name_segment(name: str) -> str:
    """The leading run of alphanumeric characters, lowercased.

    The min/max/num prefix exemption checks this instead of the fully
    normalized name: a product name that merely starts with one of those
    letters ("minio") must not be exempted just because stripping its own
    separators makes it look like a numeric-bound prefix. "min_token_length"
    (first segment "min") is exempt; "minio_secret_key" (first segment
    "minio") is not.
    """
    segments = _SEGMENT_SPLIT_RE.split(name)
    return segments[0].lower() if segments else ""


def is_secret_field(key: Any) -> bool:
    """True when key is a field name whose value must be redacted before logging.

    A non-string key (an int result key from a downstream RPC call) is never
    a secret field; returning False here, rather than raising, is what lets
    redact_secrets recurse into a dict with a non-string key.
    """
    if not isinstance(key, str):
        return False
    normalized = _normalize_field_name(key)
    if normalized in _SECRET_EXACT_NAMES:
        return True
    if (
        normalized in _SECRET_EXEMPT_EXACT
        or normalized.endswith(_SECRET_EXEMPT_SUFFIXES)
        or _first_name_segment(key) in _SECRET_EXEMPT_PREFIXES
    ):
        return False
    return normalized.endswith(_SECRET_SUFFIXES) or any(word in normalized for word in _SECRET_SUBSTRINGS)


# A candidate "name" followed by optional closing quote/backslash noise,
# optional spacing, then the assignment itself. Matches a JSON key
# ("token":), a Python-repr key ('token':), or a plain key=value pair,
# without needing to know which quote style opened the name.
_FIELD_ASSIGNMENT_RE = re.compile(r"(?<![A-Za-z0-9_\-])([A-Za-z0-9_\-]+)[\"'\\]*[ \t]*[:=]")


def _redact_conservative(text: str) -> str:
    """Blank from the first secret-named separator to the end of text.

    Deliberately does not try to find where the matched value ends: an
    earlier version of this code tried exactly that (tracking quote pairs)
    and got the pairing wrong on a stray quote earlier in the text. Losing
    whatever diagnostic text follows a secret key is the accepted trade,
    and it only applies to text bound for a log, never to wire text (see
    redact_known_secret_values).
    """
    for match in _FIELD_ASSIGNMENT_RE.finditer(text):
        if is_secret_field(match.group(1)):
            return text[: match.end()] + " " + REDACTED
    return text


def _redact_walk(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if is_secret_field(k) else _redact_walk(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_walk(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_walk(v) for v in value)
    if isinstance(value, str):
        return _redact_conservative(value)
    return value


# value/return are Any: this redacts an arbitrary log payload (dict, list,
# tuple, or scalar) with no fixed schema, recursing into nested containers.
def redact_secrets(value: Any) -> Any:
    """Replace secret-bearing values with a fixed marker before logging.

    Applied only at log call sites; stored and forwarded messages keep their
    real values, since this must not change wire or bus behavior. A string
    leaf (including a bare string value, not only a dict value) is scanned
    for an embedded "name: value" pair, so JSON or repr text carried as a
    string is covered without a separate text path.
    """
    try:
        return _redact_walk(value)
    except Exception:
        return REDACTED


def redact_text(text: str) -> str:
    """Redact secret-bearing values in free-form text.

    Text that looks like a JSON document is parsed, redacted structurally,
    and re-serialized, since that is precise about value boundaries. Any
    other text, or JSON that fails to parse, falls back to the conservative
    name-then-separator rule.
    """
    try:
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except ValueError:
                return _redact_conservative(text)
            return json.dumps(_redact_walk(parsed))
        return _redact_conservative(text)
    except Exception:
        return REDACTED


def _iter_secret_leaf_values(value: Any) -> Iterator[Any]:
    """Yield every leaf value reachable under a secret-named key, recursing
    through nested dicts, lists, and tuples in both directions: down to find
    a secret-named key, and further down again once one is found, so a
    container held under a secret key (a `credentials` dict, a `tokens`
    list) yields its own string, bytes, and int leaves instead of being
    skipped as "not a string". A leaf never reached through a secret-named
    key is not yielded at all."""
    if isinstance(value, dict):
        for k, v in value.items():
            if is_secret_field(k):
                yield from _iter_leaves(v)
            else:
                yield from _iter_secret_leaf_values(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _iter_secret_leaf_values(v)


def _iter_leaves(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_leaves(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _iter_leaves(v)
    else:
        yield value


_MIN_BLANK_LENGTH = 8
_MIN_SHORTENED_LENGTH = 16
_SHORTENED_ANCHOR_LENGTH = 8
# Upper bound on how far apart a display library's own head and tail can
# land. An unbounded ".*" is quadratic on adversarial input and, being
# greedy, spans from the first occurrence of head all the way to the LAST
# occurrence of tail anywhere later in the text, blanking real diagnostic
# text between two unrelated occurrences. 128 is generous for any known
# "first N...last M" shortening (pydantic's is under 60 characters wide)
# while keeping the match close to linear.
_MAX_SHORTENED_SPAN = 128


def _secret_text_forms(secret_text: str) -> set[str]:
    """Every textual form secret_text might appear as once encoded: the
    literal, its JSON string encoding (with and without ensure_ascii), and
    its Python repr, each with the wrapping quote stripped off since the
    surrounding text supplies its own."""
    return {
        secret_text,
        json.dumps(secret_text)[1:-1],
        json.dumps(secret_text, ensure_ascii=False)[1:-1],
        repr(secret_text)[1:-1],
    }


def _blank_shortened_form(text: str, anchor_text: str) -> str:
    """Blank a display library's own "first N...last M" shortening of
    anchor_text (e.g. pydantic's input_value repr), without depending on
    that library's exact truncation length or constant.

    Two bounded passes, not one unbounded one: head-and-tail, replacing
    every occurrence within _MAX_SHORTENED_SPAN characters of each other (a
    secret echoed more than once must be blanked each time, not just the
    first); then head-only, for a shortening that never shows a tail at
    all, bounded to a short run of trailing ellipsis punctuation so a plain
    word that happens to match the head is not blanked on its own.
    """
    if len(anchor_text) < _MIN_SHORTENED_LENGTH:
        return text
    head = re.escape(anchor_text[:_SHORTENED_ANCHOR_LENGTH])
    tail = re.escape(anchor_text[-_SHORTENED_ANCHOR_LENGTH:])
    text = re.sub(head + f".{{0,{_MAX_SHORTENED_SPAN}}}?" + tail, REDACTED, text, flags=re.DOTALL)
    return re.sub(head + r"\.{1,3}(?=[^A-Za-z0-9]|$)", REDACTED, text)


def _blank_all_forms(text: str, forms: set[str], anchor_text: str) -> str:
    """Blank every occurrence of every form in forms, longest first, so a
    longer encoded form is blanked whole rather than leaving quote or
    backslash fragments behind after a shorter form matches part of it.

    The shortened-form match (see _blank_shortened_form) runs once per
    candidate anchor: the raw secret and any of its encoded forms at least
    _MIN_SHORTENED_LENGTH long. A display library may shorten its own
    escaped repr of the secret rather than the raw text, so a secret whose
    first characters need escaping (a quote, a backslash) is only found by
    anchoring on that escaped form too.
    """
    anchors = {form for form in forms if len(form) >= _MIN_SHORTENED_LENGTH}
    if len(anchor_text) >= _MIN_SHORTENED_LENGTH:
        anchors.add(anchor_text)
    for anchor in anchors:
        text = _blank_shortened_form(text, anchor)
    for form in sorted({f for f in forms if f}, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


def redact_known_secret_values(text: str, *payloads: Any) -> str:
    """Strip any occurrence of a secret value carried by payloads from text.

    For exception text this code did not build from a fixed template (a
    validator's own repr of its rejected input), a key-based scan over the
    text cannot see the value's original shape, since the text has no field
    names to key on. This instead looks up the actual secret values from the
    request payload (args, kwargs) and blanks any occurrence of them, so the
    check is on the value itself rather than on the shape of the text.

    Only a str or bytes value of at least 8 characters, or an int (not bool)
    with at least 8 decimal digits, is blanked. A shorter or other-typed
    value (a one-character token, `None`, `True`, a 4-digit PIN) is a common
    word or literal that is likely to appear in the text for reasons
    unrelated to the secret, and that same text is often the RPC error the
    caller receives, so blanking it would corrupt the message instead of
    protecting anything.
    """
    try:
        for payload in payloads:
            for secret in _iter_secret_leaf_values(payload):
                if isinstance(secret, bool):
                    continue
                if isinstance(secret, int):
                    digits = str(secret).lstrip("-")
                    if len(digits) >= _MIN_BLANK_LENGTH:
                        text = text.replace(str(secret), REDACTED)
                    continue
                if isinstance(secret, bytes):
                    if len(secret) < _MIN_BLANK_LENGTH:
                        continue
                    decoded = secret.decode("utf-8", errors="replace")
                    # str(b"...")/repr(b"...") (the b'...' wrapper, e.g. an
                    # f-string embedding the bytes object directly) as well
                    # as its decoded text: both are common ways bytes end up
                    # in text.
                    forms = _secret_text_forms(decoded) | {repr(secret), repr(secret)[2:-1]}
                    text = _blank_all_forms(text, forms, decoded)
                    continue
                if isinstance(secret, str):
                    if len(secret) < _MIN_BLANK_LENGTH:
                        continue
                    text = _blank_all_forms(text, _secret_text_forms(secret), secret)
        return text
    except Exception:
        return REDACTED


# value is Any: truncates the same arbitrary log payload redact_secrets
# accepts, with no fixed schema.
def truncate_for_log(value: Any, max_length: int = 200) -> str:
    """Redact secrets, then truncate a value's string form for debug logging.

    Redaction runs before truncation: truncating first can cut a marker in
    half and let an unredacted prefix (e.g. `"authentication": "s3c`) through.
    """
    text = str(redact_secrets(value)) if isinstance(value, dict | list | tuple) else redact_text(str(value))
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
