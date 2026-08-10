"""The Pydantic DTOs an extraction round produces, and the strict JSON
Schema sent to OpenRouter as `response_format.json_schema.schema`.

`text_json_schema()` is hand-built rather than derived from
`ExpenseDraft.model_json_schema()`: Pydantic emits `$defs`/`$ref` for nested
models, omits `additionalProperties: false`, and renders `Decimal` as
`anyOf[{"type": "number"}, {"type": "string"}]` — none of which OpenRouter's
strict structured-output mode accepts. `tests/unit/test_extraction_schema.py`
proves the two properties that make the schema actually strict by walking
its shape, so the derivation is tested rather than trusted.
"""

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from finbot.core.categories.catalog import FALLBACK_SLUG, SLUGS
from finbot.core.money import to_amount

logger = logging.getLogger(__name__)

_ITEM_MAX_LENGTH = 200


class ExpenseDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item: str
    amount: Decimal
    category: str
    occurred_at: date | None = None

    @field_validator("item")
    @classmethod
    def _clean_item(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("item must not be empty")
        # Truncated, not rejected: a model returning an over-long noun
        # phrase is an annoyance, not a reason to spend a repair call.
        return stripped[:_ITEM_MAX_LENGTH]

    @field_validator("amount")
    @classmethod
    def _quantize_amount(cls, value: Decimal) -> Decimal:
        return to_amount(value)

    @field_validator("category")
    @classmethod
    def _fallback_unknown_category(cls, value: str) -> str:
        # The schema's `enum` already makes this near-impossible; when it
        # happens anyway (a repaired/edited response, a future looser
        # schema), filing it under `other` is worth more than a repair call.
        if value in SLUGS:
            return value
        logger.warning("unknown category slug %r from model; coercing to %r", value, FALLBACK_SLUG)
        return FALLBACK_SLUG


class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expenses: list[ExpenseDraft]


class VoiceExtractionResult(BaseModel):
    """`transcript` alongside `expenses` (docs/roadmap.md Stage 2, ADR-0004):
    a separate model from `ExtractionResult`, not that model with an
    optional field bolted on, because the two prompts have different
    contracts — one call transcribes then extracts, the other only
    extracts — and `extra="forbid"` on each should describe exactly one of
    them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcript: str
    expenses: list[ExpenseDraft]


def text_json_schema(slugs: Sequence[str]) -> dict[str, Any]:
    """Hand-built, strict-mode-ready JSON Schema for `ExtractionResult`.

    Every object node carries `additionalProperties: false` and a
    `required` list naming *all* of its own properties — OpenAI/OpenRouter's
    strict structured-output mode requires both, on every nested object, not
    just the root. `category.enum` is `slugs` verbatim, in the order given —
    callers pass catalog order, so `other` (the fallback) lands last.
    """
    expense_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "amount": {"type": "number"},
            "category": {"type": "string", "enum": list(slugs)},
            "occurred_at": {"type": ["string", "null"]},
        },
        "required": ["item", "amount", "category", "occurred_at"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "expenses": {"type": "array", "items": expense_schema},
        },
        "required": ["expenses"],
        "additionalProperties": False,
    }


def voice_json_schema(slugs: Sequence[str]) -> dict[str, Any]:
    """Hand-built, strict-mode-ready JSON Schema for `VoiceExtractionResult`
    — `text_json_schema`'s own docstring explains why hand-built at all.

    Deliberately a fully independent literal from `text_json_schema`, not a
    shared `expense_schema` helper the two both call: ADR-0014 keeps
    `ExpenseDraft` and its hand-derived wire shape "in one file, twenty
    lines apart" as the guard against drift rather than a clever
    abstraction, and the same reasoning applies here — two contracts that
    are free to evolve independently should not share the code that
    describes them.
    """
    expense_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "amount": {"type": "number"},
            "category": {"type": "string", "enum": list(slugs)},
            "occurred_at": {"type": ["string", "null"]},
        },
        "required": ["item", "amount", "category", "occurred_at"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "transcript": {"type": "string"},
            "expenses": {"type": "array", "items": expense_schema},
        },
        "required": ["transcript", "expenses"],
        "additionalProperties": False,
    }
