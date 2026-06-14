"""HuPiJiao V3 backend (虎皮椒) — internal helpers.

Used as a backend for alipay/wechat user-facing payment channels.
Configured via settings.hupijiao_* and settings.alipay_backend/wechat_backend.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import urllib.parse
import uuid

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import PaymentResult

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.hupijiao_enabled and settings.hupijiao_appid and settings.hupijiao_appsecret)


def _sign(params: dict) -> str:
    raw = "&".join(
        f"{k}={v}"
        for k, v in sorted(params.items())
        if v != "" and k not in ("hash", "sign")
    )
    raw += settings.hupijiao_appsecret
    return hashlib.md5(raw.encode()).hexdigest()


async def create_payment_backend(order: Order, plan: Plan, payment_type: str) -> PaymentResult:
    if not is_configured():
        return PaymentResult(success=False)

    # HuPiJiao V3 uses "alipay" or "wxpay" plugin strings
    now = int(time.time())
    nonce = uuid.uuid4().hex[:16]
    params = {
        "version": "1.1",
        "appid": settings.hupijiao_appid,
        "trade_order_id": order.id,
        "total_fee": f"{order.amount:.2f}",
        "title": f"{plan.name} — {plan.duration_days} days",
        "time": str(now),
        "notify_url": settings.hupijiao_notify_url,
        "return_url": settings.hupijiao_return_url or settings.hupijiao_notify_url,
        "nonce_str": nonce,
        "plugins": order.id + "|" + payment_type,
    }
    params["hash"] = _sign(params)

    pay_url = f"{settings.hupijiao_api_url.rstrip('/')}/pay_redirect.html?{urllib.parse.urlencode(params)}"
    return PaymentResult(
        success=True,
        provider_tx_id=order.id,
        pay_url=pay_url,
    )


def verify_backend_callback(raw_body: str) -> bool:
    try:
        params = dict(urllib.parse.parse_qsl(raw_body))
        their_hash = params.get("hash", "")
        expected = _sign(params)
        return bool(their_hash) and secrets.compare_digest(their_hash, expected) and params.get("status") == "OD"
    except Exception:
        return False


def extract_backend_order_id(raw_body: str) -> str | None:
    try:
        params = dict(urllib.parse.parse_qsl(raw_body))
        plugins = params.get("plugins", "")
        return plugins.split("|")[0] if "|" in plugins else params.get("trade_order_id")
    except Exception:
        return None


def extract_backend_money(raw_body: str) -> float | None:
    try:
        return float(dict(urllib.parse.parse_qsl(raw_body)).get("total_fee", 0))
    except (TypeError, ValueError):
        return None
