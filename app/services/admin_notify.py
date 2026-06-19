"""Notify configured admins via Telegram DM."""

from __future__ import annotations

import logging

from aiogram.enums import ParseMode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.dispatcher import bot
from app.config import settings
from app.database import async_session_factory
from app.models.models import User
from app.utils import parse_admin_usernames

logger = logging.getLogger(__name__)


async def _admin_notify_ids(session: AsyncSession) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    for admin_id in settings.admin_ids:
        if admin_id not in seen:
            ids.append(admin_id)
            seen.add(admin_id)

    names = parse_admin_usernames(settings.admin_usernames)
    if names:
        rows = (
            await session.execute(
                select(User.id).where(func.lower(User.username).in_(names))
            )
        ).scalars().all()
        for uid in rows:
            if uid not in seen:
                ids.append(uid)
                seen.add(uid)

    return ids


async def notify_admins(text: str) -> None:
    """Send a message to all configured admins. Failures are logged, not raised."""
    async with async_session_factory() as session:
        admin_ids = await _admin_notify_ids(session)

    if not admin_ids:
        logger.warning("notify_admins: no admin IDs configured")
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning("Failed to notify admin %d: %s", admin_id, e)
