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
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from finbot.core.categories.catalog import FALLBACK_SLUG, MODEL_SLUGS, SLUGS
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
        #
        # `SLUGS`, not `MODEL_SLUGS`: a draft is not always the model's own
        # choice. `bank.plan_writes` builds one with a code-assigned
        # `DERIVED_CATALOG` slug for a cash-withdrawal or transfer-out row
        # (ADR-0020), and this validator must not undo that.
        if value in SLUGS:
            return value
        logger.warning("unknown category slug %r; coercing to %r", value, FALLBACK_SLUG)
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


class BankRowKind(StrEnum):
    """The six wire kinds a bank-feed row can be classified as, plus a
    seventh, `UNCLASSIFIED`, that the wire schema's `enum` cannot itself
    produce (see `bank_json_schema` below — its `kind` enum lists only the
    six wire values).

    `CASH_WITHDRAWAL` is split out of `OWN_TRANSFER` by ADR-0020, because the
    two have opposite fates: a card-to-own-card transfer is skipped (the
    other card's own feed will show what was spent, so recording the transfer
    too would double-count), while cash leaves the banking system entirely
    and no later feed will ever account for it. `Зняття готівки в банкоматі`
    is a fixed Privat/Mono label, not free text, which is what makes this a
    kind the model can be asked to tell apart reliably.

    `UNCLASSIFIED` exists for the same reason `ExpenseDraft.
    _fallback_unknown_category` coerces an unknown slug to `other`: strict
    mode makes an out-of-enum value near-impossible, but a repaired response
    or a future looser schema could still produce one, and filing that row as
    unclassified — reported, written nowhere — beats spending a repair call
    and losing a whole screenshot.
    """

    EXPENSE = "expense"
    INCOME = "income"
    SAVINGS = "savings"
    OWN_TRANSFER = "own_transfer"
    CASH_WITHDRAWAL = "cash_withdrawal"
    TRANSFER_OUT = "transfer_out"
    UNCLASSIFIED = "unclassified"


_BANK_ROW_WIRE_KINDS: tuple[BankRowKind, ...] = (
    BankRowKind.EXPENSE,
    BankRowKind.INCOME,
    BankRowKind.SAVINGS,
    BankRowKind.OWN_TRANSFER,
    BankRowKind.CASH_WITHDRAWAL,
    BankRowKind.TRANSFER_OUT,
)
_BANK_ROW_WIRE_KIND_VALUES: frozenset[str] = frozenset(k.value for k in _BANK_ROW_WIRE_KINDS)


class BankRow(BaseModel):
    """One row of a bank-feed screenshot, as the model reads it. `category`
    is required by strict mode even for a non-`expense` row and is ignored
    for those (`## Chosen approach`, stage-2.5 plan) — the model still has to
    pick something, but nothing downstream reads it unless `kind` is
    `expense`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date_header: str
    time: str | None
    merchant: str
    amount: Decimal
    kind: BankRowKind
    category: str
    partially_visible: bool

    @field_validator("kind", mode="before")
    @classmethod
    def _fallback_unknown_kind(cls, value: Any) -> Any:
        # Mirrors _fallback_unknown_category below: the wire enum already
        # makes this near-impossible, so when it happens anyway, coercing to
        # UNCLASSIFIED is worth more than a repair call. `mode="before"`
        # because pydantic would otherwise reject an out-of-enum string
        # before this validator ever ran.
        #
        # `isinstance(value, str)` guards the membership check below: an
        # unhashable `value` (a model returning `"kind": []` or `{}`) would
        # otherwise raise `TypeError` out of `in _BANK_ROW_WIRE_KIND_VALUES`
        # — a schema-shaped input this validator exists specifically to
        # tolerate, not crash on.
        if isinstance(value, BankRowKind) or (
            isinstance(value, str) and value in _BANK_ROW_WIRE_KIND_VALUES
        ):
            return value
        logger.warning(
            "unknown bank row kind %r from model; coercing to %r",
            value,
            BankRowKind.UNCLASSIFIED.value,
        )
        return BankRowKind.UNCLASSIFIED

    @field_validator("category")
    @classmethod
    def _fallback_unknown_category(cls, value: str) -> str:
        # `MODEL_SLUGS`, deliberately narrower than `ExpenseDraft`'s `SLUGS`:
        # this field is the model's own answer, and `cash`/`transfers` are
        # never its to choose (ADR-0020). A model that emitted one anyway —
        # against the schema enum — must land on `other`, not smuggle a
        # code-assigned category in through the wire.
        if value in MODEL_SLUGS:
            return value
        logger.warning("unknown category slug %r from model; coercing to %r", value, FALLBACK_SLUG)
        return FALLBACK_SLUG


class BankExtractionResult(BaseModel):
    """Stage 2.5's third result shape (`## Chosen approach`): `rows` in the
    model's own top-to-bottom order, `is_transaction_feed` false meaning the
    image is not a bank transaction feed at all (Approach E's guard against a
    photographed receipt running this prompt before Stage 4 exists).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_transaction_feed: bool
    rows: list[BankRow]


def bank_json_schema(slugs: Sequence[str]) -> dict[str, Any]:
    """Hand-built, strict-mode-ready JSON Schema for `BankExtractionResult` —
    `text_json_schema`'s own docstring explains why hand-built at all, and
    ADR-0014's proximity-not-abstraction rule is why this is a fully
    independent literal rather than sharing a helper with `text_json_schema`/
    `voice_json_schema`.

    `kind.enum` is the six wire values only — `UNCLASSIFIED` is a domain
    concept `BankRow`'s validator introduces, never something the model is
    asked or allowed to emit.
    """
    row_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "date_header": {"type": "string"},
            "time": {"type": ["string", "null"]},
            "merchant": {"type": "string"},
            "amount": {"type": "number"},
            "kind": {"type": "string", "enum": [k.value for k in _BANK_ROW_WIRE_KINDS]},
            "category": {"type": "string", "enum": list(slugs)},
            "partially_visible": {"type": "boolean"},
        },
        "required": [
            "date_header",
            "time",
            "merchant",
            "amount",
            "kind",
            "category",
            "partially_visible",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "is_transaction_feed": {"type": "boolean"},
            "rows": {"type": "array", "items": row_schema},
        },
        "required": ["is_transaction_feed", "rows"],
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
