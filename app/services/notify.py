"""Payment fulfillment notification: create invite link and notify user."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest

from app.bot.dispatcher import bot
from app.database import async_session_factory
from app.models.models import Order, Plan

logger = logging.getLogger(__name__)


@dataclass
class FulfillmentResult:
    link: str | None
    dm_sent: bool = False


async def notify_fulfillment(order_id: str) -> FulfillmentResult:
    """After order fulfilled: create one-time invite link and notify user.

    Returns link and whether the user received a Telegram DM.
    Safe to call from any context (bot handler, webhook).
    """
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            logger.error("notify_fulfillment: order %s not found", order_id)
            return FulfillmentResult(link=None)
        plan = await session.get(Plan, order.plan_id)
        if plan is None:
            logger.error("notify_fulfillment: plan %d not found", order.plan_id)
            return FulfillmentResult(link=None)

        chat_id = plan.chat_id
        user_id = order.user_id
        plan_name = plan.name
        payment_message_id = order.payment_message_id

    if payment_message_id:
        try:
            await bot.delete_message(chat_id=user_id, message_id=payment_message_id)
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning("Failed to delete payment message for order %s: %s", order_id, e)

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
            name=f"sub_{order_id[:12]}",
        )
        link = invite.invite_link
    except TelegramBadRequest as e:
        logger.error("Failed to create invite link for chat %d: %s", chat_id, e)
        try:
            await bot.send_message(
                user_id,
                f"Payment confirmed for <b>{plan_name}</b>, but invite link creation failed. "
                "Please contact admin.",
            )
        except Exception:
            pass
        return FulfillmentResult(link=None)
    except Exception as e:
        logger.error("Unexpected error creating invite link: %s", e)
        try:
            await bot.send_message(
                user_id,
                "Payment confirmed but invite link failed. Contact admin.",
            )
        except Exception:
            pass
        return FulfillmentResult(link=None)

    dm_sent = False
    try:
        await bot.send_message(
            user_id,
            f"Payment successful! Tap to join <b>{plan_name}</b>:\n{link}\n\n"
            "Your join request will be approved automatically.",
        )
        dm_sent = True
    except Exception as e:
        logger.error("Failed to notify user %d: %s", user_id, e)

    return FulfillmentResult(link=link, dm_sent=dm_sent)
