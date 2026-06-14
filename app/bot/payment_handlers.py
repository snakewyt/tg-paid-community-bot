"""Payment-specific bot handlers (Telegram Stars callbacks)."""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery

from app.database import async_session_factory
from app.payments.base import CallbackData, get_provider
from app.services.notify import notify_fulfillment
from app.services.orders import handle_callback

logger = logging.getLogger(__name__)
payment_router = Router()


@payment_router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@payment_router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    payment = message.successful_payment
    try:
        payload = json.loads(payment.invoice_payload)
        order_id = payload["order_id"]
    except Exception:
        logger.error("Invalid invoice payload: %s", payment.invoice_payload)
        return

    async with async_session_factory() as session:
        provider = get_provider("stars")
        data = CallbackData(
            order_id=order_id,
            raw_body=json.dumps(payment.model_dump(), default=str),
        )
        await handle_callback(session, provider, data)
        await session.commit()

    await notify_fulfillment(order_id)
