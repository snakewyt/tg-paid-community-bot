"""User-facing bot handlers: /start, plan selection, /my."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import bot
from app.config import settings
from app.constants import PROVIDER_LABELS
from app.database import async_session_factory
from app.models.models import Order, Plan, User
from app.payments.base import list_configured_providers, get_provider
from app.services.membership import get_active_subscriptions
from app.services.orders import cancel_user_pending_orders, create_order

user_router = Router()


async def _ensure_user(telegram_user) -> User:
    async with async_session_factory() as session:
        from sqlalchemy import select

        user = await session.get(User, telegram_user.id)
        if user is None:
            user = User(
                id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name or "",
                last_name=telegram_user.last_name,
                language_code=telegram_user.language_code,
            )
            session.add(user)
            await session.commit()
        return user


def _plan_keyboard(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.name} — {plan.duration_days}d",
            callback_data=f"plan_select:{plan.id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def _provider_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    providers = list_configured_providers()
    builder = InlineKeyboardBuilder()
    for p in providers:
        builder.button(
            text=PROVIDER_LABELS.get(p.name, p.name),
            callback_data=f"pay_select:{plan_id}:{p.name}",
        )
    builder.adjust(2)
    return builder.as_markup()


@user_router.message(Command("start"))
async def cmd_start(message: Message):
    await _ensure_user(message.from_user)

    async with async_session_factory() as session:
        from sqlalchemy import select

        plans = (await session.execute(select(Plan).where(Plan.is_active == True))).scalars().all()

    if not plans:
        await message.answer("No plans available yet. Contact admin.")
        return

    providers = list_configured_providers()
    if not providers:
        await message.answer("No payment methods configured yet. Contact admin.")
        return

    welcome = (settings.welcome_message or "").strip()
    text = f"{welcome}\n\n请选择套餐：" if welcome else "请选择套餐："
    await message.answer(
        text,
        reply_markup=_plan_keyboard(list(plans)),
    )


@user_router.callback_query(F.data.startswith("plan_select:"))
async def on_plan_select(callback: CallbackQuery):
    plan_id = int(callback.data.split(":")[1])

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)

    if not plan:
        await callback.answer("Plan not found.", show_alert=True)
        return

    providers = list_configured_providers()
    price_display = []
    for p in providers:
        if p.name == "stars" and plan.price_stars:
            price_display.append(f"Stars: {plan.price_stars} XTR")
        elif p.name == "crypto" and plan.price_crypto:
            line = f"Crypto: {plan.price_crypto} USDT"
            try:
                rate = float(settings.usdt_rate)
                if rate > 0:
                    line += f" (≈¥{plan.price_crypto * rate:.2f})"
            except (TypeError, ValueError):
                pass
            price_display.append(line)
        elif p.name == "stripe" and plan.price_stripe:
            price_display.append(f"Stripe: ${plan.price_stripe / 100:.2f}")
        elif p.name == "alipay" and plan.price_alipay:
            price_display.append(f"支付宝: ¥{plan.price_alipay:.2f}")
        elif p.name == "wechat" and plan.price_wechat:
            price_display.append(f"微信支付: ¥{plan.price_wechat:.2f}")

    text = f"<b>{plan.name}</b>\n"
    if plan.description:
        text += f"{plan.description}\n\n"
    text += "Prices:\n" + "\n".join(f"  • {p}" for p in price_display)
    text += "\n\nSelect payment method:"

    await callback.message.edit_text(
        text,
        reply_markup=_provider_keyboard(plan.id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("pay_select:"))
async def on_pay_select(callback: CallbackQuery):
    parts = callback.data.split(":")
    plan_id = int(parts[1])
    provider_name = parts[2]

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        provider = get_provider(provider_name)

        if not plan or not plan.is_active:
            await callback.answer("This plan is no longer available.", show_alert=True)
            return

        price_map = {
            "stars": ("XTR", plan.price_stars),
            "crypto": ("USDT", plan.price_crypto),
            "stripe": ("USD", plan.price_stripe),
            "alipay": ("CNY", plan.price_alipay),
            "wechat": ("CNY", plan.price_wechat),
        }
        if provider_name not in price_map:
            await callback.answer("Invalid payment method.", show_alert=True)
            return
        currency, amount = price_map[provider_name]

        if not amount:
            await callback.answer("This payment method has no price set.", show_alert=True)
            return

        # Cancel any stale pending orders for the same plan before creating new
        await cancel_user_pending_orders(session, callback.from_user.id, plan_id)

        order = await create_order(
            session, callback.from_user.id, plan, provider, float(amount), currency
        )
        order_id = order.id
        await session.commit()

    result = await provider.create_payment(order, plan)
    if not result.success:
        await callback.message.edit_text(
            "⚠️ 支付创建失败，请稍后重试。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ 重新选择", callback_data=f"plan_select:{plan_id}")],
                ]
            ),
        )
        await callback.answer()
        return

    if provider.name == "stars":
        from aiogram.types import LabeledPrice

        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"{plan.name} — {plan.duration_days} days",
            description=plan.description or plan.name,
            payload=result.invoice_payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=plan.name, amount=int(amount))],
        )
        await callback.message.delete()
        await callback.answer()
        return

    if result.provider_tx_id:
        async with async_session_factory() as session:
            o = await session.get(Order, order_id)
            if o:
                o.external_id = result.provider_tx_id
                await session.commit()

    await callback.message.edit_text(
        f"Pay {amount} {currency} to join <b>{plan.name}</b>:\n\n{result.pay_url}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Pay Now", url=result.pay_url)],
            ]
        ),
    )
    await callback.answer()


@user_router.message(Command("my"))
async def cmd_my(message: Message):
    user = await _ensure_user(message.from_user)

    async with async_session_factory() as session:
        from sqlalchemy import select

        subs = await get_active_subscriptions(session, user.id)
        if not subs:
            await message.answer("You have no active subscriptions. Use /start to subscribe.")
            return

        plan_ids = {s.plan_id for s in subs}
        plans = (
            await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        ).scalars().all()
        plan_map = {p.id: p for p in plans}

    lines = ["<b>Your Subscriptions:</b>", ""]
    for sub in subs:
        plan = plan_map.get(sub.plan_id)
        plan_name = plan.name if plan else f"Plan #{sub.plan_id}"
        exp = sub.expires_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  • {plan_name} — expires {exp}")

    await message.answer("\n".join(lines))
