"""Orchestrates one message's extraction round: calls the LLM through the
repair loop, records every attempt in `extractions` (CLAUDE.md rule 6), and
writes `expenses` on success. Sends nothing to Telegram — that stays the
adapter's job, which keeps this reusable by `evals/` and by the Stage 6 HTTP
adapter (docs/plans/stage-1-text-to-expense.md, Approach B).

`extract_and_store` routes on `message.kind` (docs/roadmap.md Stage 2): text
and voice each build their own request and parse their own result shape
(`core.extraction.text` / `core.extraction.voice`), but share one repair loop
— `_run_extraction_round` below — so the attempt budget, the `extractions`
row per attempt, cost/model_id extraction and the retry backoff cannot drift
between modalities. Two attempt budgets are distinct and must not be
confused:

- `max_attempts` (`Settings.max_extraction_attempts`, spec §4.3 says two) —
  repair attempts *within one processing round*: an `invalid_json` response
  gets a repair prompt and one more try, still against the same claimed
  message.
- `max_message_attempts` (`Settings.max_message_attempts`) — processing
  *rounds* for the whole message, tracked by `messages.attempts`
  (incremented once per round by `repo.messages.claim_next`, before this
  function ever runs). A round that ends without a stored `ok` result calls
  `repo.messages.schedule_retry`, which decides between another round later
  and a terminal `failed`.

Text: `core.extraction.currency.detect_foreign_currency` runs first, on
`raw_text`, and on a hit returns without ever calling the model — see that
module's docstring for why.

Voice: there is no text to inspect before the call, so the same guard runs
on the model's own transcript, after extraction — the call is already paid
for by then, which is accepted (docs/roadmap.md Stage 2). Two more voice-only
guards run *before* any download: `models` empty means `MODEL_VOICE` is
unset, and `message.duration_seconds` over `max_voice_seconds` means the
note is refused before Telegram is asked for the file at all. A download or
`ffmpeg` failure (`AudioFetchError`, raised by the `fetch_audio` callable —
never imported directly, see that parameter's own note) happens *before* any
model call, so it is handled like the currency/duration guards, not like an
`LlmError`: the message is scheduled for retry and no `extractions` row is
ever written, because no call was ever made to record.
"""

import base64
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction import text, voice
from finbot.core.extraction.common import ExtractionInvalidError, build_repair_request
from finbot.core.extraction.currency import FOREIGN_CURRENCY_ERROR, detect_foreign_currency
from finbot.core.extraction.ports import (
    AudioFetchError,
    LlmClient,
    LlmError,
    LlmRequest,
    LlmResponse,
)
from finbot.core.extraction.schema import ExpenseDraft
from finbot.core.models import ExtractionStatus, MessageKind
from finbot.prompts import PROMPT_VERSION_TEXT, PROMPT_VERSION_VOICE
from finbot.repo import expenses as expenses_repo
from finbot.repo import extractions as extractions_repo
from finbot.repo import messages as messages_repo
from finbot.repo.models import Message

logger = logging.getLogger(__name__)

_RETRY_BASE_SECONDS = 30
_RETRY_CAP_SECONDS = 30 * 60

# LlmError means no response body ever arrived, so there is no "model that
# actually served the request" to record — recording `models[0]` (the
# requested model, never the served one) would read as a real value in
# exactly the place a "which model errors on us" query would look. A
# sentinel says plainly that no model ever responded, rather than lying
# with a plausible-looking model id. Contains no "/": every real OpenRouter
# model id is "vendor/model", so this can never collide with one — a future
# "improvement" to a friendlier-looking value like "unknown" would lose that
# guarantee, which is the whole reason to keep this one deliberately ugly.
NO_RESPONSE_MODEL_ID = "no-response"

# Set on `messages.last_error` when a voice message arrives with `MODEL_VOICE`
# unset, or over `max_voice_seconds` — findable the same way
# FOREIGN_CURRENCY_ERROR is, since both are refusals rather than failures.
VOICE_NOT_CONFIGURED_ERROR = "voice_not_configured"
VOICE_TOO_LONG_ERROR = "voice_too_long"


