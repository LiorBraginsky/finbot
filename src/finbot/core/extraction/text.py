"""Pure request/response transforms for text extraction: no I/O, no clock.

Building the request and parsing the response are kept separate from
performing the call (`llm/openrouter.py`) and from persisting the result
(`core/extraction/pipeline.py`) so that every part of this module is testable
with plain values — no fake session, no fake client, no Docker.
"""

import logging
import re
from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction.ports import LlmRequest
from finbot.core.extraction.schema import ExtractionResult, text_json_schema
from finbot.core.money import loads_decimal
from finbot.prompts import render_text_prompt

logger = logging.getLogger(__name__)

SCHEMA_NAME = "extraction_result"

# Some providers wrap a `response_format` payload in a ```json fence anyway.
# DOTALL so the fence can wrap a multi-line document; anchored so a fence
# has to span the whole trimmed string, not merely appear somewhere in it.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


class ExtractionInvalidError(Exception):
    """`content` did not parse into `ExtractionResult`.

    The message is short enough to paste into the repair prompt verbatim —
    see `build_repair_request`.
    """


def build_request(
    *,
    raw_text: str,
    today: date,
    catalog: Sequence[CategorySpec],
    models: Sequence[str],
) -> LlmRequest:
    system_prompt = render_text_prompt(today=today, catalog=catalog)
    schema = text_json_schema([category.slug for category in catalog])
    return LlmRequest(
        models=tuple(models),
        messages=(
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ),
        json_schema=schema,
        schema_name=SCHEMA_NAME,
    )


def _strip_fence(content: str) -> str:
    stripped = content.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def parse_content(content: str) -> ExtractionResult:
    """Strip an optional ```json fence, parse with `Decimal` floats, then
    validate against `ExtractionResult`. Raises `ExtractionInvalidError` — never
    the underlying `json.JSONDecodeError`/`ValidationError` — so callers have
    one exception type to catch regardless of which step failed.
    """
    unfenced = _strip_fence(content)
    try:
        payload = loads_decimal(unfenced)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise ExtractionInvalidError(f"not valid JSON: {exc}") from exc

    try:
        return ExtractionResult.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionInvalidError(f"did not match the schema: {exc}") from exc


def build_repair_request(previous: LlmRequest, bad_content: str, error: str) -> LlmRequest:
    """Append the bad assistant turn and a repair instruction, keeping
    everything else — model candidates, schema — identical to `previous`.
    """
    assistant_message = {"role": "assistant", "content": bad_content}
    repair_message = {
        "role": "user",
        "content": (
            f"The previous reply did not match the schema: {error}\n"
            "Return only a JSON document matching the schema. No prose, no code fences."
        ),
    }
    return LlmRequest(
        models=previous.models,
        messages=(*previous.messages, assistant_message, repair_message),
        json_schema=previous.json_schema,
        schema_name=previous.schema_name,
    )


def resolve_dates(result: ExtractionResult, today: date) -> ExtractionResult:
    """`None` -> `today`. A future date is impossible by construction: clamp
    to `today` with a WARNING. A past date is accepted as given.
    """
    resolved = []
    for draft in result.expenses:
        occurred_at = draft.occurred_at
        if occurred_at is None:
            occurred_at = today
        elif occurred_at > today:
            logger.warning(
                "occurred_at %s is in the future; clamping to today (%s)", occurred_at, today
            )
            occurred_at = today
        resolved.append(draft.model_copy(update={"occurred_at": occurred_at}))
    return ExtractionResult(expenses=resolved)
