"""Admin bot commands: plan management, stats, grant, broadcast."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot.dispatcher import bot
from app.config import settings
from app.database import async_session_factory
from app.models.models import (
    Order,
    OrderStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.services.grant import grant_subscription
from app.services.kick import kick_user_from_chat
from app.services.notify import notify_fulfillment
from app.utils import apply_expiry_delta, is_telegram_admin, utcnow

admin_router = Router()
logger = logging.getLogger(__name__)


def _is_admin(user) -> bool:
    """Admin if Telegram user ID or username matches configured admins."""
    return is_telegram_admin(user, settings.admin_ids, settings.admin_usernames)


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not _is_admin(message.from_user):
        return
    await message.answer(
        "Admin commands:\n"
        "/plans — list plans\n"
        "/addplan — add a plan\n"
        "/editplan <id> <field> <value> — edit a plan\n"
        "/delplan <id> — delete a plan\n"
        "/stats — revenue & member stats\n"
        "/grant <user_id> <plan_id> <days> — gift subscription\n"
        "/setexpiry <user_id> <plan_id> <+N|-N|YYYY-MM-DD> — adjust expiry\n"
        "/broadcast <text> — send message to all users\n"
        "/active — list active subscriptions"
    )


@admin_router.message(Command("plans"))
async def cmd_plans(message: Message):
    if not _is_admin(message.from_user):
        return
    async with async_session_factory() as session:
        from sqlalchemy import select

        plans = (await session.execute(select(Plan))).scalars().all()

    if not plans:
        await message.answer("No plans defined.")
        return

    lines = ["<b>Plans:</b>", ""]
    for p in plans:
        active = "active" if p.is_active else "inactive"
        lines.append(
            f"  • <b>#{p.id} {p.name}</b> — {p.duration_days}d, "
            f"chat_id={p.chat_id} [{active}]"
        )
        prices = []
        if p.price_stars:
            prices.append(f"{p.price_stars} XTR")
        if p.price_crypto:
            prices.append(f"{p.price_crypto} USDT")
        if p.price_stripe:
            prices.append(f"${p.price_stripe / 100:.2f}")
        if p.price_alipay:
            prices.append(f"支付宝 ¥{p.price_alipay:.2f}")
        if p.price_wechat:
            prices.append(f"微信 ¥{p.price_wechat:.2f}")
        lines.append(f"      Prices: {', '.join(prices)}")
    await message.answer("\n".join(lines))


@admin_router.message(Command("addplan"))
async def cmd_addplan(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return

    # Format: /addplan name duration_days chat_id star_price crypto_price stripe_price_cents alipay_price wechat_price
    args = (command.args or "").split()
    if len(args) < 4:
        await message.answer(
            "Usage: /addplan name duration_days chat_id [stars] [crypto] [stripe_cents] [alipay] [wechat]\n"
            "Example: /addplan VIP 30 -1001234567890 100 5 999 29.90 29.90"
        )
        return

    try:
        name = args[0]
        duration = int(args[1])
        chat_id = int(args[2])
        stars = int(args[3]) if len(args) > 3 else 0
        crypto = float(args[4]) if len(args) > 4 else 0.0
        stripe = int(args[5]) if len(args) > 5 else 0
        alipay = float(args[6]) if len(args) > 6 else 0.0
        wechat = float(args[7]) if len(args) > 7 else 0.0
    except (ValueError, IndexError):
        await message.answer("Invalid argument format.")
        return

    async with async_session_factory() as session:
        plan = Plan(
            name=name,
            duration_days=duration,
            chat_id=chat_id,
            price_stars=stars,
            price_crypto=crypto,
            price_stripe=stripe,
            price_alipay=alipay,
            price_wechat=wechat,
        )
        session.add(plan)
        await session.commit()
        await message.answer(f"Plan <b>{name}</b> created (id={plan.id}).")

        # Ensure bot can access the group (same check as admin panel)
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
            if member.status not in ("administrator", "creator"):
                await message.answer(
                    "Warning: Bot is not an admin in that chat. "
                    "Add the bot as admin with invite-link permissions."
                )
        except Exception as e:
            await message.answer(
                f"Warning: Could not verify bot access to chat {chat_id}: {e}"
            )


# Editable plan fields: command alias -> (model attribute, value parser)
_PLAN_FIELDS = {
    "name": ("name", str),
    "days": ("duration_days", int),
    "stars": ("price_stars", int),
    "crypto": ("price_crypto", float),
    "stripe": ("price_stripe", int),
    "cny": ("price_alipay", float),
    "wechat": ("price_wechat", float),
}


@admin_router.message(Command("editplan"))
async def cmd_editplan(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return

    # /editplan <plan_id> <field> <value>
    args = (command.args or "").split(maxsplit=2)
    if len(args) < 3 or args[1] not in _PLAN_FIELDS:
        await message.answer(
            "Usage: /editplan <plan_id> <field> <value>\n"
            f"Fields: {', '.join(_PLAN_FIELDS)}\n"
            "Examples:\n"
            "  /editplan 1 days 60\n"
            "  /editplan 1 stars 200\n"
            "  /editplan 1 name VIP-Gold"
        )
        return

    attr, parser = _PLAN_FIELDS[args[1]]
    try:
        plan_id = int(args[0])
        value = parser(args[2])
    except ValueError:
        await message.answer(f"Invalid value for field <b>{args[1]}</b>.")
        return

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if not plan:
            await message.answer("Plan not found.")
            return
        old = getattr(plan, attr)
        setattr(plan, attr, value)
        await session.commit()

    await message.answer(
        f"Plan <b>{plan.name}</b>: {args[1]} changed {old} → {value}.\n"
        "Note: existing subscriptions are not affected; only future purchases."
    )


@admin_router.message(Command("setexpiry"))
async def cmd_setexpiry(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return

    # /setexpiry <user_id> <plan_id> <+N|-N|YYYY-MM-DD>
    args = (command.args or "").split()
    if len(args) < 3:
        await message.answer(
            "Usage: /setexpiry <user_id> <plan_id> <+N|-N|YYYY-MM-DD>\n"
            "Examples:\n"
            "  /setexpiry 123456789 1 +7    (extend 7 days)\n"
            "  /setexpiry 123456789 1 -3    (reduce 3 days)\n"
            "  /setexpiry 123456789 1 2026-12-31  (set exact date)"
        )
        return

    try:
        user_id = int(args[0])
        plan_id = int(args[1])
    except ValueError:
        await message.answer("Invalid user_id or plan_id.")
        return

    raw = args[2]
    async with async_session_factory() as session:
        from sqlalchemy import select

        sub = (
            await session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.plan_id == plan_id,
                    Subscription.status == SubscriptionStatus.active,
                )
            )
        ).scalars().first()

        if not sub:
            await message.answer(
                f"No active subscription found for user {user_id} on plan {plan_id}."
            )
            return

        try:
            sub.expires_at = apply_expiry_delta(sub.expires_at, raw)
        except ValueError:
            await message.answer("Invalid value. Use +N, -N, or YYYY-MM-DD.")
            return

        sub.last_reminded_at = None
        new_expiry = sub.expires_at
        chat_id = sub.group_chat_id
        await session.commit()

    if new_expiry <= utcnow():
        from sqlalchemy import select

        if await kick_user_from_chat(chat_id, user_id):
            async with async_session_factory() as session:
                sub = (
                    await session.execute(
                        select(Subscription).where(
                            Subscription.user_id == user_id,
                            Subscription.plan_id == plan_id,
                            Subscription.status == SubscriptionStatus.active,
                        )
                    )
                ).scalars().first()
                if sub:
                    sub.status = SubscriptionStatus.kicked
                    await session.commit()
            expired_note = "\nUser kicked immediately."
        else:
            expired_note = "\nKick failed — will retry on next hourly check."
    else:
        expired_note = ""

    await message.answer(
        f"User {user_id} subscription expiry set to "
        f"<b>{new_expiry.strftime('%Y-%m-%d %H:%M UTC')}</b>.{expired_note}"
    )

    try:
        await bot.send_message(
            user_id,
            f"Your subscription expiry has been updated to "
            f"<b>{new_expiry.strftime('%Y-%m-%d %H:%M UTC')}</b>.",
        )
    except Exception as e:
        logger.warning("setexpiry notify failed user=%d: %s", user_id, e)


@admin_router.message(Command("delplan"))
async def cmd_delplan(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return
    if not command.args:
        await message.answer("Usage: /delplan <plan_id>")
        return

    async with async_session_factory() as session:
        plan = await session.get(Plan, int(command.args.strip()))
        if not plan:
            await message.answer("Plan not found.")
            return
        plan.is_active = False
        await session.commit()
        await message.answer(f"Plan <b>{plan.name}</b> deactivated.")


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not _is_admin(message.from_user):
        return
    async with async_session_factory() as session:
        from sqlalchemy import select, func

        active_count = (
            await session.execute(
                select(func.count()).where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at > utcnow(),
                )
            )
        ).scalar() or 0

        # Revenue – SQL aggregation per currency
        revenue: dict[str, float] = {}
        revenue_rows = (
            await session.execute(
                select(Order.currency, func.sum(Order.amount))
                .where(Order.status == OrderStatus.fulfilled, Order.amount > 0)
                .group_by(Order.currency)
            )
        ).all()
        for currency, total in revenue_rows:
            revenue[currency] = float(total or 0)

        paid_count = (
            await session.execute(
                select(func.count()).where(Order.status == OrderStatus.fulfilled)
            )
        ).scalar() or 0

        total_users = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar() or 0

    lines = [
        "<b>Stats:</b>",
        "",
        f"  Total users: {total_users}",
        f"  Active subscribers: {active_count}",
        f"  Total paid orders: {paid_count}",
    ]
    if revenue:
        lines.append("")
        lines.append("<b>Revenue:</b>")
        for curr, amt in revenue.items():
            lines.append(f"  {curr}: {amt:.2f}")

    await message.answer("\n".join(lines))


@admin_router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return
    # /grant <user_id> <plan_id> <days>
    args = (command.args or "").split()
    if len(args) < 3:
        await message.answer("Usage: /grant <user_id> <plan_id> <days>")
        return
    try:
        user_id = int(args[0])
        plan_id = int(args[1])
        days = int(args[2])
    except ValueError:
        await message.answer("Invalid arguments.")
        return

    async with async_session_factory() as session:
        try:
            order = await grant_subscription(session, user_id, plan_id, days)
            plan = await session.get(Plan, plan_id)
            await session.commit()
        except ValueError as e:
            await message.answer(str(e))
            return

    await message.answer(f"Granted {days} days of <b>{plan.name}</b> to user {user_id}.")

    # Send invite link to the gifted user
    link = await notify_fulfillment(order.id)
    if link:
        await message.answer(f"已自动发送邀请链接给用户 {user_id}。")
    else:
        await message.answer(
            f"⚠️ 邀请链接创建失败，请手动将用户 {user_id} 拉入群组 {plan.chat_id}，"
            f"或确认机器人是该群管理员。",
        )


@admin_router.message(Command("active"))
async def cmd_active(message: Message):
    if not _is_admin(message.from_user):
        return
    async with async_session_factory() as session:
        from sqlalchemy import select

        subs = (
            await session.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at > utcnow(),
                )
            )
        ).scalars().all()

    if not subs:
        await message.answer("No active subscriptions.")
        return

    plan_ids = {s.plan_id for s in subs}
    async with async_session_factory() as session:
        from sqlalchemy import select

        plans = (
            await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        ).scalars().all()
    plan_map = {p.id: p for p in plans}

    lines = ["<b>Active subscriptions:</b>", ""]
    for s in subs[:100]:
        plan = plan_map.get(s.plan_id)
        lines.append(
            f"  • user={s.user_id} | {plan.name if plan else 'N/A'} "
            f"| expires {s.expires_at.strftime('%Y-%m-%d')}"
        )
    if len(subs) > 100:
        lines.append(f"\n… and {len(subs) - 100} more (showing first 100)")
    await message.answer("\n".join(lines))


@admin_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if not _is_admin(message.from_user):
        return
    if not command.args:
        await message.answer("Usage: /broadcast <message>")
        return

    async with async_session_factory() as session:
        from sqlalchemy import select

        user_ids = (await session.execute(select(User.id))).scalars().all()

    total = len(user_ids)
    if total > 5000:
        await message.answer(f"Broadcast capped at 5000 users (total {total}).")
        user_ids = user_ids[:5000]
    sent = 0
    failed = 0
    # Telegram allows ~30 msg/s to different users; stay under it.
    delay = 1 / 25
    for uid in user_ids:
        try:
            await bot.send_message(uid, command.args)
            sent += 1
        except TelegramRetryAfter as e:
            # Hit the global flood limit — wait the server-specified time, retry once.
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, command.args)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # User blocked the bot / never started it.
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(delay)

    await message.answer(f"Broadcast finished: {sent} sent, {failed} failed (total {total}).")
