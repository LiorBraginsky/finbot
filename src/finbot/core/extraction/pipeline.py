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

from finbot.core.categories.catalog import FALLBACK_SLUG, CategorySpec
from finbot.core.extraction import bank, text, voice
from finbot.core.extraction.common import ExtractionInvalidError, build_repair_request
from finbot.core.extraction.currency import FOREIGN_CURRENCY_ERROR, detect_foreign_currency
from finbot.core.extraction.ports import (
    AudioFetchError,
    ImageFetchError,
    LlmClient,
    LlmError,
    LlmRequest,
    LlmResponse,
)
from finbot.core.extraction.schema import ExpenseDraft
from finbot.core.models import ExtractionStatus, MessageKind
from finbot.prompts import PROMPT_VERSION_BANK, PROMPT_VERSION_TEXT, PROMPT_VERSION_VOICE
from finbot.repo import categories as categories_repo
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

# Mirrors VOICE_NOT_CONFIGURED_ERROR: set on `messages.last_error` when a
# photo arrives with `MODEL_VISION` unset (docs/plans/stage-2_5-bank-
# screenshots.md, R10) — before any download or model call.
VISION_NOT_CONFIGURED_ERROR = "vision_not_configured"


@dataclass(frozen=True)
class BankSummary:
    """`bank.BankPlan` plus the two outcomes only the pipeline can know,
    because they depend on what the database actually did with the planned
    writes (docs/plans/stage-2_5-bank-screenshots.md, Approach C2):

    - `duplicates` — the drafts of planned writes whose keyed insert returned
      `None` (`repo.expenses.create_bank_row`'s own contract): already
      recorded, by this user, under this exact `(date, time, amount)` key.
      The **drafts**, not a count: a reply that only says "2 already
      recorded" leaves a wrongly-suppressed row invisible, which is the one
      failure mode of a dedup key too coarse for a given feed. Naming them
      turns that from silent into checkable — `len()` is the count.
    - `manual_collisions` — non-deleted, non-bank expenses matching one of
      the rows this round actually wrote, named for the reply but never
      merged or suppressed (R7): both rows keep existing.
    """

    plan: bank.BankPlan
    duplicates: tuple[ExpenseDraft, ...] = field(default_factory=tuple)
    manual_collisions: tuple[expenses_repo.ExpenseView, ...] = field(default_factory=tuple)


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
    # True only when `MODEL_VISION` is unset (an empty `models` tuple) — a
    # refusal before any download or model call, mirroring
    # `voice_not_configured` exactly.
    vision_not_configured: bool = False
    # The model's own transcript, voice only. `None` for text, and `None`
    # for a voice round that never reached a parsed result (every guard, and
    # any FAILED/INVALID_JSON outcome).
    transcript: str | None = None
    # Bank-feed rows only (Stage 2.5 Step 2): `None` for text and voice, and
    # `None` for a bank round that never reached a parsed result (the
    # vision-not-configured guard, an ImageFetchError, or any FAILED/
    # INVALID_JSON outcome).
    bank_summary: BankSummary | None = None


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


