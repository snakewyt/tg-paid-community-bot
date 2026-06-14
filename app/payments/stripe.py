"""Stripe Checkout payment provider."""

from __future__ import annotations

import json
import logging

import stripe

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import BasePaymentProvider, CallbackData, PaymentResult, register_provider

logger = logging.getLogger(__name__)


def construct_stripe_event(raw_body: str, signature: str):
    """Verify signature and return parsed Stripe event."""
    stripe.api_key = settings.stripe_secret_key
    return stripe.Webhook.construct_event(
        raw_body,
        signature or "",
        settings.stripe_webhook_secret,
    )


class StripeProvider(BasePaymentProvider):
    name = "stripe"

    def is_configured(self) -> bool:
        return bool(
            settings.stripe_enabled
            and settings.stripe_secret_key
            and settings.stripe_webhook_secret
        )

    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        stripe.api_key = settings.stripe_secret_key
        try:
            checkout = stripe.checkout.Session.create(
                mode="payment",
                success_url=settings.stripe_success_url,
                cancel_url=settings.stripe_cancel_url,
                client_reference_id=order.id,
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": f"{plan.name} — {plan.duration_days} days",
                            },
                            "unit_amount": int(order.amount),
                        },
                        "quantity": 1,
                    }
                ],
                metadata={"order_id": order.id},
            )
            return PaymentResult(
                success=True,
                provider_tx_id=checkout.id,
                pay_url=checkout.url,
            )
        except Exception as e:
            logger.error("Stripe checkout creation failed: %s", e)
            return PaymentResult(success=False)

    async def verify_callback(self, data: CallbackData) -> bool:
        try:
            event = construct_stripe_event(data.raw_body, data.signature or "")
            return event.get("type") == "checkout.session.completed"
        except Exception:
            return False

    async def extract_order_id(self, data: CallbackData) -> str | None:
        try:
            event = json.loads(data.raw_body)
            obj = event.get("data", {}).get("object", {})
            return obj.get("client_reference_id") or obj.get("metadata", {}).get("order_id")
        except Exception:
            return None

    async def verify_payment_amount(self, data: CallbackData, order: Order) -> bool:
        if order.amount == 0:
            return True
        try:
            event = json.loads(data.raw_body)
            obj = event.get("data", {}).get("object", {})
            paid_cents = obj.get("amount_total")
            if paid_cents is None:
                return False
            return int(paid_cents) == int(order.amount)
        except Exception:
            return False

    async def extract_external_id(self, data: CallbackData) -> str | None:
        try:
            event = json.loads(data.raw_body)
            obj = event.get("data", {}).get("object", {})
            return obj.get("payment_intent") or obj.get("id")
        except Exception:
            return None


provider = StripeProvider()
register_provider(provider)
