"""Plain-language display text for System Logs (Zenn's rule, 2026-10-23).

The System Logs screen is read by business users, so the message it
shows must say what happened in plain business words — never a raw
error code, an exception class name, or a stack trace. The raw
stored message is NOT rewritten in the database (support may still
need it); translation happens here, at read time, which also makes
every older row plain the moment this ships.

Rules, in order:
1. Known event types get a fixed plain sentence.
2. A stored message may pass through only if it passes a strict
   "looks non-technical" screen (no exception names, no traceback /
   SQL / driver markers, no file paths, reasonable length).
3. Everything else falls back to a plain sentence by level.

The list API additionally strips known-technical keys
(``exception_type``, ``traceback``, ...) from ``details``; the
detail API keeps the full record plus the raw message under
``technical_message`` for support, behind an explicit field name so
the UI can keep it out of the default view.
"""

from __future__ import annotations

import re

from app.models.system import LogLevel

_EVENT_MESSAGES: dict[str, str] = {
    "HTTP_ERROR": (
        "A page or action failed to load because of a server problem. "
        "The technical details are saved for the support team."
    ),
    "HTTP_EXCEPTION": (
        "Something went wrong inside the system while handling a request. "
        "The technical details are saved for the support team."
    ),
    "APPLICATION_STARTUP": "The system started.",
    "APPLICATION_SHUTDOWN": "The system stopped.",
}

_LEVEL_FALLBACKS: dict[LogLevel, str] = {
    LogLevel.ERROR: (
        "A system task failed. The technical details are saved for "
        "the support team."
    ),
    LogLevel.WARNING: "Something in the system needs attention.",
    LogLevel.INFO: "A system event was recorded.",
    LogLevel.DEBUG: "A system event was recorded.",
}

_TECHNICAL_PATTERNS = [
    re.compile(r"\b\w+(?:Error|Exception|Violation|Timeout|Fault)\b"),
    re.compile(r"Traceback|stack trace|File \"", re.IGNORECASE),
    re.compile(r"\.py\b|line \d+"),
    re.compile(
        r"psycopg|asyncpg|sqlalchemy|duplicate key|violates .* constraint|"
        r"null value in column|relation .* does not exist",
        re.IGNORECASE,
    ),
    re.compile(r"\b(SELECT|INSERT INTO|UPDATE|DELETE FROM)\b [A-Za-z_]+"),
    re.compile(r"\[Errno|ECONN|HTTP/\d|0x[0-9a-fA-F]+"),
]

# Detail keys that carry raw technical material; hidden in list views.
TECHNICAL_DETAIL_KEYS = frozenset(
    {"exception_type", "exception", "traceback", "stack", "stack_trace", "sql", "query"}
)

_MAX_PLAIN_LENGTH = 200


def _looks_non_technical(message: str) -> bool:
    if not message or len(message) > _MAX_PLAIN_LENGTH:
        return False
    return not any(pattern.search(message) for pattern in _TECHNICAL_PATTERNS)


def plain_message_for(event_type: str, level: LogLevel, raw_message: str) -> str:
    """The only message text the System Logs UI should show by default."""
    if (event_type or "").upper() == "HEALTH_CHECK":
        # Health-check rows are written in plain language already
        # ("System health: <component> <plain reason>") — the read
        # path passes them through unchanged. Anything else is
        # treated as technical text and replaced by the generic
        # level fallback, so SQL, paths, or exception text can
        # never leak into the System Logs UI.
        candidate = (raw_message or "").strip()
        if candidate.startswith("System health:"):
            return candidate
        return _LEVEL_FALLBACKS.get(level, _LEVEL_FALLBACKS[LogLevel.INFO])
    known = _EVENT_MESSAGES.get((event_type or "").upper())
    if known:
        return known
    candidate = (raw_message or "").strip()
    if candidate and _looks_non_technical(candidate):
        return candidate
    return _LEVEL_FALLBACKS.get(level, _LEVEL_FALLBACKS[LogLevel.INFO])


def public_details(details: dict | None) -> dict | None:
    """Details for list views: same data minus raw-technical keys."""
    if not details:
        return details
    return {
        key: value
        for key, value in details.items()
        if key.lower() not in TECHNICAL_DETAIL_KEYS
    }
