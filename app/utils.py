"""Shared time utilities.

SQLite stores naive datetimes; we standardize on naive UTC everywhere
to avoid aware/naive comparison errors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import User as TelegramUser


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches what SQLite returns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_admin_usernames(raw: str) -> set[str]:
    """Normalize comma-separated Telegram usernames (no @, lowercase)."""
    return {
        part.strip().lstrip("@").lower()
        for part in raw.split(",")
        if part.strip()
    }


def is_telegram_admin(
    user: "TelegramUser | None",
    admin_ids: list[int],
    admin_usernames: str,
) -> bool:
    """True if user matches configured admin IDs or usernames."""
    if user is None:
        return False
    if user.id in admin_ids:
        return True
    if user.username:
        allowed = parse_admin_usernames(admin_usernames)
        if user.username.lower() in allowed:
            return True
    return False


def apply_expiry_delta(current: datetime, raw: str) -> datetime:
    """Resolve a subscription expiry change from admin input.

    Accepts a relative day delta ("+7" / "-3") applied to ``current``, or an
    absolute date ("YYYY-MM-DD"). Raises ValueError on malformed input.
    """
    raw = raw.strip()
    if raw.startswith(("+", "-")):
        return current + timedelta(days=int(raw))
    return datetime.strptime(raw, "%Y-%m-%d")
