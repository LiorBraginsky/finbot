"""Persistence for finbot.repo.models.Message — the inbox (ADR-0013).

None of these functions commit; the caller decides the transaction boundary.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import IncomingMessage, MessageKind, MessageStatus
from finbot.repo.models import Message


def _initial_status(message: IncomingMessage) -> MessageStatus:
    """Commands never reach extraction — this is where the Stage-0 plan's
    "filtering commands out of extraction is Stage 1's job" lands. Plain
    text and, from Stage 2, voice are both PENDING rows the drain loop will
    claim; photos stay SKIPPED until Stage 4 gives them a pipeline of their
    own. `core.extraction.pipeline` is what refuses an unconfigured or
    too-long voice note — as a PENDING row it still goes through the drain
    loop, exactly like the foreign-currency guard does for text, rather than
    being turned away here where there is no way to reply.
    """
    if message.kind == MessageKind.TEXT:
        is_plain_text = not (message.raw_text or "").startswith("/")
        return MessageStatus.PENDING if is_plain_text else MessageStatus.SKIPPED
    if message.kind == MessageKind.VOICE:
        return MessageStatus.PENDING
    return MessageStatus.SKIPPED


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
            duration_seconds=message.duration_seconds,
            status=_initial_status(message),
        )
        .on_conflict_do_nothing(index_elements=[Message.telegram_update_id])
        .returning(Message.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def claim_next(session: AsyncSession, now: datetime) -> Message | None:
    """Atomically claim the oldest due `pending` message, or None.

    One statement — `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP
    LOCKED) RETURNING *` — so the claim is atomic and the transaction stays
    short: the LLM call never happens inside an open transaction. SKIP LOCKED
    costs one clause and makes a second worker safe if one ever appears.
    """
    candidate = (
        select(Message.id)
        .where(Message.status == MessageStatus.PENDING, Message.next_attempt_at <= now)
        .order_by(Message.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(Message)
        .where(Message.id == candidate.scalar_subquery())
        .values(status=MessageStatus.PROCESSING, attempts=Message.attempts + 1)
        .returning(Message)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_done(session: AsyncSession, message_id: int) -> None:
    await session.execute(
        update(Message).where(Message.id == message_id).values(status=MessageStatus.DONE)
    )


async def set_transcript(session: AsyncSession, message_id: int, transcript: str) -> None:
    """Stores a voice message's transcript into `raw_text` — the same column
    a text message's own words already live in (docs/roadmap.md Stage 2's
    decision 3), which is what makes a voice message searchable and what
    Stage 3's evals eventually read as production input. Called as soon as
    a voice extraction round parses successfully, before the currency guard
    or expense creation run, so the transcript survives even when neither
    of those does.
    """
    await session.execute(
        update(Message).where(Message.id == message_id).values(raw_text=transcript)
    )


async def mark_skipped(session: AsyncSession, message_id: int, *, error: str | None = None) -> None:
    """`error` is for a guard that decided *not* to call the model at all
    (e.g. the foreign-currency guard in `core/extraction/pipeline.py`) —
    unlike `schedule_retry`'s `last_error`, there is no failed attempt behind
    it, only a reason this message was never sent to one.
    """
    if error is None:
        await session.execute(
            update(Message).where(Message.id == message_id).values(status=MessageStatus.SKIPPED)
        )
    else:
        await session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(status=MessageStatus.SKIPPED, last_error=error)
        )


async def schedule_retry(
    session: AsyncSession,
    message_id: int,
    *,
    error: str,
    delay_seconds: float,
    max_attempts: int,
) -> None:
    """Reschedule a message for another processing round, or give up.

    `messages.attempts` counts processing rounds and is incremented once per
    round by `claim_next`, never here — this only decides the fate of the
    *next* claim: another `pending` round after `delay_seconds`, or a
    terminal `failed` once `attempts` has reached `max_attempts`.
    """
    message = await session.get(Message, message_id)
    if message is None:
        return

    if message.attempts >= max_attempts:
        await session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(status=MessageStatus.FAILED, last_error=error)
        )
    else:
        await session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                status=MessageStatus.PENDING,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                last_error=error,
            )
        )


async def reset_processing(session: AsyncSession) -> int:
    """Reset every `processing` row back to `pending`.

    Called once at startup, before the drain loop starts claiming rows —
    correct only because deployment is single-node, single-replica
    (ADR-0002); a multi-replica deployment could reset a row another replica
    is still actively working on. Does not commit.
    """
    # .returning(Message.id) rather than .rowcount: AsyncSession.execute()
    # types its return as the base Result[Any], which has no .rowcount —
    # only the CursorResult subtype does, and mypy cannot know which one a
    # given statement produces. Counting the returned ids is exactly as
    # cheap and stays within the typed surface.
    result = await session.execute(
        update(Message)
        .where(Message.status == MessageStatus.PROCESSING)
        .values(status=MessageStatus.PENDING)
        .returning(Message.id)
    )
    return len(result.scalars().all())
