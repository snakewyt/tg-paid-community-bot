"""Runtime overlay for bot configuration.

Loaded at startup; persisted to data/bot_config.json so changes made in the
admin panel survive restarts without editing .env.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

BOT_CONFIG_PATH = Path("data/bot_config.json")

BOT_CONFIG_KEYS = {
    "bot_token",
    "usdt_rate",
    "admin_usernames",
    "welcome_message",
    "order_timeout_minutes",
    "expiry_reminder_days",
    "expiry_reminder_message",
}

_TYPE_MAP: dict = {
    "usdt_rate": float,
    "order_timeout_minutes": int,
    "expiry_reminder_days": int,
}


def init_bot_config() -> None:
    """Overlay runtime settings with persisted bot config (if any)."""
    if not BOT_CONFIG_PATH.exists():
        return
    try:
        data = json.loads(BOT_CONFIG_PATH.read_text())
        for k, v in data.items():
            if k not in BOT_CONFIG_KEYS or not hasattr(settings, k):
                continue
            try:
                cast = _TYPE_MAP.get(k, str)
                setattr(settings, k, cast(v))
            except Exception:
                pass
    except Exception:
        pass


def get_bot_config() -> dict:
    return {k: getattr(settings, k, "") for k in sorted(BOT_CONFIG_KEYS)}


def save_bot_config(data: dict) -> None:
    BOT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean: dict = {}
    for k in BOT_CONFIG_KEYS:
        if k not in data:
            continue
        v = data[k]
        try:
            cast = _TYPE_MAP.get(k, str)
            clean[k] = cast(v)
        except Exception:
            clean[k] = v
    for k, v in clean.items():
        if hasattr(settings, k):
            setattr(settings, k, v)
    BOT_CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2))
