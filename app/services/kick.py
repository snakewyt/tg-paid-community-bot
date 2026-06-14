"""Kick expired users from groups."""

from __future__ import annotations

import logging

from app.bot.dispatcher import bot

logger = logging.getLogger(__name__)


async def kick_user_from_chat(chat_id: int, user_id: int) -> bool:
    """Ban then unban so the user is removed but can rejoin after repaying."""
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        return True
    except Exception as e:
        logger.warning("Kick failed user=%d chat=%d: %s", user_id, chat_id, e)
        return False
