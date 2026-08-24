"""Pure request/response transforms and the write-planning classifier for
bank-feed screenshots: no I/O, no clock — parallel to `text.py`/`voice.py`,
and for the same reason (see `text.py`'s own docstring). The image is
supplied by the caller as a data URL, already downloaded and sniffed by
`adapters.telegram.images` (Stage 2.5 Step 2; CLAUDE.md rule 3: this module
stays in `core/` and must never import that module directly) — `bank.py`
only builds the request around it and turns what comes back into the rows to
write.

**No foreign-currency guard runs on this path.** `text.py`/`voice.py` run
`core.extraction.currency.detect_foreign_currency` because a person can type
or say an amount in a currency this project does not record. A bank feed's
amounts are the account's own currency by construction (the prompt's rule 4
already tells the model to read only the account-currency figure when a
foreign-currency line is also printed), so running that regex over `merchant`
strings here would only misfire on a merchant name that happens to contain
one of its markers.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction import bank_dates
from finbot.core.extraction.common import ExtractionInvalidError, strip_fence
from finbot.core.extraction.ports import LlmRequest
from finbot.core.extraction.schema import (
    BankExtractionResult,
    BankRowKind,
    ExpenseDraft,
    bank_json_schema,
)
from finbot.core.money import loads_decimal, to_amount
from finbot.prompts import render_bank_prompt

SCHEMA_NAME = "bank_extraction_result"

# The four wire kinds that are neither `expense` nor the domain-only
# `unclassified` — a row of any of these is reported and stored nowhere
# (Approach A1), counted in `BankPlan.skipped_by_kind` by its own kind.
_SKIPPED_KINDS: frozenset[BankRowKind] = frozenset(
    {
        BankRowKind.INCOME,
        BankRowKind.SAVINGS,
        BankRowKind.OWN_TRANSFER,
        BankRowKind.TRANSFER_OUT,
    }
)

# `plan_writes` writes a row only when `row.kind is BankRowKind.EXPENSE` — a
# whitelist, not the blacklist `_SKIPPED_KINDS` alone would be (ADR-0017).
# This pins that every `BankRowKind` is accounted for on one side or the
# other: a sixth wire kind added later without updating `_SKIPPED_KINDS`
# fails here, at import time, instead of silently falling through to the
# write path and being recorded as spending. `raise AssertionError` rather
# than a bare `assert` (ruff S101 forbids the latter outside `tests/`), and
# for the better reason that this must still fire under `python -O`.
if set(_SKIPPED_KINDS) | {BankRowKind.EXPENSE, BankRowKind.UNCLASSIFIED} != set(BankRowKind):
    raise AssertionError(
        "BankRowKind gained a member that is neither BankRowKind.EXPENSE, "
        "BankRowKind.UNCLASSIFIED, nor listed in _SKIPPED_KINDS"
    )


def build_request(
    *,
    image_data_url: str,
    catalog: Sequence[CategorySpec],
    models: Sequence[str],
) -> LlmRequest:
    """Mirrors `voice.build_request`'s shape, with the user turn carrying an
    `image_url` content part instead of `input_audio` — the OpenAI-style
    shape OpenRouter's image-input documentation specifies and the spike
    (`## Reality check`) measured working: `{"type": "image_url", "image_url":
    {"url": "data:image/jpeg;base64,..."}}`. No accompanying text part, for
    the same reason `voice.build_request` has none: the system prompt already
    carries every instruction.
    """
    system_prompt = render_bank_prompt(catalog=catalog)
    schema = bank_json_schema([category.slug for category in catalog])
    return LlmRequest(
        models=tuple(models),
        messages=(
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ),
        json_schema=schema,
        schema_name=SCHEMA_NAME,
    )


def parse_content(content: str) -> BankExtractionResult:
    """Strip an optional ```json fence, parse with `Decimal` floats, then
    validate against `BankExtractionResult`. Mirrors `text.parse_content`/
    `voice.parse_content` exactly — see `text.parse_content`'s docstring.
    """
    unfenced = strip_fence(content)
    try:
        payload = loads_decimal(unfenced)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise ExtractionInvalidError(f"not valid JSON: {exc}") from exc

    try:
        return BankExtractionResult.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionInvalidError(f"did not match the schema: {exc}") from exc


_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


def _normalize_time(time: str | None) -> str:
    """`"9:05"` and `"09:05"` are the same clock time, but reach here
    verbatim off the wire (ADR-0018 §6) — two reads of the same pixels
    disagreeing only on zero-padding would otherwise mint two dedup keys for
    one transaction and double-count money, exactly the failure the key
    exists to prevent. Anything that is not `H:MM`/`HH:MM` (empty, `None`,
    an unrecognised format, or a string too long to be a clock time at all)
    normalises to `""` — the same "no information" value `time=None` already
    collapses to, and what keeps `bank_txn_key` bounded well under
    `expenses.bank_txn_key`'s `String(64)` regardless of what the model
    returns.
    """
    if not time:
        return ""
    match = _TIME_PATTERN.match(time)
    if match is None:
        return ""
    hour, minute = match.groups()
    return f"{int(hour):02d}:{minute}"


def bank_txn_key(*, occurred_at: date, time: str | None, amount: Decimal) -> str:
    """The database-enforced dedup key (Approach C2): `date | time | amount`
    at two decimal places, `merchant` deliberately excluded — OCR variance
    between two reads of the same pixels would otherwise defeat dedup and
    double-count money, the worst outcome this stage can produce. `time` is
    normalised first (`_normalize_time`, ADR-0018 §6): `time=None`,
    `time=""` and an unnormalisable `time` all collapse to the same key —
    none of them carries information the other does not.
    """
    return f"{occurred_at.isoformat()}|{_normalize_time(time)}|{amount:.2f}"


@dataclass(frozen=True)
class BankWrite:
    """One row `plan_writes` decided to write, and the key it will be written
    under — kept together so `expense_ids`/`drafts` stay index-aligned with
    the keys used for the keyed insert (Step 2), without a second pass over
    `BankExtractionResult.rows`.
    """

    draft: ExpenseDraft
    key: str


@dataclass(frozen=True)
class BankPlan:
    """The output of `plan_writes`: what to write, in the model's own row
    order, plus every reason a row was *not* written — each its own counter,
    so a row can be attributed to exactly one reason (R8: the user always
    learns what was skipped and why).

    `skipped_by_kind` counts the four non-expense, non-unclassified kinds
    (`income`, `savings`, `own_transfer`, `transfer_out`) individually, which
    is what lets a reply distinguish "2 savings, 1 transfer" rather than
    reporting one lump "skipped" number. `unclassified` is separate: it is a
    domain-only outcome the wire schema cannot itself produce (`BankRowKind`'s
    own docstring), not one of the four kinds the model can legitimately
    choose.
    """

    anchor: date
    writes: tuple[BankWrite, ...] = field(default_factory=tuple)
    skipped_by_kind: Mapping[BankRowKind, int] = field(default_factory=dict)
    cut_off: int = 0
    unresolved_date: int = 0
    bad_amount: int = 0
    unclassified: int = 0


def plan_writes(result: BankExtractionResult, *, anchor: date) -> BankPlan:
    """The pure classifier (R3, R4): decides which rows become `ExpenseDraft`s
    and which are reported but written nowhere. Checked in R4's own order —
    `kind == expense`, then `partially_visible == false`, then `amount > 0`,
    then a resolved date — so each row is attributed to exactly the first
    reason that excludes it, never more than one counter per row.

    The kind check is a **whitelist** (ADR-0017): `row.kind is not
    BankRowKind.EXPENSE` is what excludes a row, not membership in
    `_SKIPPED_KINDS`. A sixth wire kind added later and forgotten in
    `_SKIPPED_KINDS` is therefore caught by the module-level exhaustiveness
    assertion above, not silently written to `expenses` as spending.

    Nothing here is allowed to raise (R8's other half): a bad row is a
    counted row, never an exception. `to_amount` and `ExpenseDraft`'s own
    validators are the two remaining ways a row this far into the function
    can still fail — a blank/whitespace `merchant`
    (`ExpenseDraft._clean_item`), or an amount extreme enough that
    `Decimal.quantize` raises `decimal.InvalidOperation` (an
    `ArithmeticError`, not a `ValueError`) instead of returning a quantized
    value — and both land in `bad_amount`, the same counter the row-level
    `amount <= 0` check above uses, since either way the row was unusable.

    `is_transaction_feed: false` short-circuits before any row is inspected:
    Approach E's guard means an unrelated photo or a receipt produces no
    drafts and no counts at all, regardless of what `rows` happens to
    contain.
    """
    if not result.is_transaction_feed:
        return BankPlan(anchor=anchor)

    writes: list[BankWrite] = []
    skipped_by_kind: dict[BankRowKind, int] = {}
    cut_off = 0
    unresolved_date = 0
    bad_amount = 0
    unclassified = 0

    for row in result.rows:
        if row.kind is not BankRowKind.EXPENSE:
            if row.kind is BankRowKind.UNCLASSIFIED:
                unclassified += 1
            else:
                skipped_by_kind[row.kind] = skipped_by_kind.get(row.kind, 0) + 1
            continue
        if row.partially_visible:
            cut_off += 1
            continue
        if row.amount <= 0:
            bad_amount += 1
            continue
        occurred_at = bank_dates.resolve(row.date_header, anchor=anchor)
        if occurred_at is None:
            unresolved_date += 1
            continue

        try:
            amount = to_amount(row.amount)
            draft = ExpenseDraft(
                item=row.merchant, amount=amount, category=row.category, occurred_at=occurred_at
            )
        except (ValueError, ArithmeticError):
            # to_amount enforces the upper bound (core.money.MAX_AMOUNT) the
            # row-level `amount <= 0` check above does not, and can raise
            # `decimal.InvalidOperation` (an `ArithmeticError`, not a
            # `ValueError`) for an extreme amount rather than returning a
            # quantized value; `ExpenseDraft`'s own validators can raise
            # `ValidationError` (a `ValueError` subclass) for, e.g., a
            # blank/whitespace `merchant`. Every one of those is a row this
            # function cannot use, not a reason to raise out of a pure
            # classifier (R8) — all land in the same counter the amount
            # guard above uses, since either way the row was unusable.
            bad_amount += 1
            continue

        key = bank_txn_key(occurred_at=occurred_at, time=row.time, amount=amount)
        writes.append(BankWrite(draft=draft, key=key))

    return BankPlan(
        anchor=anchor,
        writes=tuple(writes),
        skipped_by_kind=skipped_by_kind,
        cut_off=cut_off,
        unresolved_date=unresolved_date,
        bad_amount=bad_amount,
        unclassified=unclassified,
    )
