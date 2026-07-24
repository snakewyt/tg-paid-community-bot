"""Join-request gatekeeper: approve only users with an active subscription
or a valid trial promo invite link.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest, User as TelegramUser

from app.database import async_session_factory
from app.models.models import Plan, Subscription
from app.services.admin_notify import notify_admins
from app.services.grant import grant_subscription
from app.services.membership import get_active_subscription
from app.services.promo import (
    find_trial_by_invite_name,
    increment_promo_use,
    user_matches_audience,
)
from app.services.user_resolve import format_user_ref

logger = logging.getLogger(__name__)
join_router = Router()


def _join_admin_message(
    user: TelegramUser,
    chat_title: str | None,
    chat_id: int,
    sub: Subscription,
    plan_name: str | None,
    *,
    trial: bool = False,
) -> str:
    name = (user.first_name or "").strip()
    label = format_user_ref(user.id, user.username)
    if name:
        label = f"{label} ({name})"
    group = chat_title or str(chat_id)
    plan_line = f"\n套餐：<b>{plan_name}</b>" if plan_name else ""
    title = "✅ <b>新成员入群（体验优惠）</b>" if trial else "✅ <b>新成员入群</b>"
    return (
        f"{title}\n"
        f"用户：{label}\n"
        f"群组：{group} (<code>{chat_id}</code>){plan_line}\n"
        f"到期：<b>{sub.expires_at.strftime('%Y-%m-%d %H:%M UTC')}</b>"
    )


@join_router.chat_join_request()
async def on_join_request(event: ChatJoinRequest):
    user_id = event.from_user.id
    chat_id = event.chat.id
    invite_name = None
    if event.invite_link is not None:
        invite_name = getattr(event.invite_link, "name", None)

    async with async_session_factory() as session:
        sub = await get_active_subscription(session, user_id, chat_id)
        plan_name = None
        trial = False

        if sub:
            plan = await session.get(Plan, sub.plan_id)
            plan_name = plan.name if plan else None
        else:
            promo = await find_trial_by_invite_name(session, invite_name)
            if promo is not None and promo.grant_days >= 1:
                plan = await session.get(Plan, promo.plan_id)
                if plan is None or int(plan.chat_id) != int(chat_id):
                    logger.warning(
                        "Trial promo %s chat mismatch: plan=%s join=%s",
                        promo.id,
                        getattr(plan, "chat_id", None),
                        chat_id,
                    )
                    promo = None
                elif not await user_matches_audience(session, user_id, promo):
                    logger.info(
                        "Trial promo %s rejected for user %d (audience=%s)",
                        promo.id,
                        user_id,
                        getattr(promo.audience, "value", promo.audience),
                    )
                    try:
                        await event.decline()
                    except Exception as e:
                        logger.error("Failed to decline join for user %d: %s", user_id, e)
                    try:
                        await event.bot.send_message(
                            user_id,
                            "此优惠链接不适用于您的账号类型（新/老用户限制）。"
                            "请使用 /start 查看可购买套餐。",
                        )
                    except Exception:
                        pass
                    return
            if promo is not None and promo.grant_days >= 1:
                try:
                    # Ensure user row exists for admin search / export.
                    from app.models.models import User as DbUser

                    db_user = await session.get(DbUser, user_id)
                    if db_user is None:
                        session.add(
                            DbUser(
                                id=user_id,
                                username=event.from_user.username,
                                first_name=event.from_user.first_name or "",
                                last_name=event.from_user.last_name,
                                language_code=event.from_user.language_code,
                            )
                        )
                        await session.flush()

                    order = await grant_subscription(
                        session,
                        user_id,
                        promo.plan_id,
                        promo.grant_days,
                        promo_id=promo.id,
                    )
                    await increment_promo_use(session, promo)
                    await session.commit()
                    sub = await get_active_subscription(session, user_id, chat_id)
                    plan = await session.get(Plan, promo.plan_id)
                    plan_name = plan.name if plan else None
                    trial = True
                    logger.info(
                        "Trial promo %s granted order %s to user %d",
                        promo.id,
                        order.id,
                        user_id,
                    )
                except Exception as e:
                    await session.rollback()
                    logger.error(
                        "Trial promo grant failed user=%d promo=%s: %s",
                        user_id,
                        promo.id,
                        e,
                    )
                    sub = None

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
                    trial=trial,
                )
            )
        except Exception as e:
            logger.warning("Failed to notify admins of join user=%d: %s", user_id, e)
    else:
        try:
            await event.decline()
            logger.info(
                "Declined join request: user %d -> chat %d (no subscription)",
                user_id,
                chat_id,
            )
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
