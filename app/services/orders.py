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
    """Cancel any existing pending orders for the same user+plan.

    Called before creating a new order so stale pending orders don't accumulate
    when users tap the pay button multiple times.
    Returns the number of orders cancelled.
    """
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
    """Find pending orders older than ORDER_TIMEOUT_MINUTES and mark them expired.

    Called by the scheduler periodically.
    """
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


async def handle_callback(
    session: AsyncSession,
    provider: BasePaymentProvider,
    data: CallbackData,
) -> Order:
    """Unified callback entry: verify → extract order_id → fulfil."""
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

    prior_status = order.status

    # Atomically claim the order: flip any non-fulfilled status -> paid via a single
    # conditional UPDATE. rowcount == 0 means a concurrent/retried callback already
    # fulfilled it, so we skip (idempotent). This prevents double-fulfilment on
    # gateway retries across SQLite (serialised writers) and Postgres/MySQL (row lock).
    claim = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status != OrderStatus.fulfilled)
        .values(status=OrderStatus.paid)
    )
    if claim.rowcount == 0:
        logger.info("Order %s already fulfilled, idempotent skip", order_id)
        return order

    # Sync the ORM object with the row we just claimed.
    await session.refresh(order)

    # Late payment: the order was timed-out (expired) or superseded (cancelled)
    # before the real paid callback arrived. The customer DID pay, so we still
    # fulfil, but flag it loudly so staff know a "closed" order was revived.
    if prior_status in (OrderStatus.cancelled, OrderStatus.expired):
        logger.warning(
            "Order %s was %s but received a valid paid callback — reviving and fulfilling",
            order_id,
            prior_status.value,
        )
        order.revived = True

    order.raw_callback = json.dumps(
        {
            "body": data.raw_body,
            "signature": data.signature,
        }
    )
    await session.flush()
    await _fulfill(session, order)
    return order


async def _fulfill(
    session: AsyncSession, order: Order, duration_days: int | None = None
) -> None:
    """Create/update subscription, then mark order fulfilled.

    duration_days overrides the plan's default duration when provided.
    """
    plan_stmt = select(Plan).where(Plan.id == order.plan_id)
    plan = (await session.execute(plan_stmt)).scalar_one_or_none()
    if plan is None:
        logger.error("_fulfill: plan %d not found for order %s", order.plan_id, order.id)
        order.status = OrderStatus.fulfilled
        await session.flush()
        return
    days = duration_days if duration_days is not None else plan.duration_days

    # Check existing active subscription – extend if present
    sub_stmt = select(Subscription).where(
        Subscription.user_id == order.user_id,
        Subscription.group_chat_id == plan.chat_id,
        Subscription.status == SubscriptionStatus.active,
    )
    existing = (await session.execute(sub_stmt)).scalars().first()

    if existing and existing.expires_at > utcnow():
        # Still active – extend from current expiry so paid time isn't lost
        existing.expires_at = existing.expires_at + timedelta(days=days)
        existing.plan_id = order.plan_id   # bought a different plan for same chat → show correct name
        existing.order_id = order.id
        existing.last_reminded_at = None  # new cycle — allow reminders again
    elif existing:
        # Expired but not kicked – reactivate from now
        existing.expires_at = utcnow() + timedelta(days=days)
        existing.status = SubscriptionStatus.active
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
