"""Order lifecycle service – the single source of truth for fulfill."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Order,
    OrderStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.payments.base import BasePaymentProvider, CallbackData
from app.utils import utcnow

logger = logging.getLogger(__name__)

_CLAIMABLE = (
    OrderStatus.pending,
    OrderStatus.expired,
    OrderStatus.cancelled,
)


class OrderError(Exception):
    pass


async def create_order(
    session: AsyncSession,
    user_id: int,
    plan: Plan,
    provider: BasePaymentProvider,
    amount: float,
    currency: str,
) -> Order:
    order = Order(
        user_id=user_id,
        plan_id=plan.id,
        provider=provider.name,  # type: ignore[arg-type]
        amount=amount,
        currency=currency,
        status=OrderStatus.pending,
    )
    session.add(order)
    await session.flush()
    return order


async def cancel_user_pending_orders(
    session: AsyncSession, user_id: int, plan_id: int
) -> int:
    """Cancel any existing pending orders for the same user+plan."""
    stmt = select(Order).where(
        Order.user_id == user_id,
        Order.plan_id == plan_id,
        Order.status == OrderStatus.pending,
    )
    orders = (await session.execute(stmt)).scalars().all()
    for order in orders:
        order.status = OrderStatus.cancelled
    await session.flush()
    return len(orders)


async def expire_stale_orders(session: AsyncSession) -> list[Order]:
    """Find pending orders older than ORDER_TIMEOUT_MINUTES and mark them expired."""
    from app.config import settings

    cutoff = utcnow() - timedelta(minutes=int(settings.order_timeout_minutes))
    stmt = select(Order).where(
        Order.status == OrderStatus.pending,
        Order.created_at < cutoff,
    )
    orders = (await session.execute(stmt)).scalars().all()
    for order in orders:
        order.status = OrderStatus.expired
    await session.flush()
    return list(orders)


async def _subscription_for_order(
    session: AsyncSession, order_id: str
) -> Subscription | None:
    return (
        await session.execute(
            select(Subscription).where(Subscription.order_id == order_id)
        )
    ).scalar_one_or_none()


async def handle_callback(
    session: AsyncSession,
    provider: BasePaymentProvider,
    data: CallbackData,
) -> tuple[Order, bool]:
    """Verify payment callback and fulfil order.

    Returns (order, newly_fulfilled). newly_fulfilled is False when the
    callback was a duplicate or the order was already processed.
    """
    valid = await provider.verify_callback(data)
    if not valid:
        raise OrderError("Callback verification failed")

    order_id = await provider.extract_order_id(data)
    if not order_id:
        raise OrderError("Cannot extract order_id from callback")

    order = (
        await session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise OrderError(f"Order not found: {order_id}")

    order_provider = order.provider.value if hasattr(order.provider, "value") else str(order.provider)
    if order_provider != provider.name:
        raise OrderError(
            f"Provider mismatch: order={order_provider}, callback={provider.name}"
        )

    if order.status == OrderStatus.fulfilled:
        return order, False

    if await _subscription_for_order(session, order.id):
        if order.status != OrderStatus.fulfilled:
            order.status = OrderStatus.fulfilled
            await session.flush()
        return order, False

    if order.status == OrderStatus.paid:
        ext_id = await provider.extract_external_id(data)
        if ext_id:
            order.external_id = ext_id
        await _fulfill(session, order)
        return order, True

    if not await provider.verify_payment_amount(data, order):
        raise OrderError("Payment amount/currency mismatch")

    prior_status = order.status

    claim = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status.in_(_CLAIMABLE))
        .values(status=OrderStatus.paid)
    )
    if claim.rowcount == 0:
        await session.refresh(order)
        if order.status == OrderStatus.fulfilled:
            return order, False
        if order.status == OrderStatus.paid:
            await _fulfill(session, order)
            return order, True
        raise OrderError(f"Order {order_id} not in claimable state: {order.status.value}")

    await session.refresh(order)

    if prior_status in (OrderStatus.cancelled, OrderStatus.expired):
        logger.warning(
            "Order %s was %s but received a valid paid callback — reviving",
            order_id,
            prior_status.value,
        )
        order.revived = True

    order.raw_callback = json.dumps(
        {"body": data.raw_body, "signature": data.signature}
    )
    ext_id = await provider.extract_external_id(data)
    if ext_id:
        order.external_id = ext_id
    await session.flush()
    await _fulfill(session, order)
    return order, True


async def _fulfill(
    session: AsyncSession, order: Order, duration_days: int | None = None
) -> None:
    """Create/update subscription, then mark order fulfilled."""
    if order.status == OrderStatus.fulfilled:
        return

    if await _subscription_for_order(session, order.id):
        order.status = OrderStatus.fulfilled
        await session.flush()
        return

    plan = (
        await session.execute(select(Plan).where(Plan.id == order.plan_id))
    ).scalar_one_or_none()
    if plan is None:
        raise OrderError(f"Plan {order.plan_id} not found for order {order.id}")

    days = duration_days if duration_days is not None else plan.duration_days

    sub_stmt = select(Subscription).where(
        Subscription.user_id == order.user_id,
        Subscription.group_chat_id == plan.chat_id,
        Subscription.status == SubscriptionStatus.active,
    )
    existing = (await session.execute(sub_stmt)).scalars().first()

    if existing and existing.expires_at > utcnow():
        existing.expires_at = existing.expires_at + timedelta(days=days)
        existing.plan_id = order.plan_id
        existing.order_id = order.id
        existing.last_reminded_at = None
    elif existing:
        existing.expires_at = utcnow() + timedelta(days=days)
        existing.status = SubscriptionStatus.active
        existing.plan_id = order.plan_id
        existing.order_id = order.id
        existing.last_reminded_at = None
    else:
        sub = Subscription(
            user_id=order.user_id,
            plan_id=plan.id,
            order_id=order.id,
            group_chat_id=plan.chat_id,
            expires_at=utcnow() + timedelta(days=days),
            status=SubscriptionStatus.active,
        )
        session.add(sub)

    order.status = OrderStatus.fulfilled
    await session.flush()