async def _resolve_category(
    session: AsyncSession,
    draft: ExpenseDraft,
    category_ids: Mapping[str, int],
) -> tuple[int, int | None]:
    """`(category_id, suggested_category_id)` for one draft (ADR-0021).

    Three outcomes, and the second is the point of the whole mechanism:

    - **No proposal, or a proposal on a row the model already categorised.**
      The draft's own category, nothing pending. The `category != other`
      guard enforces the prompt's own rule in code rather than trusting it:
      a proposal alongside a confident `groceries` is noise, and honouring it
      would let the model quietly reclassify a row it had already filed.
    - **The proposal names a category the owner has already approved.** The
      expense is filed under it directly, with nothing to tap. This is what
      makes "on the fly" mean *once*: the second Preply charge lands in
      «Освіта» by itself.
    - **The proposal is new, or proposed-but-unapproved.** The expense stays
      under `other` and carries a pointer to the suggested category, which is
      what the ✏️ picker turns into `➕ Створити «…»`. Nothing is filed under
      an unapproved category — ADR-0005's human gate, still intact, just
      reduced to one tap.

    `KeyError` on `category_ids[...]` would mean a slug with no row, which
    `catalog`'s own assertions and the seed drift guard already make
    impossible; it is left unguarded deliberately, so such a state fails
    loudly here rather than being papered over into `other`.
    """
    category_id = category_ids[draft.category]
    if draft.suggested_category is None or draft.category != FALLBACK_SLUG:
        return category_id, None

    resolved = await categories_repo.resolve_suggestion(session, draft.suggested_category)
    if resolved is None:
        # The proposal slugified onto a seeded category — a rewording of
        # something the model could have picked outright.
        return category_id, None

    view, needs_approval = resolved
    if needs_approval:
        return category_id, view.id
    return view.id, None


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
        category_id, suggested_category_id = await _resolve_category(session, draft, category_ids)
        expense_id = await expenses_repo.create(
            session,
            message_id=message.id,
            user_id=message.user_id,
            category_id=category_id,
            item=draft.item,
            amount=draft.amount,
            occurred_at=draft.occurred_at,
            suggested_category_id=suggested_category_id,
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


async def _extract_bank(
    *,
    session: AsyncSession,
    message: Message,
    llm: LlmClient,
    catalog: Sequence[CategorySpec],
    category_ids: Mapping[str, int],
    models: Sequence[str],
    max_attempts: int,
    max_message_attempts: int,
    anchor_date: date | None,
    fetch_image: Callable[[], Awaitable[str]] | None,
) -> ExtractionOutcome:
    """Mirrors `_extract_voice`'s order exactly (docs/plans/stage-2_5-bank-
    screenshots.md, Step 2): empty `models` -> `mark_skipped`
    (`VISION_NOT_CONFIGURED_ERROR`), no download, no call — R10. Then
    `fetch_image` (an `ImageFetchError` schedules a retry with no
    `extractions` row: nothing reached a model). Only past that does the
    shared repair loop ever run, followed by `bank.plan_writes` and the
    keyed inserts that give Approach C2's dedup guarantee its counters.

    `anchor_date` is `message.created_at` in the household's timezone
    (Approach B, arrival anchor) — computed by the caller
    (`adapters/telegram/runner.py`, Stage 2.5 Step 3), never here: this
    module stays free of `zoneinfo`-vs-storage concerns the same way `today`
    already is for text and voice.
    """
    if not models:
        # MODEL_VISION is unset: nothing to call, and calling nothing is the
        # whole point — `Settings.vision_model_candidates` resolves to an
        # empty tuple for exactly this reason. No download is attempted.
        await messages_repo.mark_skipped(session, message.id, error=VISION_NOT_CONFIGURED_ERROR)
        await session.commit()
        return ExtractionOutcome(status=ExtractionStatus.FAILED, vision_not_configured=True)

    if fetch_image is None or anchor_date is None:
        raise ValueError(f"photo message {message.id} has no fetch_image/anchor_date")

    try:
        image_data_url = await fetch_image()
    except ImageFetchError as exc:
        # Before any model call: unlike an LlmError, no response was ever
        # attempted, so there is no `extractions` row to write — only a
        # reason to try again later, exactly like voice's AudioFetchError
        # guard.
        await _schedule_retry(
            session, message, error=str(exc), max_message_attempts=max_message_attempts
        )
        return ExtractionOutcome(status=ExtractionStatus.FAILED)

    request = bank.build_request(image_data_url=image_data_url, catalog=catalog, models=models)
    round_result = await _run_extraction_round(
        session=session,
        message=message,
        llm=llm,
        request=request,
        prompt_version=PROMPT_VERSION_BANK,
        max_attempts=max_attempts,
        max_message_attempts=max_message_attempts,
        parse=bank.parse_content,
    )
    if isinstance(round_result, ExtractionStatus):
        return ExtractionOutcome(status=round_result)
    result, _response = round_result

    plan = bank.plan_writes(result, anchor=anchor_date)

    expense_ids: list[int] = []
    drafts: list[ExpenseDraft] = []
    written_pairs: list[tuple[date, ExpenseDraft]] = []
    duplicates: list[ExpenseDraft] = []
    for write in plan.writes:
        if write.draft.occurred_at is None:
            # bank.plan_writes's own contract: every write it returns has
            # already resolved a concrete date (R4/R5) — see
            # _create_expenses's identical guard for text/voice.
            raise AssertionError("bank.plan_writes must resolve every occurred_at to a date")
        occurred_at = write.draft.occurred_at
        category_id, suggested_category_id = await _resolve_category(
            session, write.draft, category_ids
        )
        expense_id = await expenses_repo.create_bank_row(
            session,
            message_id=message.id,
            user_id=message.user_id,
            category_id=category_id,
            item=write.draft.item,
            amount=write.draft.amount,
            occurred_at=occurred_at,
            bank_txn_key=write.key,
            suggested_category_id=suggested_category_id,
        )
        if expense_id is None:
            # Approach C2: the unique index rejected this row, so it was
            # already recorded — this is what R8 needs, not a SELECT this
            # code ran itself. The draft is kept, not just counted, so the
            # reply can name what it suppressed.
            duplicates.append(write.draft)
            continue
        expense_ids.append(expense_id)
        drafts.append(write.draft)
        written_pairs.append((occurred_at, write.draft))

    manual_collisions = await expenses_repo.manual_duplicate_candidates(
        session,
        [(occurred_at, draft.amount) for occurred_at, draft in written_pairs],
        user_id=message.user_id,
    )

    await messages_repo.mark_done(session, message.id)
    await session.commit()
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        expense_ids=tuple(expense_ids),
        drafts=tuple(drafts),
        bank_summary=BankSummary(
            plan=plan, duplicates=tuple(duplicates), manual_collisions=tuple(manual_collisions)
        ),
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
    fetch_image: Callable[[], Awaitable[str]] | None = None,
    anchor_date: date | None = None,
) -> ExtractionOutcome:
    """Routes on `message.kind` (docs/roadmap.md Stage 2; Stage 2.5 adds
    `PHOTO`). `fetch_audio`/`fetch_image` are the seams that keep aiogram,
    `ffmpeg` and this project's image-sniffing out of `core` (CLAUDE.md rule
    3): `adapters/telegram/runner.py` is the only caller that ever supplies
    either, built from `adapters.telegram.audio.fetch_and_convert` /
    `adapters.telegram.images.fetch_as_data_url` bound to a real `Bot` and
    `file_id` — this module never imports either adapter module, only the
    `AudioFetchError`/`ImageFetchError` they raise
    (`core.extraction.ports`). Each is `None` for the two modalities it does
    not apply to, and required to be non-`None` once its own modality's
    configured guard has passed. `anchor_date` is the arrival anchor
    (Approach B) a photo's date headers resolve against — `message.
    created_at` in the household's timezone, computed by the caller for the
    same reason `today` already is.
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
    if message.kind == MessageKind.PHOTO:
        return await _extract_bank(
            session=session,
            message=message,
            llm=llm,
            catalog=catalog,
            category_ids=category_ids,
            models=models,
            max_attempts=max_attempts,
            max_message_attempts=max_message_attempts,
            anchor_date=anchor_date,
            fetch_image=fetch_image,
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
