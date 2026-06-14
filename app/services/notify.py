"""Payment fulfillment notification: create invite link and notify user."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest

from app.bot.dispatcher import bot
from app.database import async_session_factory
from app.models.models import Order, Plan

logger = logging.getLogger(__name__)


async def notify_fulfillment(order_id: str) -> str | None:
    """After order fulfilled: create one-time invite link, send to user.

    Returns the invite link string, or None on failure.
    Safe to call from any context (bot handler, webhook).
    """
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            logger.error("notify_fulfillment: order %s not found", order_id)
            return None
        plan = await session.get(Plan, order.plan_id)
        if plan is None:
            logger.error("notify_fulfillment: plan %d not found", order.plan_id)
            return None

        chat_id = plan.chat_id
        user_id = order.user_id
        plan_name = plan.name

    try:
        # Join-request mode: clicking the link only files a request; the bot
        # approves it only if the requester has an active subscription.
        # A forwarded/leaked link is useless to anyone without a subscription.
        # (Telegram API: creates_join_request cannot combine with member_limit.)
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
            name=f"sub_{order_id[:12]}",
        )
        link = invite.invite_link
    except TelegramBadRequest as e:
        logger.error("Failed to create invite link for chat %d: %s", chat_id, e)
        await bot.send_message(
            user_id,
            f"Payment confirmed for <b>{plan_name}</b>, but invite link creation failed. "
            "Please contact admin.",
        )
        return None
    except Exception as e:
        logger.error("Unexpected error creating invite link: %s", e)
        await bot.send_message(
            user_id,
            "Payment confirmed but invite link failed. Contact admin.",
        )
        return None

    try:
        await bot.send_message(
            user_id,
            f"Payment successful! Tap to join <b>{plan_name}</b>:\n{link}\n\n"
            "Your join request will be approved automatically.",
        )
    except Exception as e:
        logger.error("Failed to notify user %d: %s", user_id, e)

    return link
