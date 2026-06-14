"""Telegram Stars payment provider (native Telegram Payments)."""

from __future__ import annotations

import json
import logging

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import BasePaymentProvider, CallbackData, PaymentResult, register_provider

logger = logging.getLogger(__name__)


class StarsProvider(BasePaymentProvider):
    name = "stars"

    def is_configured(self) -> bool:
        return bool(settings.stars_enabled and settings.bot_token)

    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        payload = json.dumps({"order_id": order.id, "provider": self.name})
        return PaymentResult(
            success=True,
            invoice_payload=payload,
        )

    async def verify_callback(self, data: CallbackData) -> bool:
        return bool(data.order_id)

    async def extract_order_id(self, data: CallbackData) -> str | None:
        return data.order_id

    async def verify_payment_amount(self, data: CallbackData, order: Order) -> bool:
        if order.amount == 0:
            return True
        try:
            body = json.loads(data.raw_body)
            paid = int(body.get("total_amount", -1))
            currency = str(body.get("currency", "")).upper()
            return paid == int(order.amount) and currency == order.currency.upper()
        except Exception:
            return False


provider = StarsProvider()
register_provider(provider)
