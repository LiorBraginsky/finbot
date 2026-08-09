"""Persistence for finbot.repo.models.Message."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import IncomingMessage
from finbot.repo.models import Message


async def add_if_new(session: AsyncSession, message: IncomingMessage, user_id: int) -> int | None:
    """Insert `message`, or do nothing if its update_id is already stored.

    Returns the new row id, or None when the update was already stored — that
    None is how every later stage knows to skip re-extraction. Does not commit.
    """
    stmt = (
        insert(Message)
        .values(
            telegram_update_id=message.telegram_update_id,
            telegram_message_id=message.telegram_message_id,
            chat_id=message.chat_id,
            user_id=user_id,
            kind=message.kind,
            raw_text=message.raw_text,
            file_id=message.file_id,
        )
        .on_conflict_do_nothing(index_elements=[Message.telegram_update_id])
        .returning(Message.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
