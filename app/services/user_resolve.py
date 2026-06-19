"""Resolve Telegram user ID or @username to a numeric user id."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User

logger = logging.getLogger(__name__)


def normalize_telegram_username(raw: str) -> str:
    return raw.strip().lstrip("@").lower()


def looks_like_telegram_user_id(raw: str) -> bool:
    return raw.strip().isdigit()


async def resolve_telegram_user_id(session: AsyncSession, raw: str) -> int:
    """Resolve a numeric ID or @username. Raises ValueError if not found."""
    text = raw.strip()
    if not text:
        raise ValueError("用户标识不能为空")

    if looks_like_telegram_user_id(text):
        return int(text)

    username = normalize_telegram_username(text)
    if not username:
        raise ValueError("无效的用户名")

    uid = (
        await session.execute(
            select(User.id).where(func.lower(User.username) == username)
        )
    ).scalar_one_or_none()
    if uid is not None:
        return uid

    from app.bot.dispatcher import bot

    try:
        chat = await bot.get_chat(f"@{username}")
    except TelegramBadRequest as e:
        logger.warning("resolve user @%s failed: %s", username, e)
        raise ValueError(
            f"无法解析用户 @{username}，请确认用户名正确且对方已与机器人对话"
        ) from e

    await _upsert_user_from_chat(session, chat.id, chat.username, chat.first_name, chat.last_name)
    return chat.id


async def find_user_ids_for_search(session: AsyncSession, raw: str) -> list[int]:
    """Match active-member search by numeric ID, exact @username, or partial username."""
    text = raw.strip()
    if not text:
        return []

    if looks_like_telegram_user_id(text):
        return [int(text)]

    term = normalize_telegram_username(text)
    exact = (
        await session.execute(
            select(User.id).where(func.lower(User.username) == term)
        )
    ).scalars().all()
    if exact:
        return list(exact)

    partial = (
        await session.execute(
            select(User.id).where(func.lower(User.username).contains(term))
        )
    ).scalars().all()
    if partial:
        return list(partial)

    try:
        return [await resolve_telegram_user_id(session, text)]
    except ValueError:
        return []


async def _upsert_user_from_chat(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        session.add(
            User(
                id=user_id,
                username=username,
                first_name=first_name or "",
                last_name=last_name,
            )
        )
    elif username and user.username != username:
        user.username = username
    await session.flush()


def format_user_ref(user_id: int, username: str | None) -> str:
    if username:
        return f"{user_id} (@{username})"
    return str(user_id)
