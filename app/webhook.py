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


class AdminIPMiddleware(BaseHTTPMiddleware):
    """Optional IP allowlist for /admin routes (set ADMIN_PANEL_ALLOWED_IPS)."""

    async def dispatch(self, request, call_next):
        from app.config import settings

        path = request.url.path
        if path.startswith("/admin") and settings.admin_panel_allowed_ips.strip():
            allowed = {
                ip.strip()
                for ip in settings.admin_panel_allowed_ips.split(",")
                if ip.strip()
            }
            client = request.client.host if request.client else ""
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if client not in allowed and forwarded not in allowed:
                return Response(status_code=403, content="Forbidden")
        return await call_next(request)


webhook_app.add_middleware(AdminIPMiddleware)

from app.admin_panel import admin_panel_router  # noqa: E402

webhook_app.include_router(admin_panel_router)


async def _notify_if_new(order, newly: bool) -> None:
    if newly:
        await notify_fulfillment(order.id)


@webhook_app.post("/webhook/crypto")
async def crypto_webhook(request: Request):
    body = await request.body()
    raw = body.decode()
    sig = request.headers.get("crypto-pay-api-signature", "")

    try:
        async with async_session_factory() as session:
            provider = get_provider("crypto")
            data = CallbackData(order_id="", raw_body=raw, signature=sig)
            order, newly = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Crypto callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Crypto callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await _notify_if_new(order, newly)
    return Response(status_code=200)


@webhook_app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    raw = body.decode()
    sig = request.headers.get("stripe-signature", "")

    from app.payments.stripe import construct_stripe_event

    try:
        event = construct_stripe_event(raw, sig)
    except Exception as e:
        logger.warning("Stripe signature verification failed: %s", e)
        return Response(status_code=400)

    event_type = event.get("type", "")

    if event_type in ("checkout.session.expired", "payment_intent.payment_failed"):
        await _handle_stripe_failure(event)
        return Response(status_code=200)

    if event_type == "charge.refunded":
        await _handle_stripe_refund(event)
        return Response(status_code=200)

    if event_type != "checkout.session.completed":
        return Response(status_code=200)

    try:
        async with async_session_factory() as session:
            provider = get_provider("stripe")
            data = CallbackData(order_id="", raw_body=raw, signature=sig)
            order, newly = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Stripe callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Stripe callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await _notify_if_new(order, newly)
    return Response(status_code=200)


async def _handle_stripe_failure(event: dict) -> None:
    """Mark a pending Stripe order as cancelled (verified event only)."""
    try:
        session_obj = event.get("data", {}).get("object", {})
        order_id = (
            session_obj.get("metadata", {}).get("order_id")
            or session_obj.get("client_reference_id")
        )
        if not order_id:
            return

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
                except Exception as e:
                    logger.warning("Stripe failure notify failed user=%d: %s", order.user_id, e)
    except Exception as e:
        logger.error("Failed to handle Stripe failure event: %s", e)


async def _handle_stripe_refund(event: dict) -> None:
    """Revoke subscription when Stripe charge is refunded."""
    from sqlalchemy import select

    from app.models.models import Order, OrderStatus, Subscription, SubscriptionStatus
    from app.services.kick import kick_user_from_chat
    from app.bot.dispatcher import bot

    try:
        charge = event.get("data", {}).get("object", {})
        payment_intent = charge.get("payment_intent")
        if not payment_intent:
            return

        async with async_session_factory() as session:
            orders = (
                await session.execute(
                    select(Order).where(Order.external_id == payment_intent)
                )
            ).scalars().all()
            if not orders:
                meta_order = charge.get("metadata", {}).get("order_id")
                if meta_order:
                    order = await session.get(Order, meta_order)
                    orders = [order] if order else []

            for order in orders:
                if order is None:
                    continue
                sub = (
                    await session.execute(
                        select(Subscription).where(Subscription.order_id == order.id)
                    )
                ).scalar_one_or_none()
                if sub and sub.status == SubscriptionStatus.active:
                    kicked = await kick_user_from_chat(sub.group_chat_id, sub.user_id)
                    sub.status = SubscriptionStatus.kicked if kicked else SubscriptionStatus.expired
                    try:
                        await bot.send_message(
                            order.user_id,
                            "您的付款已退款，会员资格已取消。",
                        )
                    except Exception as e:
                        logger.warning("Refund notify failed: %s", e)
                if order.status == OrderStatus.fulfilled:
                    order.status = OrderStatus.cancelled
            await session.commit()
    except Exception as e:
        logger.error("Stripe refund handler failed: %s", e)


@webhook_app.post("/webhook/epay")
async def epay_webhook(request: Request):
    body = await request.body()
    raw = body.decode()

    order_id_probe = _try_extract_epay_order_id(raw)
    provider_name = await _resolve_provider_from_order(order_id_probe)
    if not provider_name:
        return Response(status_code=400)

    try:
        async with async_session_factory() as session:
            provider = get_provider(provider_name)
            data = CallbackData(order_id="", raw_body=raw)
            order, newly = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("Epay callback rejected: %s", e)
        return Response(status_code=400)
    except Exception as e:
        logger.error("Epay callback unexpected error: %s", e, exc_info=True)
        return Response(status_code=500)

    await _notify_if_new(order, newly)
    return Response(status_code=200)


@webhook_app.post("/webhook/hupijiao")
async def hupijiao_webhook(request: Request):
    body = await request.body()
    raw = body.decode()

    order_id_probe = _try_extract_hupijiao_order_id(raw)
    provider_name = await _resolve_provider_from_order(order_id_probe)
    if not provider_name:
        return Response(content="fail", media_type="text/plain", status_code=400)

    try:
        async with async_session_factory() as session:
            provider = get_provider(provider_name)
            data = CallbackData(order_id="", raw_body=raw)
            order, newly = await handle_callback(session, provider, data)
            await session.commit()
    except OrderError as e:
        logger.warning("HuPiJiao callback rejected: %s", e)
        return Response(content="fail", media_type="text/plain", status_code=400)
    except Exception as e:
        logger.error("HuPiJiao callback unexpected error: %s", e, exc_info=True)
        return Response(content="fail", media_type="text/plain", status_code=500)

    await _notify_if_new(order, newly)
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


async def _resolve_provider_from_order(order_id_probe: str) -> str | None:
    """Look up order to route epay/hupijiao callback to alipay or wechat provider."""
    if not order_id_probe:
        logger.warning("Callback missing order id — rejecting")
        return None
    async with async_session_factory() as session:
        from sqlalchemy import select
        from app.models.models import Order

        order = (
            await session.execute(select(Order).where(Order.id == order_id_probe))
        ).scalar_one_or_none()
        if order and order.provider and order.provider.value in ("alipay", "wechat"):
            return order.provider.value
    logger.warning("Cannot resolve provider for order %s", order_id_probe)
    return None
