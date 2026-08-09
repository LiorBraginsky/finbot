"""Integration tests for finbot.repo.users / finbot.repo.messages against a real
Postgres (see tests/conftest.py). No skipif on Docker availability.
"""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import messages, users
from finbot.repo.models import Message, User


def _message(**overrides: object) -> IncomingMessage:
    base: dict[str, object] = {
        "telegram_update_id": 1001,
        "telegram_message_id": 1,
        "chat_id": -1001111111111,
        "telegram_user_id": 111111111,
        "display_name": "Alice",
        "kind": MessageKind.TEXT,
        "raw_text": "bread 50",
        "file_id": None,
    }
    base.update(overrides)
    return IncomingMessage.model_validate(base)


async def test_add_if_new_returns_id_then_none_for_same_update_id(
    db_session: AsyncSession,
) -> None:
    message = _message()
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    first_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert first_id is not None

    second_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert second_id is None

    count = await db_session.scalar(select(func.count()).select_from(Message))
    assert count == 1


async def test_get_or_create_is_idempotent_and_refreshes_display_name(
    db_session: AsyncSession,
) -> None:
    telegram_user_id = 222222222

    first_id = await users.get_or_create(db_session, telegram_user_id, "Old Name")
    await db_session.commit()

    second_id = await users.get_or_create(db_session, telegram_user_id, "New Name")
    await db_session.commit()

    assert first_id == second_id

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1

    display_name = await db_session.scalar(select(User.display_name).where(User.id == first_id))
    assert display_name == "New Name"


async def test_kind_round_trips_as_lowercase_value(db_session: AsyncSession) -> None:
    message = _message(kind=MessageKind.VOICE, raw_text=None, file_id="voice-file-id")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    # Raw SQL, deliberately: the ORM column would decode the stored VARCHAR back
    # into a MessageKind member before we could see it. This asserts what is
    # actually stored on disk.
    raw_kind = await db_session.scalar(
        text("SELECT kind FROM messages WHERE id = :id").bindparams(id=row_id)
    )
    assert raw_kind == "voice"
