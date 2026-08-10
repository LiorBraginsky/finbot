"""Pure request/response transforms for text extraction: no I/O, no clock.

Building the request and parsing the response are kept separate from
performing the call (`llm/openrouter.py`) and from persisting the result
(`core/extraction/pipeline.py`) so that every part of this module is testable
with plain values — no fake session, no fake client, no Docker.
"""

import logging
from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction.common import ExtractionInvalidError, strip_fence
from finbot.core.extraction.ports import LlmRequest
from finbot.core.extraction.schema import ExtractionResult, text_json_schema
from finbot.core.money import loads_decimal
from finbot.prompts import render_text_prompt

logger = logging.getLogger(__name__)

SCHEMA_NAME = "extraction_result"


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


def parse_content(content: str) -> ExtractionResult:
    """Strip an optional ```json fence, parse with `Decimal` floats, then
    validate against `ExtractionResult`. Raises `ExtractionInvalidError` — never
    the underlying `json.JSONDecodeError`/`ValidationError` — so callers have
    one exception type to catch regardless of which step failed.
    """
    unfenced = strip_fence(content)
    try:
        payload = loads_decimal(unfenced)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise ExtractionInvalidError(f"not valid JSON: {exc}") from exc

    try:
        return ExtractionResult.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionInvalidError(f"did not match the schema: {exc}") from exc


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
