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

from finbot.core.categories.catalog import DERIVED_CATALOG, CategorySpec
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

# The wire kinds that are reported and stored nowhere (ADR-0017 as amended by
# ADR-0020), counted in `BankPlan.skipped_by_kind` by their own kind. Public
# (no leading underscore) because `adapters.telegram.render` derives its
# Ukrainian label map from this set and asserts the two cover each other — a
# kind skipped with no label would raise `KeyError` mid-reply, on a real
# screenshot, in production.
#
# Each one's reason for being here is different, and worth stating because
# `cash_withdrawal` and `transfer_out` deliberately are *not*:
#   - `income` — not a withdrawal of money at all.
#   - `savings` — moved into the household's own jar. Where it went is known.
#   - `own_transfer` — moved to the household's own other card. That card's
#     own feed will show whatever is eventually spent from it, so recording
#     the transfer as well would count the same money twice.
SKIPPED_KINDS: frozenset[BankRowKind] = frozenset(
    {
        BankRowKind.INCOME,
        BankRowKind.SAVINGS,
        BankRowKind.OWN_TRANSFER,
    }
)

# The non-`expense` kinds that are written, each under a category the **code**
# assigns from the kind alone — never the model's own `category`, which for
# these rows is meaningless (ADR-0020). Both mean the same thing: the money
# left the account and the feed does not say where it went. Recording it under
# an honest "we don't know" beats not recording it, because a missing row
# understates the total silently, while a miscategorised row is visible and
# one ✏️ tap from correct.
FORCED_CATEGORY: Mapping[BankRowKind, str] = {
    BankRowKind.CASH_WITHDRAWAL: "cash",
    BankRowKind.TRANSFER_OUT: "transfers",
}

# `plan_writes` writes a row only when its kind is in here — a whitelist, not
# the blacklist `SKIPPED_KINDS` alone would be (ADR-0017).
_WRITTEN_KINDS: frozenset[BankRowKind] = frozenset({BankRowKind.EXPENSE}) | frozenset(
    FORCED_CATEGORY
)

# This pins that every `BankRowKind` is accounted for on exactly one side: a
# seventh wire kind added later without updating either set fails here, at
# import time, instead of silently falling through to the write path and being
# recorded as spending — or silently vanishing from the reply. `raise
# AssertionError` rather than a bare `assert` (ruff S101 forbids the latter
# outside `tests/`), and for the better reason that this must still fire under
# `python -O`.
if SKIPPED_KINDS | _WRITTEN_KINDS | {BankRowKind.UNCLASSIFIED} != set(BankRowKind):
    raise AssertionError(
        "BankRowKind gained a member that is neither BankRowKind.UNCLASSIFIED "
        "nor listed in SKIPPED_KINDS or _WRITTEN_KINDS"
    )
if SKIPPED_KINDS & _WRITTEN_KINDS:
    raise AssertionError("a BankRowKind cannot be both skipped and written")
# `FORCED_CATEGORY`'s values have to exist as real `categories` rows, or the
# pipeline's `category_ids[draft.category]` lookup raises `KeyError` on a
# screenshot that happens to contain one — a crash, not a counted row.
if not set(FORCED_CATEGORY.values()) <= {c.slug for c in DERIVED_CATALOG}:
    raise AssertionError("every FORCED_CATEGORY slug must be in catalog.DERIVED_CATALOG")


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

    `skipped_by_kind` counts the three skipped kinds (`income`, `savings`,
    `own_transfer` — see `SKIPPED_KINDS` for why each) individually, which is
    what lets a reply distinguish "2 savings, 1 transfer to self" rather than
    reporting one lump "skipped" number. `unclassified` is separate: it is a
    domain-only outcome the wire schema cannot itself produce
    (`BankRowKind`'s own docstring), not one of the kinds the model can
    legitimately choose.
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

    The kind check is a **whitelist** (ADR-0017): absence from
    `_WRITTEN_KINDS` is what excludes a row, not membership in
    `SKIPPED_KINDS`. A seventh wire kind added later and forgotten in both
    sets is therefore caught by the module-level exhaustiveness assertion
    above, not silently written to `expenses` as spending.

    Three kinds are written, not one (ADR-0020): a true `expense` under the
    model's own `category`, and `cash_withdrawal`/`transfer_out` under the
    `FORCED_CATEGORY` slug the code assigns from the kind. A row cannot be
    partially believed — if it is written at all, its amount and date come
    from the same reading as an expense's do.

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
        if row.kind not in _WRITTEN_KINDS:
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
                item=row.merchant,
                amount=amount,
                # The model's `category` only for a true `expense`; for the
                # other written kinds the code decides from the kind alone
                # (ADR-0020) — a cash withdrawal has no merchant to
                # categorise, and whatever the model guessed for it is noise.
                category=FORCED_CATEGORY.get(row.kind, row.category),
                occurred_at=occurred_at,
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
