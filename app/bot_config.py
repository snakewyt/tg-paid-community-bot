"""Runtime overlay for bot configuration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

BOT_CONFIG_PATH = Path("data/bot_config.json")

BOT_CONFIG_KEYS = {
    "bot_token",
    "admin_usernames",
    "welcome_message",
    "vip_group_url",
    "vip_channel_url",
    "order_timeout_minutes",
    "expiry_reminder_days",
    "expiry_reminder_message",
}

_SECRET_KEYS = frozenset({"bot_token"})

_TYPE_MAP: dict = {
    "order_timeout_minutes": int,
    "expiry_reminder_days": int,
}


def init_bot_config() -> None:
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
            except Exception as e:
                logger.warning("bot_config skip key %s: %s", k, e)
    except Exception as e:
        logger.warning("Failed to load bot_config: %s", e)


def get_bot_config() -> dict:
    return {k: getattr(settings, k, "") for k in sorted(BOT_CONFIG_KEYS)}


def save_bot_config(data: dict) -> None:
    existing = {}
    if BOT_CONFIG_PATH.exists():
        try:
            existing = json.loads(BOT_CONFIG_PATH.read_text())
        except Exception:
            pass

    clean: dict = {}
    for k in BOT_CONFIG_KEYS:
        if k not in data:
            if k in existing:
                clean[k] = existing[k]
            continue
        v = data[k]
        if k in _SECRET_KEYS and not str(v).strip():
            if k in existing:
                clean[k] = existing[k]
            continue
        try:
            cast = _TYPE_MAP.get(k, str)
            clean[k] = cast(v)
        except Exception:
            clean[k] = v

    for k, v in clean.items():
        if hasattr(settings, k):
            setattr(settings, k, v)

    BOT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2))
    try:
        BOT_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