@dataclass(frozen=True)
class ExtractionOutcome:
    status: ExtractionStatus
    expense_ids: tuple[int, ...] = field(default_factory=tuple)
    drafts: tuple[ExpenseDraft, ...] = field(default_factory=tuple)
    asked_for_clarification: bool = False
    # True for the foreign-currency guard: `status` is set to something in
    # ExtractionStatus regardless (the type requires it), but for text it
    # means nothing — no extraction was attempted, so no `extractions` row
    # exists for it to describe. For voice the guard runs on the transcript
    # *after* a real call, so an `extractions` row (status OK) does exist;
    # either way, callers (runner.py) must check this flag before ever
    # looking at `status`.
    foreign_currency: bool = False
    # True only when `MODEL_VOICE` is unset (an empty `models` tuple) or the
    # note is longer than `max_voice_seconds` — both refusals before any
    # download or model call, so neither ever produces an `extractions` row.
    voice_not_configured: bool = False
    voice_too_long: bool = False
    # The model's own transcript, voice only. `None` for text, and `None`
    # for a voice round that never reached a parsed result (every guard, and
    # any FAILED/INVALID_JSON outcome).
    transcript: str | None = None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def backoff_seconds(processing_rounds: int) -> float:
    """`30 * 2**(rounds-1)` seconds, capped at 30 minutes. `processing_rounds`
    is `messages.attempts`, which `claim_next` has already incremented to at
    least 1 before this function runs; `max(..., 1)` only guards a test that
    calls this on an unclaimed message.

    Public (no leading underscore): `adapters/telegram/runner.py`'s drain
    loop calls this too, to release a message `_process_claimed` crashed on
    with the same backoff a repair-loop exhaustion would use — one retry
    schedule, not two that could quietly drift apart.
    """
    exponent = max(processing_rounds, 1) - 1
    # int(...): `int ** int` is typed `Any` in typeshed (a negative exponent
    # would return float), which would otherwise make this whole expression
    # — and therefore the function's return value — infer as `Any`.
    delay = int(_RETRY_BASE_SECONDS * (2**exponent))
    return min(delay, _RETRY_CAP_SECONDS)


async def _schedule_retry(
    session: AsyncSession, message: Message, *, error: str, max_message_attempts: int
) -> None:
    await messages_repo.schedule_retry(
        session,
        message.id,
        error=error,
        delay_seconds=backoff_seconds(message.attempts),
        max_attempts=max_message_attempts,
    )
    await session.commit()


