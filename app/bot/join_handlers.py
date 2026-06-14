"""Join-request gatekeeper: approve only users with an active subscription."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest

from app.database import async_session_factory
from app.services.membership import has_active_subscription

logger = logging.getLogger(__name__)
join_router = Router()


@join_router.chat_join_request()
async def on_join_request(event: ChatJoinRequest):
    user_id = event.from_user.id
    chat_id = event.chat.id

    async with async_session_factory() as session:
        allowed = await has_active_subscription(session, user_id, chat_id)

    if allowed:
        try:
            await event.approve()
            logger.info("Approved join request: user %d -> chat %d", user_id, chat_id)
        except Exception as e:
            logger.error("Failed to approve join for user %d: %s", user_id, e)
    else:
        try:
            await event.decline()
            logger.info("Declined join request: user %d -> chat %d (no subscription)", user_id, chat_id)
        except Exception as e:
            logger.error("Failed to decline join for user %d: %s", user_id, e)
        try:
            await event.bot.send_message(
                user_id,
                "Your join request was declined: no active subscription found. "
                "Use /start to subscribe.",
            )
        except Exception:
            # User may have never started the bot; can't message them
            pass
