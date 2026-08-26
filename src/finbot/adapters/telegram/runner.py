"""The inbox drain (ADR-0013): claims one `pending` message at a time and
turns it into expenses and a reply. Runs alongside `polling.run_polling` in
one `asyncio.TaskGroup` (see main.py) — the two are independent: a slow or
failing model never blocks Telegram's own queue from being drained, and vice
versa.

Order within one claimed message is ADR-0007's, literally: **write, then
reply.** `extract_and_store` commits the `expenses`/`extractions` rows
before this module ever calls Telegram, so a crash after the write loses
nothing but the reply — and nothing resends it, since a `done` message is
never reclaimed. The expense itself survives regardless of when the crash
lands relative to `bot_message_id` being stamped, because the buttons carry
`expense_id` and siblings are grouped by `message_id`, never by
`bot_message_id`.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finbot.adapters.telegram.audio import fetch_and_convert
from finbot.adapters.telegram.images import fetch_as_data_url
from finbot.adapters.telegram.keyboards import confirmation_keyboard
from finbot.adapters.telegram.render import (
    FOREIGN_CURRENCY_REPLY,
    NO_EXPENSE_REPLY,
    PROCESSING_FAILED_REPLY,
    VISION_NOT_CONFIGURED_REPLY,
    VOICE_NOT_CONFIGURED_REPLY,
    render_bank_note,
    render_confirmation,
    to_confirmation_lines,
    transcript_line,
    voice_too_long_reply,
)
from finbot.config import Settings
from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import ExtractionOutcome, backoff_seconds, extract_and_store
from finbot.core.extraction.ports import LlmClient
from finbot.core.models import ExtractionStatus, MessageKind, MessageStatus
from finbot.repo import categories, expenses, messages
from finbot.repo.models import Message

logger = logging.getLogger(__name__)


def _fetch_audio_for(
    bot: Bot, message: Message, *, ffmpeg_timeout_seconds: int
) -> Callable[[], Awaitable[bytes]]:
    """Binds `adapters.telegram.audio.fetch_and_convert` to this message's
    `bot`/`file_id` — the only place aiogram's download API and `ffmpeg` are
    reachable from, and exactly the seam `core.extraction.pipeline` expects
    (its own `fetch_audio` parameter, never imported directly — CLAUDE.md
    rule 3).
    """
    if message.file_id is None:
        raise ValueError(f"voice message {message.id} has no file_id")
    return partial(fetch_and_convert, bot, message.file_id, timeout_seconds=ffmpeg_timeout_seconds)


def _fetch_image_for(bot: Bot, message: Message) -> Callable[[], Awaitable[str]]:
    """Binds `adapters.telegram.images.fetch_as_data_url` to this message's
    `bot`/`file_id` — mirrors `_fetch_audio_for` exactly, for the seam
    `core.extraction.pipeline` expects (its own `fetch_image` parameter,
    never imported directly — CLAUDE.md rule 3).
    """
    if message.file_id is None:
        raise ValueError(f"photo message {message.id} has no file_id")
    return partial(fetch_as_data_url, bot, message.file_id)


async def _send_bank_reply(
    *, session: AsyncSession, message: Message, outcome: ExtractionOutcome, bot: Bot
) -> None:
    """The bank-feed reply (docs/plans/stage-2_5-bank-screenshots.md, Step 3,
    Approach D2): the note first, always — sent once and never edited again,
    which is what lets it carry a summary `handlers._rerender_group` could
    never preserve (Reality check #2) — then the confirmation, sent only
    when at least one row was actually written (`outcome.expense_ids`).

    `set_bot_message_id` is stamped from the **confirmation**'s message id,
    never the note's: `bot_message_id` exists so a ✏️/🗑 button knows which
    rows it controls (ADR-0007), and the note carries no buttons at all.
    `delete_all_message_id=message.id` (the internal `messages` row, not a
    Telegram message id) is what lets `🗑 Видалити все` and a later
    `_rerender_group` find every row from this screenshot regardless of how
    many are still active.
    """
    summary = outcome.bank_summary
    if summary is None:
        raise AssertionError("_send_bank_reply requires outcome.bank_summary")
    anchor = summary.plan.anchor

    # `written=len(outcome.expense_ids)` — the same tuple the confirmation
    # below is built from, so the note cannot promise rows the next message
    # does not carry (see `render_bank_note`'s own docstring).
    await bot.send_message(
        chat_id=message.chat_id,
        text=render_bank_note(summary, anchor=anchor, written=len(outcome.expense_ids)),
    )

    if not outcome.expense_ids:
        return

    # Built from the rows that were written, not from the model's drafts:
    # a line now carries its category's label, emoji and pending proposal,
    # and only the database has those (see `to_confirmation_lines`).
    lines = to_confirmation_lines(await expenses.siblings(session, message.id))
    sent = await bot.send_message(
        chat_id=message.chat_id,
        text=render_confirmation(lines, today=anchor),
        reply_markup=confirmation_keyboard(lines, delete_all_message_id=message.id),
    )
    await expenses.set_bot_message_id(session, outcome.expense_ids, sent.message_id)
    await session.commit()


async def _process_claimed(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    bot: Bot,
    settings: Settings,
) -> None:
    category_ids = await categories.by_slug(session)
    # A read commits too, not only a write: the SELECT above autobegins a
    # transaction on this session, and nothing else touches the DB until
    # extract_and_store's own first write — so without this, that
    # transaction sits open, idle, for however long `llm.complete()` takes
    # (up to `settings.llm_timeout_seconds`). repo/messages.py's own
    # claim_next docstring promises "the LLM call never happens inside an
    # open transaction"; this is the other half of keeping that true.
    await session.commit()
    today = datetime.now(tz=settings.tz).date()

    is_voice = message.kind == MessageKind.VOICE
    is_photo = message.kind == MessageKind.PHOTO
    if is_voice:
        models = settings.voice_model_candidates
    elif is_photo:
        models = settings.vision_model_candidates
    else:
        models = settings.model_candidates
    fetch_audio = (
        _fetch_audio_for(bot, message, ffmpeg_timeout_seconds=settings.ffmpeg_timeout_seconds)
        if is_voice
        else None
    )
    fetch_image = _fetch_image_for(bot, message) if is_photo else None
    # message.created_at, not `today` (Approach B, the arrival anchor): a
    # bank screenshot's relative date headers ("Сьогодні"/"Вчора") mean
    # relative to when the photo *arrived*, not to whenever the drain loop
    # happens to claim it — the two can differ by the retry backoff, or by
    # however long the message sat pending.
    anchor_date = message.created_at.astimezone(settings.tz).date() if is_photo else None

    outcome = await extract_and_store(
        session=session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=today,
        models=models,
        max_attempts=settings.max_extraction_attempts,
        max_message_attempts=settings.max_message_attempts,
        max_voice_seconds=settings.max_voice_seconds,
        fetch_audio=fetch_audio,
        fetch_image=fetch_image,
        anchor_date=anchor_date,
    )

    if outcome.voice_not_configured:
        await bot.send_message(chat_id=message.chat_id, text=VOICE_NOT_CONFIGURED_REPLY)
        return

    if outcome.voice_too_long:
        await bot.send_message(
            chat_id=message.chat_id, text=voice_too_long_reply(settings.max_voice_seconds)
        )
        return

    if outcome.vision_not_configured:
        await bot.send_message(chat_id=message.chat_id, text=VISION_NOT_CONFIGURED_REPLY)
        return

    if outcome.foreign_currency:
        # extract_and_store already marked the message 'skipped' with
        # last_error set (core/extraction/currency.py) and, for text, never
        # called the model — checked ahead of `status`, which means nothing
        # here (see ExtractionOutcome's own docstring).
        await bot.send_message(chat_id=message.chat_id, text=FOREIGN_CURRENCY_REPLY)
        return

    if outcome.status == ExtractionStatus.OK:
        if outcome.bank_summary is not None:
            await _send_bank_reply(session=session, message=message, outcome=outcome, bot=bot)
            return

        if outcome.asked_for_clarification:
            reply = NO_EXPENSE_REPLY
            if outcome.transcript is not None:
                reply = f"{transcript_line(outcome.transcript)}\n{NO_EXPENSE_REPLY}"
            await bot.send_message(chat_id=message.chat_id, text=reply)
            return

        lines = to_confirmation_lines(await expenses.siblings(session, message.id))
        sent = await bot.send_message(
            chat_id=message.chat_id,
            text=render_confirmation(lines, today=today, transcript=outcome.transcript),
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


async def _release_crashed_claim(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Bot,
    message_id: int,
    chat_id: int,
    processing_rounds: int,
    error: str,
    max_message_attempts: int,
) -> None:
    """Releases a message `_process_claimed` crashed on, in a session of its
    own: whatever failed on the original `session` may have left it in a bad
    transactional state, and a second, independent write must not share
    that.

    Only touches the row if it is still `processing`. `extract_and_store`
    commits `messages.status` (to `done`, or to `pending`/`failed` via its
    own `schedule_retry` call) *before* `_process_claimed` ever calls
    Telegram — see the module docstring — so a crash later in
    `_process_claimed` (rendering the confirmation, `bot.send_message`,
    stamping `bot_message_id`) leaves a row that already has its final
    status. Calling `schedule_retry` unconditionally there would resurrect
    an already-`done` message and reprocess it: a second model call billed,
    and a second, duplicate set of `expenses`. The loss in that case is only
    the reply — the module docstring's own trade-off — not a crash this
    function can or should fix.

    Like `reset_processing`, correct only under this project's single-node,
    single-replica deployment (ADR-0002): nothing here stamps a claim owner
    on `message_id`, so a multi-replica deployment would need one before
    this function could tell "this process's own crashed claim" apart from
    a row a different replica is still legitimately working.
    """
    async with sessionmaker() as retry_session:
        current = await retry_session.get(Message, message_id)
        if current is None or current.status != MessageStatus.PROCESSING:
            return

        await messages.schedule_retry(
            retry_session,
            message_id,
            error=error,
            delay_seconds=backoff_seconds(processing_rounds),
            max_attempts=max_message_attempts,
        )
        await retry_session.commit()

        retry_session.expire(current)
        refreshed = await retry_session.get(Message, message_id)

    # The release above already committed by this point — a failure here is
    # only a failure to *notify*, and must not read, to whoever catches it,
    # as a failure to release. Caught here, not left to propagate, exactly
    # so drain_loop's own except clause never has to guess which one it was.
    if refreshed is not None and refreshed.status == MessageStatus.FAILED:
        try:
            await bot.send_message(chat_id=chat_id, text=PROCESSING_FAILED_REPLY)
        except Exception:
            logger.exception(
                "message_id=%s released to 'failed', but sending the reply failed", message_id
            )


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
                message_id = message.id
                chat_id = message.chat_id
                processing_rounds = message.attempts
                try:
                    await _process_claimed(
                        session=session, message=message, llm=llm, bot=bot, settings=settings
                    )
                except Exception as exc:
                    # A bug here must not stop the drain loop from ever
                    # claiming the next message — the Telegram-side analogue
                    # of run_polling's "log and move on" for non-persistence
                    # failures. Left alone, this row would stay 'processing'
                    # forever: claim_next already committed that status, and
                    # reset_processing only runs at startup (ADR-0011).
                    logger.exception("failed to process claimed message_id=%s", message_id)
                    try:
                        await _release_crashed_claim(
                            sessionmaker=sessionmaker,
                            bot=bot,
                            message_id=message_id,
                            chat_id=chat_id,
                            processing_rounds=processing_rounds,
                            error=str(exc),
                            max_message_attempts=settings.max_message_attempts,
                        )
                    except Exception:
                        # The release itself failed (e.g. Postgres is the
                        # thing that's down) — the row stays stuck in
                        # 'processing' until the next restart's
                        # reset_processing, but the drain loop still must
                        # not die: the next iteration may claim a different,
                        # unrelated message just fine.
                        logger.exception(
                            "failed to release claimed message_id=%s back from 'processing'",
                            message_id,
                        )

        if not claimed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
