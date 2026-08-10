"""Deterministic scoring for the golden set.

Every metric here compares parsed model output against a hand-labelled
expectation with `==`. `item_similar` (docs/specs/2026-08-09-expense-capture-
design.md §8) is deliberately absent: judging whether "хліб" and "a loaf of
bread" name the same thing needs a judge model, and that is Stage 3's job.
Never call a judge where an exact check exists.

`amount_exact`, `category_exact` and `date_exact` are computed **only** when
`count_exact` already holds — an off-by-one item count makes positional
comparison meaningless, so the case is scored as a miss on every metric
rather than compared against a misaligned list. `zip(..., strict=True)` would
raise in that situation; checking `count_exact` first avoids ever reaching it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from finbot.core.extraction.schema import ExtractionResult


@dataclass(frozen=True)
class ExpectedExpense:
    """One line of a golden case's `expected` array.

    `item` is carried through purely for a human reading the case table — it
    is never compared. Scoring `item` needs a judge (Stage 3); comparing it
    with `==` here would fail correct paraphrases and teach nothing.
    """

    item: str
    amount: Decimal
    category: str
    occurred_offset_days: int


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    raw_text: str
    expected: tuple[ExpectedExpense, ...]


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    schema_ok: bool
    count_exact: bool
    amount_exact: bool
    category_exact: bool
    date_exact: bool
    cost_usd: Decimal | None
    latency_ms: int


@dataclass(frozen=True)
class ModelResult:
    model: str
    total: int
    schema_ok: int
    count_exact: int
    amount_exact: int
    category_exact: int
    date_exact: int
    costs: tuple[Decimal, ...]
    latencies_ms: tuple[int, ...]

    @property
    def cost_mean(self) -> Decimal | None:
        """Mean of `usage.cost` over calls that reported one. `None` — not
        zero — when nothing did, so "no data" is never confused with "free".
        """
        if not self.costs:
            return None
        return sum(self.costs, Decimal("0")) / len(self.costs)

    @property
    def latency_p50_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.50)

    @property
    def latency_p95_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.95)


def _nearest_rank_percentile(values: tuple[int, ...], quantile: float) -> int:
    """Nearest-rank percentile: no interpolation, so the result is always one
    of the actual observed latencies — deterministic and easy to sanity-check
    on the small samples (eleven cases x a handful of repeats) this runner
    ever produces.
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(math.ceil(quantile * len(ordered)) - 1, 0)
    return ordered[rank]


def load_golden_cases(path: Path) -> list[GoldenCase]:
    """One JSON object per line. Amounts are JSON strings (`"50.00"`), never
    bare numbers — CLAUDE.md rule 2 applies to a hand-written fixture just as
    much as to a wire response. Dates are `occurred_offset_days`, relative to
    the run date, never an absolute date (a clock bomb in a committed file).
    """
    cases: list[GoldenCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload: dict[str, Any] = json.loads(stripped)
        expected = tuple(
            ExpectedExpense(
                item=item["item"],
                amount=Decimal(item["amount"]),
                category=item["category"],
                occurred_offset_days=int(item["occurred_offset_days"]),
            )
            for item in payload["expected"]
        )
        cases.append(
            GoldenCase(case_id=payload["id"], raw_text=payload["input"], expected=expected)
        )
    return cases


def score_case(
    case: GoldenCase,
    today: date,
    resolved: ExtractionResult,
    *,
    cost_usd: Decimal | None,
    latency_ms: int,
) -> CaseScore:
    """Score one already-parsed, already-date-resolved response against its
    case. `resolved` must have come through `core.extraction.text.
    resolve_dates` — every `occurred_at` here is a concrete date, never
    `None` — the same production step the confirmation message relies on.
    """
    actual = resolved.expenses
    count_exact = len(actual) == len(case.expected)
    if not count_exact:
        return CaseScore(
            case_id=case.case_id,
            schema_ok=True,
            count_exact=False,
            amount_exact=False,
            category_exact=False,
            date_exact=False,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    pairs = list(zip(actual, case.expected, strict=True))
    amount_exact = all(a.amount == e.amount for a, e in pairs)
    category_exact = all(a.category == e.category for a, e in pairs)
    date_exact = all(
        a.occurred_at == today + timedelta(days=e.occurred_offset_days) for a, e in pairs
    )
    return CaseScore(
        case_id=case.case_id,
        schema_ok=True,
        count_exact=True,
        amount_exact=amount_exact,
        category_exact=category_exact,
        date_exact=date_exact,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def failed_case_score(case_id: str, *, cost_usd: Decimal | None, latency_ms: int) -> CaseScore:
    """The response never became a usable `ExtractionResult`: a transport
    failure (`LlmError`) or a schema violation (`ExtractionInvalidError`).
    Every metric is a miss — there is nothing to compare.
    """
    return CaseScore(
        case_id=case_id,
        schema_ok=False,
        count_exact=False,
        amount_exact=False,
        category_exact=False,
        date_exact=False,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def aggregate(model: str, scores: list[CaseScore]) -> ModelResult:
    return ModelResult(
        model=model,
        total=len(scores),
        schema_ok=sum(score.schema_ok for score in scores),
        count_exact=sum(score.count_exact for score in scores),
        amount_exact=sum(score.amount_exact for score in scores),
        category_exact=sum(score.category_exact for score in scores),
        date_exact=sum(score.date_exact for score in scores),
        costs=tuple(score.cost_usd for score in scores if score.cost_usd is not None),
        latencies_ms=tuple(score.latency_ms for score in scores),
    )


def render_table(results: list[ModelResult]) -> str:
    """Raw counts, never percentages — `evals/README.md`'s stated format.
    A count out of eleven is legible at a glance; a percentage hides exactly
    which case failed, which is what a human deciding between models needs.
    """
    header = (
        "| model | schema_ok | count_exact | amount_exact | category_exact "
        "| date_exact | mean cost (USD) | p50 latency (ms) | p95 latency (ms) |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|"
    rows = [header, separator]
    for result in results:
        cost = f"{result.cost_mean:.6f}" if result.cost_mean is not None else "n/a"
        rows.append(
            f"| {result.model} "
            f"| {result.schema_ok}/{result.total} "
            f"| {result.count_exact}/{result.total} "
            f"| {result.amount_exact}/{result.total} "
            f"| {result.category_exact}/{result.total} "
            f"| {result.date_exact}/{result.total} "
            f"| {cost} "
            f"| {result.latency_p50_ms} "
            f"| {result.latency_p95_ms} |"
        )
    return "\n".join(rows)
