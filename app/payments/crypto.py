"""CryptoBot (Crypto Pay API) payment provider."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import BasePaymentProvider, CallbackData, PaymentResult, register_provider

logger = logging.getLogger(__name__)

CRYPTO_API_BASE = "https://pay.crypt.bot/api"


def _parse_body(raw_body: str) -> dict | None:
    try:
        return json.loads(raw_body)
    except Exception:
        return None


class CryptoProvider(BasePaymentProvider):
    name = "crypto"

    def is_configured(self) -> bool:
        return bool(
            settings.crypto_enabled
            and settings.crypto_api_token
        )

    def _headers(self) -> dict:
        return {"Crypto-Pay-API-Token": settings.crypto_api_token}

    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        url = f"{CRYPTO_API_BASE}/createInvoice"
        payload = {
            "asset": "USDT",
            "amount": str(order.amount),
            "description": f"{plan.name} — {plan.duration_days} days",
            "payload": order.id,
            "allow_comments": False,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            data = resp.json()

        if not data.get("ok"):
            logger.error("CryptoBot createInvoice failed: %s", data)
            return PaymentResult(success=False)

        invoice = data["result"]
        return PaymentResult(
            success=True,
            provider_tx_id=str(invoice["invoice_id"]),
            pay_url=invoice["pay_url"],
        )

    def _check_signature(self, body: str, sig_header: str) -> bool:
        secret = hashlib.sha256(settings.crypto_api_token.encode()).digest()
        expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_header)

    async def verify_callback(self, data: CallbackData) -> bool:
        if not data.signature:
            return False
        if not self._check_signature(data.raw_body, data.signature):
            return False
        body = _parse_body(data.raw_body)
        if not body or body.get("update_type") != "invoice_paid":
            return False
        invoice = body.get("payload")
        if not isinstance(invoice, dict) or invoice.get("status") != "paid":
            return False
        return True

    async def extract_order_id(self, data: CallbackData) -> str | None:
        body = _parse_body(data.raw_body)
        if not body:
            return None
        invoice = body.get("payload")
        if isinstance(invoice, dict):
            oid = invoice.get("payload")
            return str(oid) if oid else None
        return None

    async def verify_payment_amount(self, data: CallbackData, order: Order) -> bool:
        if order.amount == 0:
            return True
        body = _parse_body(data.raw_body)
        if not body:
            return False
        invoice = body.get("payload")
        if not isinstance(invoice, dict):
            return False
        try:
            paid = float(invoice.get("amount", 0))
        except (TypeError, ValueError):
            return False
        asset = str(invoice.get("asset", "")).upper()
        if asset and asset != order.currency.upper():
            return False
        return abs(paid - order.amount) < 0.001


provider = CryptoProvider()
register_provider(provider)
