"""User-facing bot handlers: /start, plan selection, /my."""

from __future__ import annotations

import urllib.parse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import bot
from app.config import settings
from app.constants import PROVIDER_LABELS
from app.database import async_session_factory
from app.models.models import Order, OrderStatus, Plan, User
from app.payments.base import list_configured_providers, get_provider
from app.services.membership import get_active_subscriptions
from app.services.orders import cancel_user_pending_orders, create_order

user_router = Router()

# Persistent bottom-menu button labels (matched by exact message text).
BTN_VIP_GROUP = "👥 VIP群组"
BTN_VIP_CHANNEL = "📢 VIP频道"
BTN_MY_ORDERS = "📋 我的订单"
BTN_PROFILE = "👤 个人中心"
BTN_BUY = "🛒 购买/续费"

_ORDER_STATUS_LABELS = {
    OrderStatus.pending: "待支付",
    OrderStatus.paid: "已支付",
    OrderStatus.fulfilled: "已完成",
    OrderStatus.expired: "已过期",
    OrderStatus.cancelled: "已取消",
}


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard shown under the chat input box."""
    rows = [[KeyboardButton(text=BTN_BUY)]]
    entry_row = []
    if (settings.vip_group_url or "").strip():
        entry_row.append(KeyboardButton(text=BTN_VIP_GROUP))
    if (settings.vip_channel_url or "").strip():
        entry_row.append(KeyboardButton(text=BTN_VIP_CHANNEL))
    if entry_row:
        rows.append(entry_row)
    rows.append([KeyboardButton(text=BTN_MY_ORDERS), KeyboardButton(text=BTN_PROFILE)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="请选择下方菜单",
    )


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
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="checkout_cancel"))
    return builder.as_markup()


def _payment_keyboard(
    plan_id: int,
    order_id: str,
    *,
    pay_url: str | None = None,
    qr_url: str | None = None,
    mobile_label: str = "Pay Now",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pay_url:
        rows.append([InlineKeyboardButton(text=f"📱 {mobile_label}", url=pay_url)])
    if qr_url:
        rows.append([InlineKeyboardButton(text="🖥 网页二维码", url=qr_url)])
    rows.append([InlineKeyboardButton(text="❌ 取消支付", callback_data=f"pay_cancel:{order_id}:{plan_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_plan_selection(message: Message, *, header: str | None = None) -> None:
    """Send the plan list with an inline keyboard as a fresh message."""
    async with async_session_factory() as session:
        from sqlalchemy import select

        plans = (
            await session.execute(select(Plan).where(Plan.is_active == True))
        ).scalars().all()

    if not plans:
        await message.answer("暂无可购买的套餐，请联系管理员。")
        return

    providers = list_configured_providers()
    if not providers:
        await message.answer("暂未配置支付方式，请联系管理员。")
        return

    await message.answer(
        header or "请选择套餐：",
        reply_markup=_plan_keyboard(list(plans)),
    )


@user_router.message(Command("start"))
async def cmd_start(message: Message):
    await _ensure_user(message.from_user)

    welcome = (settings.welcome_message or "").strip() or "欢迎使用VIP会员机器人！"
    await message.answer(welcome, reply_markup=_main_menu_keyboard())
    await _send_plan_selection(message)


@user_router.message(F.text == BTN_BUY)
async def on_menu_buy(message: Message):
    await _ensure_user(message.from_user)
    await _send_plan_selection(message)


@user_router.message(F.text == BTN_VIP_GROUP)
async def on_menu_vip_group(message: Message):
    url = (settings.vip_group_url or "").strip()
    if not url:
        await message.answer("管理员尚未配置 VIP 群组入口。")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="👉 进入 VIP 群组", url=url)]]
    )
    await message.answer(
        "点击下方按钮进入 VIP 群组：\n\n"
        "· 需持有有效会员，入群申请会自动通过\n"
        "· 未购买请先点「🛒 购买/续费」",
        reply_markup=kb,
    )


@user_router.message(F.text == BTN_VIP_CHANNEL)
async def on_menu_vip_channel(message: Message):
    url = (settings.vip_channel_url or "").strip()
    if not url:
        await message.answer("管理员尚未配置 VIP 频道入口。")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="👉 进入 VIP 频道", url=url)]]
    )
    await message.answer(
        "点击下方按钮进入 VIP 频道：\n\n"
        "· 需持有有效会员，入群申请会自动通过\n"
        "· 未购买请先点「🛒 购买/续费」",
        reply_markup=kb,
    )


@user_router.message(F.text == BTN_MY_ORDERS)
async def on_menu_my_orders(message: Message):
    await _ensure_user(message.from_user)
    async with async_session_factory() as session:
        from sqlalchemy import select

        orders = (
            await session.execute(
                select(Order)
                .where(Order.user_id == message.from_user.id)
                .order_by(Order.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

        if not orders:
            await message.answer("你还没有任何订单，点「🛒 购买/续费」开始吧。")
            return

        plan_ids = {o.plan_id for o in orders}
        plans = (
            await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        ).scalars().all()
        plan_map = {p.id: p for p in plans}

    lines = ["<b>最近订单（最多 10 条）：</b>", ""]
    for o in orders:
        plan = plan_map.get(o.plan_id)
        plan_name = plan.name if plan else f"套餐#{o.plan_id}"
        status = _ORDER_STATUS_LABELS.get(o.status, o.status.value)
        provider = PROVIDER_LABELS.get(o.provider.value, o.provider.value)
        date = o.created_at.strftime("%Y-%m-%d %H:%M")
        revived = " 🔁迟付复活" if o.revived else ""
        lines.append(
            f"· <b>{plan_name}</b> — {o.amount:g} {o.currency} / {provider}\n"
            f"    {status}{revived} · {date}"
        )

    await message.answer("\n".join(lines))


@user_router.message(F.text == BTN_PROFILE)
async def on_menu_profile(message: Message):
    user = await _ensure_user(message.from_user)
    async with async_session_factory() as session:
        subs = await get_active_subscriptions(session, user.id)
        plan_map: dict[int, Plan] = {}
        if subs:
            from sqlalchemy import select

            plan_ids = {s.plan_id for s in subs}
            plans = (
                await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))
            ).scalars().all()
            plan_map = {p.id: p for p in plans}

    name = (message.from_user.first_name or "").strip()
    if message.from_user.last_name:
        name = f"{name} {message.from_user.last_name}".strip()
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    lines = [
        "<b>👤 个人中心</b>",
        "",
        f"昵称：{name or '—'}",
        f"用户名：{username}",
        f"ID：<code>{user.id}</code>",
        "",
    ]
    if subs:
        lines.append(f"<b>有效会员（{len(subs)}）：</b>")
        for sub in subs:
            plan = plan_map.get(sub.plan_id)
            plan_name = plan.name if plan else f"套餐#{sub.plan_id}"
            exp = sub.expires_at.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"· {plan_name} — 到期 {exp}")
    else:
        lines.append("暂无有效会员，点「🛒 购买/续费」开通。")

    await message.answer("\n".join(lines))


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
            price_display.append(f"Crypto: {plan.price_crypto} USDT")
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


async def _show_plan_list(target: Message) -> None:
    async with async_session_factory() as session:
        from sqlalchemy import select

        plans = (await session.execute(select(Plan).where(Plan.is_active == True))).scalars().all()

    if not plans:
        await target.edit_text("No plans available yet. Contact admin.")
        return

    welcome = (settings.welcome_message or "").strip()
    text = f"{welcome}\n\n请选择套餐：" if welcome else "请选择套餐："
    await target.edit_text(text, reply_markup=_plan_keyboard(list(plans)))


@user_router.callback_query(F.data == "checkout_cancel")
async def on_checkout_cancel(callback: CallbackQuery):
    await _show_plan_list(callback.message)
    await callback.answer("已取消")


@user_router.callback_query(F.data.startswith("pay_cancel:"))
async def on_pay_cancel(callback: CallbackQuery):
    parts = callback.data.split(":")
    order_id = parts[1]
    plan_id = int(parts[2]) if len(parts) > 2 else None

    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if (
            order
            and order.user_id == callback.from_user.id
            and order.status == OrderStatus.pending
        ):
            order.status = OrderStatus.cancelled
        if plan_id is not None:
            plan = await session.get(Plan, plan_id)
        else:
            plan = None
        await session.commit()

    try:
        await callback.message.delete()
    except Exception:
        pass

    if plan and plan.is_active:
        providers = list_configured_providers()
        price_display = []
        for p in providers:
            if p.name == "stars" and plan.price_stars:
                price_display.append(f"Stars: {plan.price_stars} XTR")
            elif p.name == "crypto" and plan.price_crypto:
                price_display.append(f"Crypto: {plan.price_crypto} USDT")
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
        await bot.send_message(
            callback.from_user.id,
            text,
            reply_markup=_provider_keyboard(plan.id),
        )
    else:
        plans = []
        async with async_session_factory() as session:
            from sqlalchemy import select

            plans = (
                await session.execute(select(Plan).where(Plan.is_active == True))
            ).scalars().all()
        welcome = (settings.welcome_message or "").strip()
        text = f"{welcome}\n\n请选择套餐：" if welcome else "请选择套餐："
        await bot.send_message(
            callback.from_user.id,
            text,
            reply_markup=_plan_keyboard(list(plans)),
        )

    await callback.answer("已取消支付")


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

    if provider.name in ("alipay", "wechat") and result.pay_url:
        label = "打开支付宝" if provider.name == "alipay" else "打开微信支付"
        keyboard = _payment_keyboard(
            plan_id,
            order_id,
            pay_url=result.pay_url,
            qr_url=result.qr_url,
            mobile_label=label,
        )
        caption = (
            f"请支付 <b>{amount}</b> {currency} 购买 <b>{plan.name}</b>\n\n"
            "📷 请用支付宝扫描下方二维码，或点击按钮在手机中付款。"
            if provider.name == "alipay"
            else f"请支付 <b>{amount}</b> {currency} 购买 <b>{plan.name}</b>\n\n"
            "📷 请用微信扫描下方二维码，或点击按钮在手机中付款。"
        )
        qr_image = (
            "https://api.qrserver.com/v1/create-qr-code/?size=400x400&data="
            + urllib.parse.quote(result.pay_url, safe="")
        )
        await callback.message.delete()
        sent = await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=qr_image,
            caption=caption,
            reply_markup=keyboard,
        )
        async with async_session_factory() as session:
            o = await session.get(Order, order_id)
            if o:
                o.payment_message_id = sent.message_id
                await session.commit()
        await callback.answer()
        return

    await callback.message.edit_text(
        f"Pay {amount} {currency} to join <b>{plan.name}</b>:\n\n{result.pay_url}",
        reply_markup=_payment_keyboard(
            plan_id,
            order_id,
            pay_url=result.pay_url,
            mobile_label="Pay Now",
        ),
    )
    async with async_session_factory() as session:
        o = await session.get(Order, order_id)
        if o:
            o.payment_message_id = callback.message.message_id
            await session.commit()
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
