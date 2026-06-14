"""Payment-specific bot handlers (Telegram Stars callbacks)."""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery

from app.database import async_session_factory
from app.models.models import Order, OrderStatus
from app.payments.base import CallbackData, get_provider
from app.services.notify import notify_fulfillment
from app.services.orders import OrderError, handle_callback

logger = logging.getLogger(__name__)
payment_router = Router()


@payment_router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    try:
        payload = json.loads(query.invoice_payload)
        order_id = payload["order_id"]
    except Exception:
        await query.answer(ok=False, error_message="Invalid invoice.")
        return

    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order is None or order.status != OrderStatus.pending:
            await query.answer(ok=False, error_message="Order expired or invalid.")
            return
        if order.user_id != query.from_user.id:
            await query.answer(ok=False, error_message="Order does not belong to you.")
            return
        if int(query.total_amount) != int(order.amount):
            await query.answer(ok=False, error_message="Amount mismatch.")
            return
        if query.currency.upper() != order.currency.upper():
            await query.answer(ok=False, error_message="Currency mismatch.")
            return

    await query.answer(ok=True)


@payment_router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    payment = message.successful_payment
    try:
        payload = json.loads(payment.invoice_payload)
        order_id = payload["order_id"]
    except Exception:
        logger.error("Invalid invoice payload: %s", payment.invoice_payload)
        await message.answer("Payment received but order data is invalid. Contact admin.")
        return

    try:
        async with async_session_factory() as session:
            provider = get_provider("stars")
            data = CallbackData(
                order_id=order_id,
                raw_body=json.dumps(payment.model_dump(), default=str),
            )
            order, newly = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.error("Stars fulfillment failed order=%s: %s", order_id, e)
        await message.answer(
            "⚠️ 支付已收到但开通失败，请联系管理员并提供订单号。"
        )
        return
    except Exception as e:
        logger.error("Stars unexpected error order=%s: %s", order_id, e, exc_info=True)
        await message.answer("Payment error. Contact admin.")
        return

    if newly:
        await notify_fulfillment(order_id)
