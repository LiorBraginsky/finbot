"""Integration tests for finbot.repo.users / finbot.repo.messages against a real
Postgres (see tests/conftest.py). No skipif on Docker availability.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import IncomingMessage, MessageKind, MessageStatus
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


async def test_add_if_new_sets_pending_for_plain_text(db_session: AsyncSession) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.PENDING


async def test_add_if_new_sets_skipped_for_a_command(db_session: AsyncSession) -> None:
    message = _message(raw_text="/ping")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.SKIPPED


async def test_add_if_new_sets_pending_for_voice(db_session: AsyncSession) -> None:
    """From Stage 2 (docs/roadmap.md): voice becomes a PENDING row exactly
    like plain text, so the drain loop claims and processes it — unlike
    Stage 0/1, where voice and photo were both SKIPPED.
    """
    message = _message(
        kind=MessageKind.VOICE, raw_text=None, file_id="voice-file-id", duration_seconds=5
    )
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.PENDING
    assert row.duration_seconds == 5


async def test_add_if_new_sets_skipped_for_a_photo(db_session: AsyncSession) -> None:
    """Photo stays SKIPPED until Stage 4 gives it a pipeline of its own."""
    message = _message(kind=MessageKind.PHOTO, raw_text=None, file_id="photo-file-id")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)

    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.SKIPPED


async def test_claim_next_returns_the_oldest_pending_row_and_increments_attempts(
    db_session: AsyncSession,
) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    claimed = await messages.claim_next(db_session, datetime.now(UTC))
    await db_session.commit()

    assert claimed is not None
    assert claimed.id == row_id
    assert claimed.status == MessageStatus.PROCESSING
    assert claimed.attempts == 1


async def test_claim_next_returns_none_when_nothing_is_pending(db_session: AsyncSession) -> None:
    claimed = await messages.claim_next(db_session, datetime.now(UTC))
    assert claimed is None


async def test_claim_next_ignores_rows_scheduled_in_the_future(db_session: AsyncSession) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None
    await db_session.execute(
        text("UPDATE messages SET next_attempt_at = :future WHERE id = :id").bindparams(
            future=datetime.now(UTC) + timedelta(hours=1), id=row_id
        )
    )
    await db_session.commit()

    claimed = await messages.claim_next(db_session, datetime.now(UTC))

    assert claimed is None


async def test_mark_done_sets_status_done(db_session: AsyncSession) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    await messages.mark_done(db_session, row_id)
    await db_session.commit()
    db_session.expire_all()  # force a fresh read past the identity map

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.DONE


async def test_set_transcript_writes_into_raw_text(db_session: AsyncSession) -> None:
    """Stage 2 (docs/roadmap.md): a voice message's transcript lands in the
    same column a text message's own words already occupy — what makes a
    voice message searchable and, eventually, Stage 3's production eval
    input.
    """
    message = _message(
        kind=MessageKind.VOICE, raw_text=None, file_id="voice-file-id", duration_seconds=5
    )
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    await messages.set_transcript(db_session, row_id, "хліб пʼятдесят")
    await db_session.commit()
    db_session.expire_all()

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.raw_text == "хліб пʼятдесят"


async def test_mark_skipped_sets_status_skipped(db_session: AsyncSession) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None

    await messages.mark_skipped(db_session, row_id)
    await db_session.commit()
    db_session.expire_all()  # force a fresh read past the identity map

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.SKIPPED


async def test_schedule_retry_reschedules_pending_when_under_max_attempts(
    db_session: AsyncSession,
) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None
    await messages.claim_next(db_session, datetime.now(UTC))  # attempts -> 1
    await db_session.commit()

    before = datetime.now(UTC)
    await messages.schedule_retry(
        db_session, row_id, error="boom", delay_seconds=30, max_attempts=5
    )
    await db_session.commit()
    db_session.expire_all()  # force a fresh read past the identity map

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.PENDING
    assert row.last_error == "boom"
    assert row.next_attempt_at > before


async def test_schedule_retry_marks_failed_once_max_attempts_reached(
    db_session: AsyncSession,
) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None
    await messages.claim_next(db_session, datetime.now(UTC))  # attempts -> 1
    await db_session.commit()

    await messages.schedule_retry(
        db_session, row_id, error="boom", delay_seconds=30, max_attempts=1
    )
    await db_session.commit()
    db_session.expire_all()  # force a fresh read past the identity map

    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.FAILED
    assert row.last_error == "boom"


async def test_reset_processing_resets_processing_rows_and_returns_the_count(
    db_session: AsyncSession,
) -> None:
    message = _message(raw_text="хліб 50")
    user_id = await users.get_or_create(db_session, message.telegram_user_id, message.display_name)
    row_id = await messages.add_if_new(db_session, message, user_id)
    await db_session.commit()
    assert row_id is not None
    await messages.claim_next(db_session, datetime.now(UTC))
    await db_session.commit()

    reset_count = await messages.reset_processing(db_session)
    await db_session.commit()
    db_session.expire_all()  # force a fresh read past the identity map

    assert reset_count == 1
    row = await db_session.get(Message, row_id)
    assert row is not None
    assert row.status == MessageStatus.PENDING
