"""Track and query the groups/channels the bot belongs to."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.models import BotChat

logger = logging.getLogger(__name__)

# Chat member statuses that mean the bot is still in the chat.
_MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}
_ADMIN_STATUSES = {"administrator", "creator"}


async def record_bot_chat(
    *,
    chat_id: int,
    title: str | None,
    chat_type: str | None,
    username: str | None,
    status: str,
) -> None:
    """Upsert a chat's membership state from a `my_chat_member` update."""
    is_member = status in _MEMBER_STATUSES
    is_admin = status in _ADMIN_STATUSES

    async with async_session_factory() as session:
        chat = await session.get(BotChat, chat_id)
        if chat is None:
            chat = BotChat(chat_id=chat_id)
            session.add(chat)
        chat.title = title
        chat.type = chat_type
        chat.username = username
        chat.is_admin = is_admin
        chat.is_member = is_member
        await session.commit()

    logger.info(
        "Recorded bot chat %s (%s) status=%s admin=%s member=%s",
        chat_id,
        title,
        status,
        is_admin,
        is_member,
    )


async def list_bot_chats(
    session: AsyncSession, *, members_only: bool = True
) -> list[BotChat]:
    """Return known chats, most recently updated first."""
    stmt = select(BotChat)
    if members_only:
        stmt = stmt.where(BotChat.is_member == True)  # noqa: E712
    stmt = stmt.order_by(BotChat.is_admin.desc(), BotChat.updated_at.desc())
    return list((await session.execute(stmt)).scalars().all())
