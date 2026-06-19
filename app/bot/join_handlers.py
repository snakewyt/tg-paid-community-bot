"""Join-request gatekeeper: approve only users with an active subscription."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest, User as TelegramUser

from app.database import async_session_factory
from app.models.models import Plan, Subscription
from app.services.admin_notify import notify_admins
from app.services.membership import get_active_subscription
from app.services.user_resolve import format_user_ref

logger = logging.getLogger(__name__)
join_router = Router()


def _join_admin_message(
    user: TelegramUser,
    chat_title: str | None,
    chat_id: int,
    sub: Subscription,
    plan_name: str | None,
) -> str:
    name = (user.first_name or "").strip()
    label = format_user_ref(user.id, user.username)
    if name:
        label = f"{label} ({name})"
    group = chat_title or str(chat_id)
    plan_line = f"\n套餐：<b>{plan_name}</b>" if plan_name else ""
    return (
        "✅ <b>新成员入群</b>\n"
        f"用户：{label}\n"
        f"群组：{group} (<code>{chat_id}</code>){plan_line}\n"
        f"到期：<b>{sub.expires_at.strftime('%Y-%m-%d %H:%M UTC')}</b>"
    )


@join_router.chat_join_request()
async def on_join_request(event: ChatJoinRequest):
    user_id = event.from_user.id
    chat_id = event.chat.id

    async with async_session_factory() as session:
        sub = await get_active_subscription(session, user_id, chat_id)
        plan_name = None
        if sub:
            plan = await session.get(Plan, sub.plan_id)
            plan_name = plan.name if plan else None

    if sub:
        try:
            await event.approve()
            logger.info("Approved join request: user %d -> chat %d", user_id, chat_id)
        except Exception as e:
            logger.error("Failed to approve join for user %d: %s", user_id, e)
            return

        try:
            await notify_admins(
                _join_admin_message(
                    event.from_user,
                    event.chat.title,
                    chat_id,
                    sub,
                    plan_name,
                )
            )
        except Exception as e:
            logger.warning("Failed to notify admins of join user=%d: %s", user_id, e)
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
            pass
