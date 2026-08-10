"""Orchestrates one message's extraction round: calls the LLM through the
repair loop, records every attempt in `extractions` (CLAUDE.md rule 6), and
writes `expenses` on success. Sends nothing to Telegram — that stays the
adapter's job, which keeps this reusable by `evals/` and by the Stage 6 HTTP
adapter (docs/plans/stage-1-text-to-expense.md, Approach B).

Two attempt budgets are distinct and must not be confused:

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

The plan's Step 2.8 sketch lists only `max_attempts` in this function's
signature; `max_message_attempts` is required too; there is no
`schedule_retry` without it and no way to fill it from an existing parameter.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction.ports import LlmClient, LlmError
from finbot.core.extraction.schema import ExpenseDraft
from finbot.core.extraction.text import (
    ExtractionInvalidError,
    build_repair_request,
    build_request,
    parse_content,
    resolve_dates,
)
from finbot.core.models import ExtractionStatus
from finbot.prompts import PROMPT_VERSION_TEXT
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
# with a plausible-looking model id.
NO_RESPONSE_MODEL_ID = "no-response"


@dataclass(frozen=True)
class ExtractionOutcome:
    status: ExtractionStatus
    expense_ids: tuple[int, ...] = field(default_factory=tuple)
    drafts: tuple[ExpenseDraft, ...] = field(default_factory=tuple)
    asked_for_clarification: bool = False


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
) -> ExtractionOutcome:
    if message.raw_text is None:
        raise ValueError(f"message {message.id} has no raw_text; extraction requires plain text")

    request = build_request(raw_text=message.raw_text, today=today, catalog=catalog, models=models)
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
                prompt_version=PROMPT_VERSION_TEXT,
                attempt=attempt,
                status=ExtractionStatus.FAILED,
                raw_response=exc.raw,
                cost_usd=None,
                latency_ms=_elapsed_ms(started),
            )
            await messages_repo.schedule_retry(
                session,
                message.id,
                error=str(exc),
                delay_seconds=backoff_seconds(message.attempts),
                max_attempts=max_message_attempts,
            )
            await session.commit()
            return ExtractionOutcome(status=ExtractionStatus.FAILED)

        try:
            result = parse_content(response.content)
        except ExtractionInvalidError as exc:
            await extractions_repo.record(
                session,
                message_id=message.id,
                model_id=response.model_id,
                prompt_version=PROMPT_VERSION_TEXT,
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
            prompt_version=PROMPT_VERSION_TEXT,
            attempt=attempt,
            status=ExtractionStatus.OK,
            raw_response=response.raw,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )

        resolved = resolve_dates(result, today)
        expense_ids: list[int] = []
        for draft in resolved.expenses:
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

        await messages_repo.mark_done(session, message.id)
        await session.commit()
        return ExtractionOutcome(
            status=ExtractionStatus.OK,
            expense_ids=tuple(expense_ids),
            drafts=tuple(resolved.expenses),
            asked_for_clarification=not expense_ids,
        )

    # The repair loop ended without a stored 'ok' outcome: defer the
    # message's fate to schedule_retry rather than failing it outright here.
    await messages_repo.schedule_retry(
        session,
        message.id,
        error=last_error,
        delay_seconds=backoff_seconds(message.attempts),
        max_attempts=max_message_attempts,
    )
    await session.commit()
    return ExtractionOutcome(status=ExtractionStatus.INVALID_JSON)
