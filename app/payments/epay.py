"""Epay backend (易支付 protocol) — internal helpers.

Used as a backend for alipay/wechat user-facing payment channels.
Configured via settings.epay_* and settings.alipay_backend/wechat_backend.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import urllib.parse

import httpx

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import PaymentResult

logger = logging.getLogger(__name__)

PAYMENT_TYPES = {"alipay": "alipay", "wechat": "wxpay"}


def is_configured() -> bool:
    return bool(settings.epay_enabled and settings.epay_pid and settings.epay_key)


def _sign(params: dict) -> str:
    raw = "&".join(
        f"{k}={v}"
        for k, v in sorted(params.items())
        if v != "" and k not in ("sign", "sign_type")
    )
    raw += settings.epay_key
    return hashlib.md5(raw.encode()).hexdigest()


async def create_payment_backend(order: Order, plan: Plan, payment_type: str) -> PaymentResult:
    if not is_configured():
        return PaymentResult(success=False)

    ptype = PAYMENT_TYPES.get(payment_type, payment_type)
    params = {
        "pid": settings.epay_pid,
        "type": ptype,
        "out_trade_no": order.id,
        "notify_url": settings.epay_notify_url,
        "return_url": settings.epay_return_url or settings.epay_notify_url,
        "name": f"{plan.name} — {plan.duration_days} days",
        "money": str(order.amount),
    }
    params["sign"] = _sign(params)
    params["sign_type"] = "MD5"

    submit_url = f"{settings.epay_api_url.rstrip('/')}/submit.php"
    return PaymentResult(
        success=True,
        provider_tx_id=order.id,
        pay_url=f"{submit_url}?{urllib.parse.urlencode(params)}",
    )


def verify_backend_callback(raw_body: str) -> bool:
    try:
        params = dict(urllib.parse.parse_qsl(raw_body))
        their_sign = params.pop("sign", "")
        params.pop("sign_type", None)
        return secrets.compare_digest(_sign(params), their_sign) and params.get("trade_status") == "TRADE_SUCCESS"
    except Exception:
        return False


def extract_backend_order_id(raw_body: str) -> str | None:
    try:
        return dict(urllib.parse.parse_qsl(raw_body)).get("out_trade_no")
    except Exception:
        return None
