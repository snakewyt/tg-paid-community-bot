"""APScheduler jobs: expiry checks, reminders, order timeout cleanup."""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.dispatcher import bot
from app.database import async_session_factory
from app.models.models import Plan, SubscriptionStatus
from app.services.kick import kick_user_from_chat
from app.services.membership import find_expired, mark_kicked
from app.utils import utcnow

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

_REMINDER_COOLDOWN = timedelta(hours=23)


async def _check_expiry():
    """Kick expired subscribers. Keep status active until kick succeeds."""
    async with async_session_factory() as session:
        expired = await find_expired(session)

        for sub in expired:
            plan = await session.get(Plan, sub.plan_id)
            kicked = await kick_user_from_chat(sub.group_chat_id, sub.user_id)
            if not kicked:
                logger.error(
                    "Kick failed user=%d chat=%d — will retry next run",
                    sub.user_id,
                    sub.group_chat_id,
                )
                continue

            await mark_kicked(session, sub)
            plan_name = plan.name if plan else "您的套餐"
            try:
                await bot.send_message(
                    sub.user_id,
                    f"您的 <b>{plan_name}</b> 订阅已到期，已被移出群组。\n"
                    "发送 /start 可立即续费。",
                )
            except Exception as e:
                logger.warning("Expiry notify failed user=%d: %s", sub.user_id, e)
            logger.info("Kicked user %d from chat %d", sub.user_id, sub.group_chat_id)

        await session.commit()


async def _send_reminders():
    from app.config import settings
    from sqlalchemy import or_, select
    from app.models.models import Subscription

    now = utcnow()
    cooldown_cutoff = now - _REMINDER_COOLDOWN

    async with async_session_factory() as session:
        reminder_windows = {int(settings.expiry_reminder_days), 1}
        for days in sorted(reminder_windows, reverse=True):
            if days < 1:
                continue
            deadline = now + timedelta(days=days)
            window_end = deadline + timedelta(hours=6)

            stmt = select(Subscription).where(
                Subscription.status == SubscriptionStatus.active,
                Subscription.expires_at >= deadline,
                Subscription.expires_at < window_end,
                or_(
                    Subscription.last_reminded_at.is_(None),
                    Subscription.last_reminded_at < cooldown_cutoff,
                ),
            )
            subs = (await session.execute(stmt)).scalars().all()

            for sub in subs:
                plan = await session.get(Plan, sub.plan_id)
                plan_name = plan.name if plan else "您的套餐"
                try:
                    msg_template = str(settings.expiry_reminder_message)
                    expiry_date = sub.expires_at.strftime("%Y-%m-%d")
                    msg = msg_template.format(days=days, expiry_date=expiry_date)
                    await bot.send_message(
                        sub.user_id,
                        f"⏰ <b>{plan_name}</b>\n{msg}\n\n发送 /start 续费。",
                    )
                    sub.last_reminded_at = now
                except Exception as e:
                    logger.warning("Reminder failed user=%d: %s", sub.user_id, e)

        await session.commit()


async def _expire_pending_orders():
    from app.services.orders import expire_stale_orders

    async with async_session_factory() as session:
        expired = await expire_stale_orders(session)
        await session.commit()

    for order in expired:
        try:
            await bot.send_message(
                order.user_id,
                "⏰ 您有一笔订单因超时未支付已自动关闭。\n"
                "发送 /start 可重新发起支付。",
            )
        except Exception as e:
            logger.warning("Order expiry notify failed user=%d: %s", order.user_id, e)

    if expired:
        logger.info("Expired %d stale pending orders", len(expired))


def start_scheduler():
    scheduler.add_job(_check_expiry, "interval", hours=1, id="check_expiry")
    scheduler.add_job(_send_reminders, "interval", hours=6, id="send_reminders")
    scheduler.add_job(_expire_pending_orders, "interval", minutes=15, id="expire_orders")
    scheduler.add_job(_cleanup_sessions, "interval", minutes=10, id="cleanup_sessions")
    scheduler.start()
    logger.info(
        "Scheduler started (expiry: hourly, reminders: 6h, order-timeout: 15min, cleanup: 10min)"
    )


async def _cleanup_sessions():
    from app.admin_panel import _cleanup_stale_entries

    removed = _cleanup_stale_entries()
    if removed:
        logger.info("Cleaned up %d stale admin session entries", removed)
