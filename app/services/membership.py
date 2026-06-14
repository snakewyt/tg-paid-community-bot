"""Membership service: invite links, kick, reminders."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Subscription, SubscriptionStatus
from app.utils import utcnow

logger = logging.getLogger(__name__)


async def get_active_subscriptions(
    session: AsyncSession, user_id: int
) -> list[Subscription]:
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.status == SubscriptionStatus.active,
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def has_active_subscription(
    session: AsyncSession, user_id: int, chat_id: int
) -> bool:
    """Whether the user holds an unexpired subscription for this group/channel."""
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.group_chat_id == chat_id,
        Subscription.status == SubscriptionStatus.active,
        Subscription.expires_at > utcnow(),
    )
    result = await session.execute(stmt)
    return result.scalars().first() is not None


async def find_expired(session: AsyncSession) -> list[Subscription]:
    """Find active subscriptions that have passed their expiry."""
    stmt = select(Subscription).where(
        Subscription.status == SubscriptionStatus.active,
        Subscription.expires_at <= utcnow(),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_expired(session: AsyncSession, sub: Subscription) -> None:
    sub.status = SubscriptionStatus.expired
    await session.flush()


async def mark_kicked(session: AsyncSession, sub: Subscription) -> None:
    sub.status = SubscriptionStatus.kicked
    await session.flush()
