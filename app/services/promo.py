"""Promo campaign helpers: trial invites and paid discounts."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Plan,
    PromoAudience,
    PromoCampaign,
    PromoKind,
    Subscription,
)
from app.utils import utcnow

logger = logging.getLogger(__name__)

# In-memory map: telegram user_id -> active discount promo id (from /start payload).
_user_discount_promo: dict[int, int] = {}

AUDIENCE_LABELS = {
    PromoAudience.all: "全部用户",
    PromoAudience.new: "仅新用户",
    PromoAudience.returning: "仅老用户",
}


def set_user_discount_promo(user_id: int, promo_id: int | None) -> None:
    if promo_id is None:
        _user_discount_promo.pop(user_id, None)
    else:
        _user_discount_promo[user_id] = promo_id


def get_user_discount_promo_id(user_id: int) -> int | None:
    return _user_discount_promo.get(user_id)


def clear_user_discount_promo(user_id: int) -> None:
    _user_discount_promo.pop(user_id, None)


def make_start_payload() -> str:
    return f"promo_{secrets.token_hex(4)}"


def make_invite_link_name(promo_id: int) -> str:
    # Telegram invite link name max length is 32.
    return f"promo_{promo_id}"[:32]


def parse_audience(raw: str | None) -> PromoAudience:
    value = (raw or "all").strip().lower()
    try:
        return PromoAudience(value)
    except ValueError:
        return PromoAudience.all


def campaign_is_usable(promo: PromoCampaign, *, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if not promo.is_active:
        return False
    if promo.link_expire_at and promo.link_expire_at <= now:
        return False
    if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
        return False
    return True


def apply_discount(amount: float, promo: PromoCampaign) -> float:
    """Return discounted amount (never below zero). Percent takes priority."""
    if amount <= 0:
        return amount
    if promo.discount_percent and promo.discount_percent > 0:
        pct = min(max(int(promo.discount_percent), 1), 99)
        return max(round(amount * (100 - pct) / 100, 2), 0)
    if promo.discount_amount and promo.discount_amount > 0:
        return max(round(amount - float(promo.discount_amount), 2), 0)
    return amount


async def user_had_group_subscription(
    session: AsyncSession, user_id: int, group_chat_id: int
) -> bool:
    """True if the user ever had any subscription tied to this group."""
    row = (
        await session.execute(
            select(Subscription.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.group_chat_id == group_chat_id,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def user_matches_audience(
    session: AsyncSession, user_id: int, promo: PromoCampaign
) -> bool:
    """Check whether the user is allowed by promo.audience for the promo's plan group."""
    audience = promo.audience or PromoAudience.all
    if hasattr(audience, "value"):
        audience_val = PromoAudience(audience.value)
    else:
        audience_val = parse_audience(str(audience))

    if audience_val == PromoAudience.all:
        return True

    plan = await session.get(Plan, promo.plan_id)
    if plan is None:
        return False

    had = await user_had_group_subscription(session, user_id, plan.chat_id)
    if audience_val == PromoAudience.new:
        return not had
    if audience_val == PromoAudience.returning:
        return had
    return True


async def get_promo(session: AsyncSession, promo_id: int) -> PromoCampaign | None:
    return await session.get(PromoCampaign, promo_id)


async def find_trial_by_invite_name(
    session: AsyncSession, invite_name: str | None
) -> PromoCampaign | None:
    if not invite_name:
        return None
    promo = (
        await session.execute(
            select(PromoCampaign).where(
                PromoCampaign.kind == PromoKind.trial,
                PromoCampaign.invite_link_name == invite_name,
                PromoCampaign.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if promo is None or not campaign_is_usable(promo):
        return None
    return promo


async def find_discount_by_payload(
    session: AsyncSession, payload: str | None
) -> PromoCampaign | None:
    if not payload:
        return None
    payload = payload.strip()
    if not payload.startswith("promo_"):
        return None
    promo = (
        await session.execute(
            select(PromoCampaign).where(
                PromoCampaign.kind == PromoKind.discount,
                PromoCampaign.start_payload == payload,
                PromoCampaign.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if promo is None or not campaign_is_usable(promo):
        return None
    return promo


async def increment_promo_use(session: AsyncSession, promo: PromoCampaign) -> None:
    promo.used_count = int(promo.used_count or 0) + 1
    if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
        promo.is_active = False
        logger.info("Promo %s reached max uses (%s), deactivated", promo.id, promo.max_uses)
    await session.flush()


async def list_promos(
    session: AsyncSession, *, kind: PromoKind | None = None
) -> list[PromoCampaign]:
    stmt = select(PromoCampaign).order_by(PromoCampaign.id.desc())
    if kind is not None:
        stmt = stmt.where(PromoCampaign.kind == kind)
    return list((await session.execute(stmt)).scalars().all())
