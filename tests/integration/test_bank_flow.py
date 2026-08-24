"""Integration tests for the bank-feed (`MessageKind.PHOTO`) reply, end to
end: the real `Dispatcher` persists the photo, then the real `drain_loop`
claims and answers it — real Postgres, a fake LLM client and a fake Telegram
transport, no socket (mirrors `tests/integration/test_confirmation_flow.py`'s
harness, one layer up, the same way that file mirrors
`test_extraction_pipeline.py`).

This is the regression test for Reality check #1
(docs/plans/stage-2_5-bank-screenshots.md): flipping `repo.messages.
_initial_status`'s PHOTO branch to PENDING and removing `handlers.py`'s
`@router.message(F.photo)` had to ship as one change, or a screenshot would
get two contradictory replies — one inline, fast-lane, and one later from
the drain. `assert cast(FakeSession, bot.session).requests == []`
immediately after `feed_raw_update` is where that regression would surface.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.main import build_dispatcher
from finbot.adapters.telegram.render import VISION_NOT_CONFIGURED_REPLY
from finbot.adapters.telegram.runner import drain_loop
from finbot.config import Settings
from finbot.core.extraction.ports import LlmClient
from finbot.core.models import MessageKind, MessageStatus
from finbot.repo.engine import json_serializer
from finbot.repo.models import Message
from tests.support.fake_llm import FakeLlmClient
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, CHAT_ID, photo_update

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_PHOTO_FILE_ID = "bank-photo-1"
# Real JPEG magic bytes: this flow goes through the real
# `adapters.telegram.images.fetch_as_data_url`, unlike
# `tests/integration/test_bank_pipeline.py`'s stubbed `fetch_image`, so
# `sniff_mime` must actually recognise what `FakeSession` hands back.
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake-bank-screenshot-bytes"


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


def _settings(postgres_url: str, *, model_vision: str = "google/gemini-3.5-flash-lite") -> Settings:
    return Settings(
        telegram_bot_token="42:TESTTOKEN",
        telegram_allowed_user_ids=str(ALLOWED_USER_ID),
        telegram_chat_id=CHAT_ID,
        database_url=postgres_url,
        openrouter_api_key="sk-or-fake-not-a-real-key",
        model_text="google/gemini-3.5-flash-lite",
        model_vision=model_vision,
    )


@pytest_asyncio.fixture
async def dispatcher(postgres_url: str) -> AsyncIterator[Dispatcher]:
    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))
    finally:
        async with sessionmaker() as session:
            # DELETE, not TRUNCATE, for users — see
            # tests/integration/test_telegram_flow.py's own `dispatcher`
            # fixture for why TRUNCATE ... CASCADE here would silently wipe
            # the migration-seeded `categories` table too.
            await session.execute(text("TRUNCATE messages RESTART IDENTITY CASCADE"))
            await session.execute(text("DELETE FROM users"))
            await session.commit()
        await engine.dispose()


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession(voice_files={_PHOTO_FILE_ID: _JPEG_BYTES}))


async def _run_one_drain_tick(
    *,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LlmClient,
    settings: Settings,
) -> None:
    stop = asyncio.Event()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            drain_loop(
                bot=bot,
                sessionmaker=sessionmaker,
                llm=llm,
                settings=settings,
                stop=stop,
                idle_seconds=5.0,
            ),
            timeout=1.0,
        )


async def test_photo_is_pending_with_no_inline_reply_then_the_drain_sends_note_then_confirmation(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession, postgres_url: str
) -> None:
    update = photo_update(update_id=9001, file_id=_PHOTO_FILE_ID)

    await dispatcher.feed_raw_update(bot, update)

    # No reply at all from the fast lane — see this module's own docstring.
    assert cast(FakeSession, bot.session).requests == []

    row = (await db_session.execute(select(Message))).scalar_one()
    message_id = row.id
    assert row.kind == MessageKind.PHOTO
    assert row.status == MessageStatus.PENDING

    settings = _settings(postgres_url)
    anchor = row.created_at.astimezone(settings.tz).date()

    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    llm = FakeLlmClient(_load_fixture("bank_feed_ok"))

    try:
        await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)
    finally:
        await engine.dispose()

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 2
    note, confirmation = sent

    # The note: no keyboard, names the anchor date and every non-zero
    # counter this fixture produces (1 written; 1 savings, 1 own transfer
    # skipped) — bank_feed_ok.json's own three rows.
    assert note.text is not None
    assert note.reply_markup is None
    assert f"{anchor:%d.%m}" in note.text
    assert "Записав: 1 (нижче)." in note.text
    assert "скарбничка 1" in note.text
    assert "переказ собі 1" in note.text

    # The confirmation: the numbered row, and a keyboard whose last row is
    # the delete-all row (Approach D2).
    assert confirmation.text is not None
    assert "Silpo" in confirmation.text
    assert confirmation.reply_markup is not None
    last_row = confirmation.reply_markup.inline_keyboard[-1]
    assert len(last_row) == 1
    assert last_row[0].text == "🗑 Видалити все"

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE
    # No transcript for a photo (ADR-0009): the rows already live in
    # extractions.raw_response.
    assert refreshed.raw_text is None


async def test_vision_not_configured_photo_gets_the_configured_reply_from_the_drain(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession, postgres_url: str
) -> None:
    """`MODEL_VISION` unset end to end through `runner.py` — Step 2 already
    proves the pipeline-level guard (`tests/integration/test_bank_guards.
    py`); this proves `_process_claimed` actually wires
    `outcome.vision_not_configured` to `VISION_NOT_CONFIGURED_REPLY`, the one
    branch Step 3 adds to that function.
    """
    update = photo_update(update_id=9002, file_id=_PHOTO_FILE_ID)
    await dispatcher.feed_raw_update(bot, update)
    assert cast(FakeSession, bot.session).requests == []

    settings = _settings(postgres_url, model_vision="")
    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    llm = FakeLlmClient()  # any call at all is a bug: MODEL_VISION is unset.

    try:
        await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)
    finally:
        await engine.dispose()

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    assert sent[0].text == VISION_NOT_CONFIGURED_REPLY

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.status == MessageStatus.SKIPPED
    assert row.last_error == "vision_not_configured"
