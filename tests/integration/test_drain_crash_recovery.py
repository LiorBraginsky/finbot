"""Integration test for CRITICAL 3 of the Stage 1 review: a bug in
`_process_claimed` must not leave the claimed row stuck in `processing`
forever. Real Postgres, a fake LLM client, no Telegram socket (mirrors
`tests/integration/test_confirmation_flow.py`'s harness, one layer up).
"""

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import finbot.adapters.telegram.runner as runner_module
from finbot.adapters.telegram.render import ConfirmationLine
from finbot.adapters.telegram.runner import drain_loop
from finbot.config import Settings
from finbot.core.extraction.ports import LlmClient
from finbot.core.models import IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories as categories_repo
from finbot.repo import messages, users
from finbot.repo.engine import create_sessionmaker
from finbot.repo.models import Expense, Message
from tests.support.fake_llm import FakeLlmClient
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, CHAT_ID

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"


def _settings(postgres_url: str) -> Settings:
    return Settings(
        telegram_bot_token="42:TESTTOKEN",
        telegram_allowed_user_ids=str(ALLOWED_USER_ID),
        telegram_chat_id=CHAT_ID,
        database_url=postgres_url,
        openrouter_api_key="sk-or-fake-not-a-real-key",
        model_text="google/gemini-3.5-flash-lite",
    )


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


async def _seed_pending_message(session: AsyncSession, raw_text: str) -> int:
    incoming = IncomingMessage(
        telegram_update_id=abs(hash(raw_text)) % 1_000_000_000,
        telegram_message_id=1,
        chat_id=CHAT_ID,
        telegram_user_id=ALLOWED_USER_ID,
        display_name="Alice",
        kind=MessageKind.TEXT,
        raw_text=raw_text,
        file_id=None,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    assert message_id is not None
    await session.commit()
    return message_id


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


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def test_a_bug_in_process_claimed_releases_the_row_and_a_later_tick_recovers(
    bot: Bot, db_session: AsyncSession, postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    message_id = await _seed_pending_message(db_session, "хліб 50")
    settings = _settings(postgres_url)
    sessionmaker = create_sessionmaker(postgres_url)

    # A bug, not a scripted LLM failure: `_process_claimed`'s very first
    # call raises, before `extract_and_store` — and therefore before any
    # model call — ever runs. `llm` is scripted with exactly one response,
    # the one the second, successful tick will consume.
    original_by_slug = categories_repo.by_slug
    calls = {"n": 0}

    async def flaky_by_slug(session: AsyncSession) -> dict[str, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom: category lookup exploded")
        return await original_by_slug(session)

    monkeypatch.setattr(categories_repo, "by_slug", flaky_by_slug)

    llm = FakeLlmClient(_load_fixture("no_cost"))

    await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)

    db_session.expire_all()
    released = await db_session.get(Message, message_id)
    assert released is not None
    # Left processing, not stuck there: claim_next's own commit put it at
    # 'processing', and without the release under test it would still read
    # that here.
    assert released.status == MessageStatus.PENDING
    assert released.attempts == 1
    assert released.next_attempt_at > datetime.now(UTC)
    assert released.last_error is not None
    assert "boom" in released.last_error
    # The bug fired before extract_and_store ever ran: no model call, no
    # extractions row, nothing billed for a round that never reached the LLM.
    assert calls["n"] == 1
    assert llm.requests == []

    # Simulate the backoff having elapsed rather than sleeping ~30s for
    # real: `next_attempt_at <= now` is the only thing standing between this
    # row and a second claim.
    await db_session.execute(
        update(Message).where(Message.id == message_id).values(next_attempt_at=datetime.now(UTC))
    )
    await db_session.commit()

    await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)

    db_session.expire_all()
    recovered = await db_session.get(Message, message_id)
    assert recovered is not None
    assert recovered.status == MessageStatus.DONE
    assert calls["n"] == 2  # the second tick used the now-fixed by_slug

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 1

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1


async def test_a_late_crash_after_done_does_not_resurrect_the_message(
    bot: Bot, db_session: AsyncSession, postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the guard `_release_crashed_claim` needed that the original
    Critical 3 finding did not name: `extract_and_store` commits
    `messages.status = 'done'` (and the `expenses` rows) *before*
    `_process_claimed` ever renders the reply, so a crash in rendering —
    after the write, only the reply lost, per the module docstring — must
    leave the row alone. Without the `current.status != PROCESSING` guard,
    the crash handler would call `schedule_retry` unconditionally, flip this
    already-`done` row back to `pending`, and a later tick would re-claim
    it: a second billed model call and a second, duplicate set of
    `expenses`.
    """
    message_id = await _seed_pending_message(db_session, "хліб 50, таксі 200")
    settings = _settings(postgres_url)
    sessionmaker = create_sessionmaker(postgres_url)
    llm = FakeLlmClient(_load_fixture("ok_two_items"))

    def boom_render_confirmation(lines: Sequence[ConfirmationLine], *, today: date) -> str:
        raise RuntimeError("boom: rendering exploded after the write")

    # runner.py binds render_confirmation into its own module namespace via
    # `from ... import render_confirmation`, so the module-under-test is
    # what must be patched — patching `finbot.adapters.telegram.render`
    # itself would leave runner's already-bound name untouched.
    monkeypatch.setattr(runner_module, "render_confirmation", boom_render_confirmation)

    await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)

    db_session.expire_all()
    message = await db_session.get(Message, message_id)
    assert message is not None
    assert message.status == MessageStatus.DONE
    assert message.attempts == 1

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 2
    assert len(llm.requests) == 1

    # A second tick must not reclaim this message: claim_next only claims
    # 'pending' rows, and this one must still be 'done'. Reprocessing it now
    # would double-bill the model and duplicate every expense row.
    await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)

    db_session.expire_all()
    message_after_second_tick = await db_session.get(Message, message_id)
    assert message_after_second_tick is not None
    assert message_after_second_tick.status == MessageStatus.DONE
    assert message_after_second_tick.attempts == 1  # not reclaimed
    assert len(llm.requests) == 1  # no second LlmRequest issued

    expense_rows_after_second_tick = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows_after_second_tick) == 2  # not duplicated
