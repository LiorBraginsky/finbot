"""Integration tests for the two before-the-call guards on the bank-feed
(`MessageKind.PHOTO`) path (docs/plans/stage-2_5-bank-screenshots.md, Step
2): `MODEL_VISION` unset, and `ImageFetchError` from `fetch_image`. Both are
before any model call, so CLAUDE.md rule 6 requires neither to ever produce
an `extractions` row — proven here the same way voice's own guards are, in
`tests/integration/test_extraction_pipeline.py`: an empty `select(Extraction)`
after the call, and a `FakeLlmClient()` given no scripted responses at all,
which raises `AssertionError` the instant `complete()` is ever called.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.extraction.ports import ImageFetchError
from finbot.core.models import ExtractionStatus, IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories, messages, users
from finbot.repo.models import Extraction, Message
from tests.support.fake_llm import FakeLlmClient

_ANCHOR = date(2026, 8, 24)
_MODELS = ("google/gemini-3.5-flash-lite", "google/gemini-3.6-flash")


async def _claimed_photo_message(session: AsyncSession, *, file_id: str = "photo-1") -> Message:
    """See `test_bank_pipeline._claimed_photo_message`'s own docstring for
    why the row is flipped to `pending` by hand here.
    """
    incoming = IncomingMessage(
        telegram_update_id=hash(file_id) & 0x7FFFFFFF,
        telegram_message_id=1,
        chat_id=-1001111111111,
        telegram_user_id=444444444,
        display_name="Alice",
        kind=MessageKind.PHOTO,
        raw_text=None,
        file_id=file_id,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    await session.commit()
    assert message_id is not None

    await session.execute(
        update(Message).where(Message.id == message_id).values(status=MessageStatus.PENDING)
    )
    await session.commit()

    claimed = await messages.claim_next(session, datetime.now(UTC))
    await session.commit()
    assert claimed is not None
    return claimed


async def _category_ids(session: AsyncSession) -> dict[str, int]:
    category_ids = await categories.by_slug(session)
    await session.commit()
    return category_ids


async def _never_fetch_image() -> str:
    """Never actually called: passed where a test must prove no download was
    even attempted — mirrors `test_extraction_pipeline._never_fetch` for the
    image seam instead of the audio one.
    """
    raise AssertionError("fetch_image was called, but this guard must fire before any download")


async def test_vision_not_configured_marks_skipped_before_any_download_or_model_call(
    db_session: AsyncSession,
) -> None:
    """An empty `models` tuple is what `Settings.vision_model_candidates`
    resolves to when `MODEL_VISION` is unset — `_never_fetch_image` proves
    no download is attempted, and `FakeLlmClient()` (no scripted responses)
    would raise `AssertionError` the instant it was ever called.
    """
    message = await _claimed_photo_message(db_session)
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient()

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=(),
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_never_fetch_image,
        anchor_date=_ANCHOR,
    )

    assert outcome.vision_not_configured is True
    assert outcome.expense_ids == ()
    assert outcome.status == ExtractionStatus.FAILED

    # Nothing reached a model: no extractions row, ever (rule 6).
    assert (await db_session.execute(select(Extraction))).scalars().all() == []

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.SKIPPED
    assert refreshed.last_error == "vision_not_configured"


async def test_image_fetch_error_schedules_retry_with_no_extractions_row(
    db_session: AsyncSession,
) -> None:
    """A download or sniff failure (`ImageFetchError`) happens before any
    model call — unlike an `LlmError`, there is no response to record, so no
    `extractions` row exists, and the message goes back to `pending` rather
    than `failed` (one attempt, well under `max_message_attempts`).
    """
    message = await _claimed_photo_message(db_session)
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient()

    async def _boom() -> str:
        raise ImageFetchError("photo is 25165824 bytes, over the 20971520-byte limit")

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_boom,
        anchor_date=_ANCHOR,
    )

    assert outcome.status == ExtractionStatus.FAILED
    assert outcome.vision_not_configured is False

    # Nothing reached a model here either: the download/sniff failed first.
    assert (await db_session.execute(select(Extraction))).scalars().all() == []

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.PENDING
    assert refreshed.last_error == "photo is 25165824 bytes, over the 20971520-byte limit"