async def _run_extraction_round[ResultT](
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    request: LlmRequest,
    prompt_version: str,
    max_attempts: int,
    max_message_attempts: int,
    parse: Callable[[str], ResultT],
) -> tuple[ResultT, LlmResponse] | ExtractionStatus:
    """The repair loop shared by every modality (CLAUDE.md rule 6,
    docs/roadmap.md Stage 2): up to `max_attempts` calls to `llm`, one
    `extractions` row per attempt, cost/model_id read from the response
    body, and `schedule_retry`'s backoff on every path that does not end in
    a parsed result. `parse` is the only per-modality behaviour —
    `core.extraction.text.parse_content` / `core.extraction.voice.
    parse_content` — everything else here is identical between them.

    Returns the parsed result and the response that produced it on success.
    On failure, returns the `ExtractionStatus` the caller should report —
    `FAILED` for a transport error, `INVALID_JSON` for an exhausted repair
    loop — after already calling `schedule_retry` (and committing) for it,
    so the caller only ever has to handle the success path itself.
    """
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            response = await llm.complete(request)
        except LlmError as exc:
            # A provider outage is not repairable by rephrasing (2.8.2):
            # stop the repair loop immediately and let schedule_retry decide
            # the message's fate, the same as an exhausted repair loop does.
            await extractions_repo.record(
                session,
                message_id=message.id,
                model_id=NO_RESPONSE_MODEL_ID,
                prompt_version=prompt_version,
                attempt=attempt,
                status=ExtractionStatus.FAILED,
                raw_response=exc.raw,
                # Not always None: a malformed 200 (a null model, an empty
                # choices) can still carry a legible usage.cost next to
                # whatever else is wrong with the body — parse_response_body
                # fills LlmError.cost_usd in exactly then, so the call the
                # household was actually billed for is the row rule 6 keeps,
                # not a blanket NULL that reads as "this was free".
                cost_usd=exc.cost_usd,
                latency_ms=_elapsed_ms(started),
            )
            await _schedule_retry(
                session, message, error=str(exc), max_message_attempts=max_message_attempts
            )
            return ExtractionStatus.FAILED

        try:
            result = parse(response.content)
        except ExtractionInvalidError as exc:
            await extractions_repo.record(
                session,
                message_id=message.id,
                model_id=response.model_id,
                prompt_version=prompt_version,
                attempt=attempt,
                status=ExtractionStatus.INVALID_JSON,
                raw_response=response.raw,
                cost_usd=response.cost_usd,
                latency_ms=response.latency_ms,
            )
            last_error = str(exc)
            if attempt == max_attempts:
                break
            request = build_repair_request(request, response.content, last_error)
            continue

        await extractions_repo.record(
            session,
            message_id=message.id,
            model_id=response.model_id,
            prompt_version=prompt_version,
            attempt=attempt,
            status=ExtractionStatus.OK,
            raw_response=response.raw,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
        return result, response

    # The repair loop ended without a stored 'ok' outcome: defer the
    # message's fate to schedule_retry rather than failing it outright here.
    await _schedule_retry(
        session, message, error=last_error, max_message_attempts=max_message_attempts
    )
    return ExtractionStatus.INVALID_JSON


async def _create_expenses(
    session: AsyncSession,
    message: Message,
    drafts: Sequence[ExpenseDraft],
    category_ids: Mapping[str, int],
) -> tuple[int, ...]:
    expense_ids: list[int] = []
    for draft in drafts:
        if draft.occurred_at is None:
            # resolve_dates's own contract: every draft leaves it with a
            # concrete date. A raise here (never a bare assert, which
            # `python -O` strips) also narrows the type for mypy below.
            raise AssertionError("resolve_dates must resolve every occurred_at to a date")
        expense_id = await expenses_repo.create(
            session,
            message_id=message.id,
            user_id=message.user_id,
            category_id=category_ids[draft.category],
            item=draft.item,
            amount=draft.amount,
            occurred_at=draft.occurred_at,
        )
        expense_ids.append(expense_id)
    return tuple(expense_ids)


async def _extract_text(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    catalog: Sequence[CategorySpec],
    category_ids: Mapping[str, int],
    today: date,
    models: Sequence[str],
    max_attempts: int,
    max_message_attempts: int,
) -> ExtractionOutcome:
    if message.raw_text is None:
        raise ValueError(f"message {message.id} has no raw_text; extraction requires plain text")

    if detect_foreign_currency(message.raw_text):
        # Before anything else, and before building a request: currencies
        # are Stage 1.5 (docs/roadmap.md), so refusing here is not only
        # correct but free — no request is ever built, let alone sent. No
        # `extractions` row either: there was no attempt to record, only a
        # decision not to make one.
        await messages_repo.mark_skipped(session, message.id, error=FOREIGN_CURRENCY_ERROR)
        await session.commit()
        return ExtractionOutcome(status=ExtractionStatus.FAILED, foreign_currency=True)

    request = text.build_request(
        raw_text=message.raw_text, today=today, catalog=catalog, models=models
    )
    round_result = await _run_extraction_round(
        session=session,
        message=message,
        llm=llm,
        request=request,
        prompt_version=PROMPT_VERSION_TEXT,
        max_attempts=max_attempts,
        max_message_attempts=max_message_attempts,
        parse=text.parse_content,
    )
    if isinstance(round_result, ExtractionStatus):
        return ExtractionOutcome(status=round_result)
    result, _response = round_result

    resolved = text.resolve_dates(result, today)
    expense_ids = await _create_expenses(session, message, resolved.expenses, category_ids)

    await messages_repo.mark_done(session, message.id)
    await session.commit()
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        expense_ids=expense_ids,
        drafts=tuple(resolved.expenses),
        asked_for_clarification=not expense_ids,
    )


