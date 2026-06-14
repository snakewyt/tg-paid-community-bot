"""Admin grant flow – single entry for gifting subscriptions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Order, OrderStatus, PaymentProvider, Plan
from app.services.orders import _fulfill


async def grant_subscription(
    session: AsyncSession,
    user_id: int,
    plan_id: int,
    days: int,
) -> Order:
    """Create a zero-amount grant order and fulfil via the standard path."""
    if days < 1:
        raise ValueError("days must be >= 1")

    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise ValueError("Plan not found")
    if not plan.is_active:
        raise ValueError("Plan is inactive")

    order = Order(
        user_id=user_id,
        plan_id=plan_id,
        provider=PaymentProvider.stars,
        amount=0,
        currency="GRANT",
        status=OrderStatus.pending,
    )
    session.add(order)
    await session.flush()

    try:
        await _fulfill(session, order, duration_days=days)
    except OrderError:
        raise
    return order
