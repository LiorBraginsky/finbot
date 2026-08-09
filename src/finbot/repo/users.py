"""Persistence for finbot.repo.models.User."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.repo.models import User


async def get_or_create(session: AsyncSession, telegram_user_id: int, display_name: str) -> int:
    """Return the id of the user row for `telegram_user_id`, creating or refreshing it.

    Uses DO UPDATE rather than DO NOTHING: with DO NOTHING a concurrent second
    insert returns no row, leaving the caller without an id. This also keeps
    display_name current for free. Does not commit.
    """
    stmt = (
        insert(User)
        .values(telegram_user_id=telegram_user_id, display_name=display_name)
        .on_conflict_do_update(
            index_elements=[User.telegram_user_id],
            set_={"display_name": display_name},
        )
        .returning(User.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()
