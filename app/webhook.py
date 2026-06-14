"""FastAPI webhook server for external payment callbacks."""

from __future__ import annotations

import json
import logging
import urllib.parse

from fastapi import FastAPI, Request, Response

from app.database import async_session_factory
from app.payments.base import CallbackData, get_provider
from app.services.notify import notify_fulfillment
from app.services.orders import OrderError, handle_callback

logger = logging.getLogger(__name__)
webhook_app = FastAPI(title="TG Paid Community Bot Webhooks")

# --- security headers middleware ---
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


webhook_app.add_middleware(SecurityHeadersMiddleware)

from app.admin_panel import admin_panel_router  # noqa: E402

webhook_app.include_router(admin_panel_router)


@webhook_app.post("/webhook/crypto")
async def crypto_webhook(request: Request):
    body = await request.body()
    raw = body.decode()
    sig = request.headers.get("crypto-pay-api-signature", "")

    try:
        async with async_session_factory() as session:
            provider = get_provider("crypto")
            data = CallbackData(order_id="", raw_body=raw, signature=sig)
            order = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Crypto callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Crypto callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await notify_fulfillment(order.id)
    return Response(status_code=200)


@webhook_app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    raw = body.decode()
    sig = request.headers.get("stripe-signature", "")

    try:
        payload = json.loads(raw)
    except Exception:
        return Response(status_code=400)

    event_type = payload.get("type", "")

    # Handle payment failure / session expiry — mark the order cancelled
    if event_type in ("checkout.session.expired", "payment_intent.payment_failed"):
        await _handle_stripe_failure(payload)
        return Response(status_code=200)

    if event_type != "checkout.session.completed":
        return Response(status_code=200)

    try:
        async with async_session_factory() as session:
            provider = get_provider("stripe")
            data = CallbackData(order_id="", raw_body=raw, signature=sig)
            order = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Stripe callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Stripe callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await notify_fulfillment(order.id)
    return Response(status_code=200)


async def _handle_stripe_failure(payload: dict) -> None:
    """Mark a Stripe order as cancelled and notify the user."""
    try:
        session_obj = payload.get("data", {}).get("object", {})
        order_id = (
            session_obj.get("metadata", {}).get("order_id")
            or session_obj.get("client_reference_id")
        )
        if not order_id:
            return

        from sqlalchemy import select
        from app.models.models import Order, OrderStatus
        from app.bot.dispatcher import bot

        async with async_session_factory() as session:
            order = await session.get(Order, order_id)
            if order and order.status == OrderStatus.pending:
                order.status = OrderStatus.cancelled
                await session.commit()
                try:
                    await bot.send_message(
                        order.user_id,
                        "⚠️ 您的支付未完成或已取消。请发送 /start 重新选择套餐。",
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error("Failed to handle Stripe failure event: %s", e)


@webhook_app.post("/webhook/epay")
async def epay_webhook(request: Request):
    """Epay backend callback — routed to alipay or wechat provider based on order."""
    body = await request.body()
    raw = body.decode()

    order_id_probe = _try_extract_epay_order_id(raw)
    provider_name = await _resolve_provider_from_order(order_id_probe)

    try:
        async with async_session_factory() as session:
            provider = get_provider(provider_name)
            data = CallbackData(order_id="", raw_body=raw)
            order = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Epay callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Epay callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await notify_fulfillment(order.id)
    return Response(status_code=200)


@webhook_app.post("/webhook/hupijiao")
async def hupijiao_webhook(request: Request):
    """HuPiJiao backend callback — routed to alipay or wechat provider based on order."""
    body = await request.body()
    raw = body.decode()

    order_id_probe = _try_extract_hupijiao_order_id(raw)
    provider_name = await _resolve_provider_from_order(order_id_probe)

    try:
        async with async_session_factory() as session:
            provider = get_provider(provider_name)
            data = CallbackData(order_id="", raw_body=raw)
            order = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("HuPiJiao callback rejected: %s", e)
        return Response(content="fail", media_type="text/plain", status_code=400)
    except Exception as e:
        logger.error("HuPiJiao callback unexpected error: %s", e, exc_info=True)
        return Response(content="fail", media_type="text/plain", status_code=500)

    await notify_fulfillment(order.id)
    return Response(content="success", media_type="text/plain")


def _try_extract_epay_order_id(raw: str) -> str:
    try:
        return dict(urllib.parse.parse_qsl(raw)).get("out_trade_no", "")
    except Exception:
        return ""


def _try_extract_hupijiao_order_id(raw: str) -> str:
    try:
        params = dict(urllib.parse.parse_qsl(raw))
        plugins = params.get("plugins", "")
        return plugins.split("|")[0] if "|" in plugins else params.get("trade_order_id", "")
    except Exception:
        return ""


async def _resolve_provider_from_order(order_id_probe: str) -> str:
    """Look up the order to find which user-facing provider (alipay/wechat) it belongs to."""
    if not order_id_probe:
        return "alipay"  # fallback
    async with async_session_factory() as session:
        from sqlalchemy import select
        from app.models.models import Order

        order = (await session.execute(select(Order).where(Order.id == order_id_probe))).scalar_one_or_none()
        if order and order.provider and order.provider.value in ("alipay", "wechat"):
            return order.provider.value
    return "alipay"  # fallback
