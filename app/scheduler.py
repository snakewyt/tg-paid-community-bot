"""APScheduler jobs: expiry checks, reminders, order timeout cleanup."""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.dispatcher import bot
from app.database import async_session_factory
from app.models.models import Plan, SubscriptionStatus
from app.services.membership import find_expired, mark_expired, mark_kicked
from app.utils import utcnow

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _check_expiry():
    """Check expired subscriptions, kick users, and send reminders."""
    async with async_session_factory() as session:
        expired = await find_expired(session)

        for sub in expired:
            plan = await session.get(Plan, sub.plan_id)

            try:
                if sub.status == SubscriptionStatus.active:
                    await bot.ban_chat_member(chat_id=sub.group_chat_id, user_id=sub.user_id)
                    await bot.unban_chat_member(chat_id=sub.group_chat_id, user_id=sub.user_id)
                    await mark_kicked(session, sub)

                    plan_name = plan.name if plan else "您的套餐"
                    await bot.send_message(
                        sub.user_id,
                        f"您的 <b>{plan_name}</b> 订阅已到期，已被移出群组。\n"
                        "发送 /start 可立即续费。",
                    )
                    logger.info("Kicked user %d from chat %d", sub.user_id, sub.group_chat_id)
            except Exception as e:
                logger.error("Failed to kick user %d: %s", sub.user_id, e)
                await mark_expired(session, sub)

        await session.commit()


# A subscription won't be reminded twice within this window. It is shorter than
# the gap between reminder thresholds (>=1 day) but longer than the job interval,
# so each threshold fires exactly once and the 6h job drift can't double-send.
_REMINDER_COOLDOWN = timedelta(hours=23)


async def _send_reminders():
    """Send renewal reminders using the configurable expiry_reminder_days setting.

    Dedupe is enforced via Subscription.last_reminded_at: a subscription reminded
    within _REMINDER_COOLDOWN is skipped. The flag is reset to NULL on renewal
    (see app.services.orders._fulfill), so the next cycle reminds again.
    """
    from app.config import settings
    from sqlalchemy import or_, select
    from app.models.models import Subscription

    now = utcnow()
    cooldown_cutoff = now - _REMINDER_COOLDOWN

    async with async_session_factory() as session:
        # Primary reminder: N days before (configurable), plus a fixed 1-day reminder
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
                    msg = msg_template.format(days=days)
                    await bot.send_message(
                        sub.user_id,
                        f"⏰ <b>{plan_name}</b>\n{msg}\n\n发送 /start 续费。",
                    )
                    sub.last_reminded_at = now
                except Exception:
                    pass

        await session.commit()


async def _expire_pending_orders():
    """Mark timed-out pending orders as expired and notify users."""
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
        except Exception:
            pass

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
    """Clean up stale admin sessions and rate-limit entries."""
    from app.admin_panel import _cleanup_stale_entries

    removed = _cleanup_stale_entries()
    if removed:
        logger.info("Cleaned up %d stale admin session entries", removed)
