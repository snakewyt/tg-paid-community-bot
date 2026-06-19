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

import httpx

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


def _payment_endpoint() -> str:
    base = settings.hupijiao_api_url.rstrip("/")
    if base.endswith("/payment/do.html"):
        return base
    return f"{base}/payment/do.html"


async def create_payment_backend(order: Order, plan: Plan, payment_type: str) -> PaymentResult:
    if not is_configured():
        return PaymentResult(success=False)

    now = int(time.time())
    nonce = uuid.uuid4().hex[:16]
    title = f"{plan.name} {plan.duration_days}days"
    params = {
        "version": "1.1",
        "appid": settings.hupijiao_appid,
        "trade_order_id": order.id,
        "total_fee": f"{order.amount:.2f}",
        "title": title[:127],
        "time": str(now),
        "notify_url": settings.hupijiao_notify_url,
        "return_url": settings.hupijiao_return_url or settings.hupijiao_notify_url,
        "nonce_str": nonce,
        "plugins": f"{order.id}|{payment_type}",
    }
    params["hash"] = _sign(params)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_payment_endpoint(), data=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("HuPiJiao payment request failed: %s", e)
        return PaymentResult(success=False)

    if data.get("errcode") != 0:
        logger.error("HuPiJiao payment rejected: %s %s", data.get("errcode"), data.get("errmsg"))
        return PaymentResult(success=False)

    pay_url = data.get("url") or data.get("url_qrcode")
    qr_url = data.get("url_qrcode")
    if not pay_url:
        logger.error("HuPiJiao response missing payment url: %s", data)
        return PaymentResult(success=False)

    return PaymentResult(
        success=True,
        provider_tx_id=order.id,
        pay_url=pay_url,
        qr_url=qr_url,
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
        if params.get("trade_order_id"):
            return params["trade_order_id"]
        plugins = params.get("plugins", "")
        return plugins.split("|")[0] if "|" in plugins else None
    except Exception:
        return None


def extract_backend_money(raw_body: str) -> float | None:
    try:
        return float(dict(urllib.parse.parse_qsl(raw_body)).get("total_fee", 0))
    except (TypeError, ValueError):
        return None
