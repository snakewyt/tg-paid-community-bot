from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import BigInteger, Enum, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    fulfilled = "fulfilled"
    expired = "expired"
    cancelled = "cancelled"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    kicked = "kicked"


class PaymentProvider(str, enum.Enum):
    stars = "stars"
    crypto = "crypto"
    stripe = "stripe"
    alipay = "alipay"    # backend: epay or hupijiao
    wechat = "wechat"    # backend: epay or hupijiao


class PromoKind(str, enum.Enum):
    trial = "trial"
    discount = "discount"


class PromoAudience(str, enum.Enum):
    """Who can use the promo."""

    all = "all"  # anyone
    new = "new"  # never had a subscription for this plan's group
    returning = "returning"  # had a subscription for this plan's group before


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(128))
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    language_code: Mapped[Optional[str]] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    duration_days: Mapped[int] = mapped_column()
    chat_id: Mapped[int] = mapped_column()
    price_stars: Mapped[int] = mapped_column(default=0)
    price_crypto: Mapped[float] = mapped_column(default=0.0)
    price_stripe: Mapped[int] = mapped_column(default=0)
    price_alipay: Mapped[float] = mapped_column(default=0.0)   # CNY
    price_wechat: Mapped[float] = mapped_column(default=0.0)    # CNY
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: uuid4().hex
    )
    user_id: Mapped[int] = mapped_column()
    plan_id: Mapped[int] = mapped_column()
    provider: Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider))
    amount: Mapped[float] = mapped_column()
    currency: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.pending
    )
    # True when a late paid callback fulfilled an already cancelled/expired order.
    revived: Mapped[bool] = mapped_column(default=False)
    payment_message_id: Mapped[Optional[int]] = mapped_column(default=None)
    promo_id: Mapped[Optional[int]] = mapped_column(default=None)
    raw_callback: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class BotChat(Base):
    """A group/channel the bot is currently (or was) a member of.

    Populated from `my_chat_member` updates so the admin panel can offer
    the chats the bot has joined when creating a plan.
    """

    __tablename__ = "bot_chats"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    title: Mapped[Optional[str]] = mapped_column(String(255))
    # "group" | "supergroup" | "channel"
    type: Mapped[Optional[str]] = mapped_column(String(32))
    username: Mapped[Optional[str]] = mapped_column(String(128))
    # Whether the bot currently has admin rights in the chat.
    is_admin: Mapped[bool] = mapped_column(default=False)
    # False once the bot is removed/kicked/left.
    is_member: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class PromoCampaign(Base):
    """Trial invite link or paid discount deep-link campaign."""

    __tablename__ = "promo_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[PromoKind] = mapped_column(Enum(PromoKind))
    plan_id: Mapped[int] = mapped_column()
    # all | new | returning — who may redeem this promo
    audience: Mapped[PromoAudience] = mapped_column(
        Enum(PromoAudience), default=PromoAudience.all
    )
    grant_days: Mapped[int] = mapped_column(default=0)
    discount_percent: Mapped[int] = mapped_column(default=0)
    discount_amount: Mapped[float] = mapped_column(default=0.0)
    max_uses: Mapped[int] = mapped_column(default=0)
    used_count: Mapped[int] = mapped_column(default=0)
    invite_link: Mapped[Optional[str]] = mapped_column(String(512))
    invite_link_name: Mapped[Optional[str]] = mapped_column(String(64))
    start_payload: Mapped[Optional[str]] = mapped_column(String(64))
    # Human-readable promo code for in-bot redemption (uppercase A-Z0-9).
    code: Mapped[Optional[str]] = mapped_column(String(16), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    link_expire_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column()
    plan_id: Mapped[int] = mapped_column()
    order_id: Mapped[str] = mapped_column(String(36))
    group_chat_id: Mapped[int] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.active
    )
    # Timestamp of the last expiry reminder sent; reset to NULL on renewal so
    # the next cycle's reminders fire again. Used to dedupe reminders.
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
