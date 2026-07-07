"""Track the bot's own membership changes across groups/channels.

Telegram sends a `my_chat_member` update whenever the bot is added to,
removed from, or has its rights changed in a chat. We persist these so the
admin panel can suggest chats the bot has joined when creating a plan.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from app.services.chats import record_bot_chat

logger = logging.getLogger(__name__)
chat_member_router = Router()


@chat_member_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    chat = event.chat
    # Only track groups/channels, not private DMs with users.
    if chat.type not in ("group", "supergroup", "channel"):
        return

    status = event.new_chat_member.status
    status = getattr(status, "value", status)  # ChatMemberStatus enum -> str

    try:
        await record_bot_chat(
            chat_id=chat.id,
            title=chat.title,
            chat_type=chat.type,
            username=chat.username,
            status=str(status),
        )
    except Exception as e:
        logger.warning("Failed to record bot chat %s: %s", chat.id, e)
