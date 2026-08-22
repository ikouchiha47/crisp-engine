"""Canonical timestamp helpers.

All timestamps stored in the memory system are UTC ISO-8601 with explicit
+00:00 offset: "2026-08-22T14:30:00.123456+00:00"

Rules:
  - NEVER use datetime.now() without tz=UTC_TZ  — produces local naive time
  - NEVER use datetime.utcnow()                 — produces UTC but marks it naive
  - NEVER store a naive datetime                — unresolvable if timezone changes
  - ALWAYS parse with parse_ts()                — normalises Z / +00:00 / naive UTC

Timezone changes on the host machine cannot corrupt stored timestamps because
the offset is part of the stored string. Comparisons are always done after
converting to aware UTC via parse_ts(), so they remain correct regardless of
what the OS clock says the local timezone is.
"""

from __future__ import annotations

from datetime import datetime, timezone

UTC_TZ = timezone.utc


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC_TZ)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with explicit +00:00 offset."""
    return now_utc().isoformat()


def parse_ts(ts: str | datetime | None) -> datetime:
    """Parse any timestamp string the store may have written into an aware UTC datetime.

    Handles:
      "2026-08-22T14:30:00+00:00"   — canonical form
      "2026-08-22T14:30:00Z"        — common alternate (Z = UTC)
      "2026-08-22T14:30:00"         — naive, assumed UTC (legacy utcnow() output)
      datetime (aware or naive)     — passed through / localised to UTC
    """
    if ts is None:
        return now_utc()

    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC_TZ)
        return ts.astimezone(UTC_TZ)

    # Normalise Z suffix before fromisoformat (Python < 3.11 doesn't accept Z)
    normalised = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        # Unparseable — return now so callers degrade gracefully
        return now_utc()

    if dt.tzinfo is None:
        # Naive string assumed to be UTC (legacy utcnow() output)
        dt = dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(UTC_TZ)


def age_days(ts: str | datetime | None) -> float:
    """Days elapsed since ts (always positive, computed in UTC)."""
    return max(0.0, (now_utc() - parse_ts(ts)).total_seconds() / 86400)
