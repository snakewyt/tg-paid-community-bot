"""Telegram Stars payment provider (native Telegram Payments)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import BasePaymentProvider, CallbackData, PaymentResult, register_provider

logger = logging.getLogger(__name__)


class StarsProvider(BasePaymentProvider):
    name = "stars"

    def is_configured(self) -> bool:
        return bool(settings.stars_enabled and settings.bot_token)

    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        # Telegram Stars payment is initiated by the bot handler via sendInvoice.
        # We just return the order payload to be used as invoice payload.
        payload = json.dumps({"order_id": order.id, "provider": self.name})
        return PaymentResult(
            success=True,
            invoice_payload=payload,
        )

    async def verify_callback(self, data: CallbackData) -> bool:
        # Telegram Stars callbacks are verified by Telegram itself via
        # pre_checkout_query / successful_payment handlers. No extra sig check needed.
        return True

    async def extract_order_id(self, data: CallbackData) -> str | None:
        return data.order_id


provider = StarsProvider()
register_provider(provider)
