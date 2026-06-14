"""Shared time utilities.

SQLite stores naive datetimes; we standardize on naive UTC everywhere
to avoid aware/naive comparison errors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches what SQLite returns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def apply_expiry_delta(current: datetime, raw: str) -> datetime:
    """Resolve a subscription expiry change from admin input.

    Accepts a relative day delta ("+7" / "-3") applied to ``current``, or an
    absolute date ("YYYY-MM-DD"). Raises ValueError on malformed input.
    """
    raw = raw.strip()
    if raw.startswith(("+", "-")):
        return current + timedelta(days=int(raw))
    return datetime.strptime(raw, "%Y-%m-%d")
