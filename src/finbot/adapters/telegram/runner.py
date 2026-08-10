"""The inbox drain (ADR-0013): claims one `pending` message at a time and
turns it into expenses and a reply. Runs alongside `polling.run_polling` in
one `asyncio.TaskGroup` (see main.py) — the two are independent: a slow or
failing model never blocks Telegram's own queue from being drained, and vice
versa.

Order within one claimed message is ADR-0007's, literally: **write, then
reply.** `extract_and_store` commits the `expenses`/`extractions` rows
before this module ever calls Telegram, so a crash after the write loses
nothing; a crash before `bot_message_id` is stamped loses only provenance,
because the buttons carry `expense_id` and siblings are grouped by
`message_id`, never by `bot_message_id`.
"""

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finbot.adapters.telegram.keyboards import confirmation_keyboard
from finbot.adapters.telegram.render import (
    NO_EXPENSE_REPLY,
    PROCESSING_FAILED_REPLY,
    ConfirmationLine,
    render_confirmation,
)
from finbot.config import Settings
from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.extraction.ports import LlmClient
from finbot.core.extraction.schema import ExpenseDraft
from finbot.core.models import ExtractionStatus, MessageStatus
from finbot.repo import categories, expenses, messages
from finbot.repo.models import Message

logger = logging.getLogger(__name__)


def _confirmation_lines(
    outcome_expense_ids: Sequence[int], outcome_drafts: Sequence[ExpenseDraft], *, today: date
) -> list[ConfirmationLine]:
    """Numbers each line by its fixed position in the model's own output
    order — the same order `expense_ids` and `drafts` share, since
    `extract_and_store` appends to both from the same loop.
    """
    return [
        ConfirmationLine(
            index=index,
            expense_id=expense_id,
            item=draft.item,
            amount=draft.amount,
            category_slug=draft.category,
            # extract_and_store's own contract: resolve_dates leaves every
            # draft with a concrete date, so `draft.occurred_at or today` is
            # belt-and-braces, never load-bearing.
            occurred_at=draft.occurred_at or today,
        )
        for index, (expense_id, draft) in enumerate(
            zip(outcome_expense_ids, outcome_drafts, strict=True), start=1
        )
    ]


async def _process_claimed(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    bot: Bot,
    settings: Settings,
) -> None:
    category_ids = await categories.by_slug(session)
    today = datetime.now(tz=settings.tz).date()

    outcome = await extract_and_store(
        session=session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=today,
        models=settings.model_candidates,
        max_attempts=settings.max_extraction_attempts,
        max_message_attempts=settings.max_message_attempts,
    )

    if outcome.status == ExtractionStatus.OK:
        if outcome.asked_for_clarification:
            await bot.send_message(chat_id=message.chat_id, text=NO_EXPENSE_REPLY)
            return

        lines = _confirmation_lines(outcome.expense_ids, outcome.drafts, today=today)
        sent = await bot.send_message(
            chat_id=message.chat_id,
            text=render_confirmation(lines, today=today),
            reply_markup=confirmation_keyboard(lines),
        )
        await expenses.set_bot_message_id(session, outcome.expense_ids, sent.message_id)
        await session.commit()
        return

    # INVALID_JSON / FAILED: extract_and_store already called schedule_retry,
    # which decided between another silent round later and a terminal
    # 'failed'. Only the terminal case is user-facing — see
    # render.PROCESSING_FAILED_REPLY's docstring. schedule_retry updates
    # `messages` through a Core-style UPDATE, which does not refresh this
    # already-identity-mapped `message` object in place, so it must be
    # expired before re-reading it (mirrors
    # tests/integration/test_extraction_pipeline.py's own pattern).
    session.expire(message)
    refreshed = await session.get(Message, message.id)
    if refreshed is not None and refreshed.status == MessageStatus.FAILED:
        await bot.send_message(chat_id=message.chat_id, text=PROCESSING_FAILED_REPLY)


async def drain_loop(
    *,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LlmClient,
    settings: Settings,
    stop: asyncio.Event,
    idle_seconds: float = 2.0,
) -> None:
    while not stop.is_set():
        claimed = False
        async with sessionmaker() as session:
            message = await messages.claim_next(session, datetime.now(UTC))
            await session.commit()
            if message is not None:
                claimed = True
                try:
                    await _process_claimed(
                        session=session, message=message, llm=llm, bot=bot, settings=settings
                    )
                except Exception:
                    # A bug here must not stop the drain loop from ever
                    # claiming the next message — the Telegram-side analogue
                    # of run_polling's "log and move on" for non-persistence
                    # failures.
                    logger.exception("failed to process claimed message_id=%s", message.id)

        if not claimed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
