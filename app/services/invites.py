"""Shared helpers for creating Telegram invite links."""

from __future__ import annotations

from datetime import datetime

from app.bot.dispatcher import bot


async def create_join_request_invite(
    chat_id: int,
    *,
    name: str,
    expire_date: datetime | None = None,
) -> str:
    """Create a join-request invite link and return the URL."""
    kwargs: dict = {
        "chat_id": chat_id,
        "creates_join_request": True,
        "name": name[:32],
    }
    if expire_date is not None:
        kwargs["expire_date"] = expire_date
    invite = await bot.create_chat_invite_link(**kwargs)
    return invite.invite_link


async def revoke_invite_link(chat_id: int, invite_link: str) -> None:
    await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link)