async def _extract_voice(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    catalog: Sequence[CategorySpec],
    category_ids: Mapping[str, int],
    today: date,
    models: Sequence[str],
    max_attempts: int,
    max_message_attempts: int,
    max_voice_seconds: int,
    fetch_audio: Callable[[], Awaitable[bytes]] | None,
) -> ExtractionOutcome:
    if not models:
        # MODEL_VOICE is unset (docs/roadmap.md Stage 2): nothing to call,
        # and calling nothing is the whole point — `Settings.
        # voice_model_candidates` resolves to an empty tuple for exactly
        # this reason. No download is attempted either.
        await messages_repo.mark_skipped(session, message.id, error=VOICE_NOT_CONFIGURED_ERROR)
        await session.commit()
        return ExtractionOutcome(status=ExtractionStatus.FAILED, voice_not_configured=True)

    if message.duration_seconds is not None and message.duration_seconds > max_voice_seconds:
        # Before any download (spec §7): the duration is already known from
        # the original Telegram update (core.models.IncomingMessage.
        # duration_seconds), so there is nothing to fetch to answer this.
        await messages_repo.mark_skipped(session, message.id, error=VOICE_TOO_LONG_ERROR)
        await session.commit()
        return ExtractionOutcome(status=ExtractionStatus.FAILED, voice_too_long=True)

    if fetch_audio is None:
        raise ValueError(f"voice message {message.id} has no fetch_audio callable")

    try:
        audio_bytes = await fetch_audio()
    except AudioFetchError as exc:
        # Before any model call: unlike an LlmError, no response was ever
        # attempted, so there is no `extractions` row to write — only a
        # reason to try again later, exactly like the guards above.
        await _schedule_retry(
            session, message, error=str(exc), max_message_attempts=max_message_attempts
        )
        return ExtractionOutcome(status=ExtractionStatus.FAILED)

    request = voice.build_request(
        audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
        today=today,
        catalog=catalog,
        models=models,
    )
    round_result = await _run_extraction_round(
        session=session,
        message=message,
        llm=llm,
        request=request,
        prompt_version=PROMPT_VERSION_VOICE,
        max_attempts=max_attempts,
        max_message_attempts=max_message_attempts,
        parse=voice.parse_content,
    )
    if isinstance(round_result, ExtractionStatus):
        return ExtractionOutcome(status=round_result)
    result, _response = round_result

    # Stored as soon as it exists, before the currency guard or expense
    # creation run, so the transcript survives even when neither of those
    # does (docs/roadmap.md Stage 2's decision 3).
    await messages_repo.set_transcript(session, message.id, result.transcript)

    if detect_foreign_currency(result.transcript):
        # After extraction, not before: there is no text to inspect until
        # the model has transcribed it, and the call is already paid for by
        # then — accepted (docs/roadmap.md Stage 2's decision 4). The
        # `extractions` row above already recorded the call; this only
        # decides not to act on what it returned.
        await messages_repo.mark_skipped(session, message.id, error=FOREIGN_CURRENCY_ERROR)
        await session.commit()
        return ExtractionOutcome(
            status=ExtractionStatus.OK, foreign_currency=True, transcript=result.transcript
        )

    resolved = voice.resolve_dates(result, today)
    expense_ids = await _create_expenses(session, message, resolved.expenses, category_ids)

    await messages_repo.mark_done(session, message.id)
    await session.commit()
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        expense_ids=expense_ids,
        drafts=tuple(resolved.expenses),
        asked_for_clarification=not expense_ids,
        transcript=resolved.transcript,
    )


async def extract_and_store(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    catalog: Sequence[CategorySpec],
    category_ids: Mapping[str, int],
    today: date,
    models: Sequence[str],
    max_attempts: int,
    max_message_attempts: int,
    max_voice_seconds: int,
    fetch_audio: Callable[[], Awaitable[bytes]] | None = None,
) -> ExtractionOutcome:
    """Routes on `message.kind` (docs/roadmap.md Stage 2). `fetch_audio` is
    the seam that keeps aiogram and `ffmpeg` out of `core` (CLAUDE.md rule
    3): `adapters/telegram/runner.py` is the only caller that ever supplies
    one, built from `adapters.telegram.audio.fetch_and_convert` bound to a
    real `Bot` and `file_id` — this module never imports that module, only
    the `AudioFetchError` it raises (`core.extraction.ports`). `None` for a
    text message; required to be non-`None` for a voice message once the
    configured/duration guards below have passed.
    """
    if message.kind == MessageKind.VOICE:
        return await _extract_voice(
            session=session,
            message=message,
            llm=llm,
            catalog=catalog,
            category_ids=category_ids,
            today=today,
            models=models,
            max_attempts=max_attempts,
            max_message_attempts=max_message_attempts,
            max_voice_seconds=max_voice_seconds,
            fetch_audio=fetch_audio,
        )
    return await _extract_text(
        session=session,
        message=message,
        llm=llm,
        catalog=catalog,
        category_ids=category_ids,
        today=today,
        models=models,
        max_attempts=max_attempts,
        max_message_attempts=max_message_attempts,
    )
