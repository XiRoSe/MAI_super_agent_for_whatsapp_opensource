"""Short, reader-local time labels for fetched WhatsApp messages.

GOWA hands back a raw per-message ``timestamp`` whose shape varies by build:
a unix epoch in SECONDS or MILLISECONDS, an ISO-8601 string (with or without a
trailing ``Z``), a numeric string, an already-parsed ``datetime``, or ``None``.
``msg_time_label`` parses ALL of these defensively (never raises), converts to the
reader's IANA timezone, and renders a compact label relative to "now" so the agent
can reason precisely about "what was said this morning / yesterday".

Label formats (24-hour clock):
- same day      -> ``"today HH:MM"``
- previous day  -> ``"yesterday HH:MM"``
- older, same year      -> ``"DD/MM HH:MM"``
- older, different year -> ``"DD/MM/YY HH:MM"``
- missing / unparseable -> ``""`` (caller falls back to no time)
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Epoch values at/above this are treated as MILLISECONDS (seconds would put us past
# the year 5138, so any realistic ms timestamp lands here and any realistic seconds
# timestamp stays below it).
_MS_THRESHOLD = 1e11


def _from_epoch(value: float) -> datetime | None:
    """A unix epoch (seconds or milliseconds) -> aware UTC datetime, or None."""
    if abs(value) >= _MS_THRESHOLD:
        value = value / 1000.0
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_ts(ts) -> datetime | None:
    """Best-effort parse of a GOWA timestamp into an aware UTC datetime, or None."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(ts, bool):  # guard: bool is an int subclass
        return None
    if isinstance(ts, (int, float)):
        return _from_epoch(float(ts))
    s = str(ts).strip()
    if not s:
        return None
    try:  # numeric string -> epoch (ISO strings have non-numeric chars and fall through)
        return _from_epoch(float(s))
    except ValueError:
        pass
    try:  # ISO-8601, tolerating a trailing 'Z' and naive (assumed UTC) values
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def msg_time_label(ts, tz_name: str | None, *, now: datetime | None = None) -> str:
    """A short, reader-local label for a message's send-time. Never raises.

    ``ts`` may be epoch seconds/ms, an ISO string, a numeric string, a datetime, or
    None. ``tz_name`` is the reader's IANA zone (e.g. "Asia/Jerusalem"); None/invalid
    falls back to UTC. ``now`` defaults to the current UTC time (override for tests).
    Returns "" when ``ts`` is missing or unparseable.
    """
    dt = _parse_ts(ts)
    if dt is None:
        return ""
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local = dt.astimezone(tz)
    now_local = now.astimezone(tz)
    hm = local.strftime("%H:%M")
    delta_days = (now_local.date() - local.date()).days
    if delta_days == 0:
        return f"today {hm}"
    if delta_days == 1:
        return f"yesterday {hm}"
    if local.year == now_local.year:
        return f"{local.strftime('%d/%m')} {hm}"
    return f"{local.strftime('%d/%m/%y')} {hm}"
