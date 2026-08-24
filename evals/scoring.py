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

import base64
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.paths import ensure_outside_repo

# `as convert_to_mp3` (not a bare import) is deliberate: mypy's strict
# mode otherwise treats a name imported-and-not-re-exported as private to
# this module, and load_voice_golden_cases's own identity pin
# (tests/unit/test_evals_scoring.py) imports this name from here
# specifically to assert it is still the bot's own function, not a copy.
from finbot.adapters.telegram.audio import convert_to_mp3 as convert_to_mp3

# Same reasoning, applied to the bank modality (Stage 2.5): `to_data_url` is
# the exact function `core.extraction.bank.build_request`'s caller uses to
# turn a real incoming photo into the data URL the request carries, imported
# directly rather than reimplemented — `test_evals_bank.py`'s identity pin
# checks this name is still that function, not a copy.
from finbot.adapters.telegram.images import to_data_url as to_data_url
from finbot.core.extraction import bank, bank_dates
from finbot.core.extraction.ports import AudioFetchError, ImageFetchError
from finbot.core.extraction.schema import (
    BankExtractionResult,
    BankRow,
    BankRowKind,
    ExtractionResult,
    VoiceExtractionResult,
)


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
    # None only for a transport failure (LlmError): no response ever came
    # back, so there is no real duration to record — 0 would silently make
    # a timing-out model look like the fastest one, and criterion 3
    # tie-breaks on p95. A schema-invalid response still has a real
    # latency and keeps it.
    latency_ms: int | None


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


def _parse_expected(payload: dict[str, Any], path: Path) -> tuple[ExpectedExpense, ...]:
    """Shared by `load_golden_cases` and `load_voice_golden_cases`: both
    formats carry the same `expected` array shape (docs/roadmap.md Stage 2's
    `voice_v1.jsonl` adds `expected_transcript_contains` alongside it, never
    inside it).
    """
    expected_items: list[ExpectedExpense] = []
    for item in payload["expected"]:
        # CLAUDE.md rule 2 applies to a hand-written fixture as much as
        # to a wire response: a bare JSON number here would parse fine
        # today but re-opens exactly the float-money hole `parse_float`
        # exists to close the moment someone edits this file by hand.
        # A raise, not a bare `assert` (S101; also stripped by `python
        # -O`), mirrors core.extraction.pipeline's own convention.
        if not isinstance(item["amount"], str):
            msg = (
                f"{path}: golden case {payload['id']!r} amount must be a JSON string, "
                f"never a bare number, got {item['amount']!r}"
            )
            raise TypeError(msg)
        expected_items.append(
            ExpectedExpense(
                item=item["item"],
                amount=Decimal(item["amount"]),
                category=item["category"],
                occurred_offset_days=int(item["occurred_offset_days"]),
            )
        )
    return tuple(expected_items)


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
        payload: dict[str, Any] = json.loads(stripped, parse_float=Decimal)
        cases.append(
            GoldenCase(
                case_id=payload["id"],
                raw_text=payload["input"],
                expected=_parse_expected(payload, path),
            )
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


def failed_case_score(
    case_id: str, *, cost_usd: Decimal | None, latency_ms: int | None
) -> CaseScore:
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
        latencies_ms=tuple(score.latency_ms for score in scores if score.latency_ms is not None),
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


# --- voice (docs/roadmap.md Stage 2) ------------------------------------------
#
# A parallel set of hand-maintained shapes, deliberately not the text ones
# with an optional field bolted on — same reasoning as `core.extraction.
# schema.voice_json_schema` next to `text_json_schema`: the two case formats
# and score shapes are free to evolve independently, and proximity in one
# file is the guard, not a shared abstraction between them.


@dataclass(frozen=True)
class VoiceGoldenCase:
    case_id: str
    audio_filename: str
    # Read, converted to mp3 with the bot's own `convert_to_mp3`, and
    # base64-encoded by `load_voice_golden_cases` itself, not by `evals.run.
    # run_voice_case` per call: reading every case's audio upfront, before
    # the first `client.complete`, is what makes a missing file or a broken
    # `ffmpeg` (the exact state the owner is in while still recording
    # samples — see evals/golden/voice/README.md) a fast, free failure
    # instead of one discovered mid-run, after some cases have already been
    # billed.
    audio_base64: str
    expected: tuple[ExpectedExpense, ...]
    # A cheap deterministic proxy for "did it hear the words", without a
    # judge model (ADR-0014 §7: never call a judge where an exact check
    # exists) — every substring here must appear in the model's own
    # `transcript`, case-insensitively. Never empty: `all(...)` over an
    # empty sequence is vacuously `True`, which would make transcript_ok
    # pass without checking anything — load_voice_golden_cases rejects it.
    expected_transcript_contains: tuple[str, ...]


@dataclass(frozen=True)
class VoiceCaseScore:
    case_id: str
    schema_ok: bool
    count_exact: bool
    amount_exact: bool
    category_exact: bool
    date_exact: bool
    transcript_ok: bool
    cost_usd: Decimal | None
    latency_ms: int | None


@dataclass(frozen=True)
class VoiceModelResult:
    model: str
    total: int
    schema_ok: int
    count_exact: int
    amount_exact: int
    category_exact: int
    date_exact: int
    transcript_ok: int
    costs: tuple[Decimal, ...]
    latencies_ms: tuple[int, ...]

    @property
    def cost_mean(self) -> Decimal | None:
        if not self.costs:
            return None
        return sum(self.costs, Decimal("0")) / len(self.costs)

    @property
    def latency_p50_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.50)

    @property
    def latency_p95_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.95)


def _read_jsonl_lines(path: Path) -> list[str]:
    """A plain, synchronous read — kept out of `load_voice_golden_cases`'s
    own `async def` body for the same reason `evals.run._save_raw` is kept
    out of `run_case` (ASYNC240): a `pathlib.Path` method blocks the event
    loop even when nothing else in the function is waiting on I/O yet.
    """
    return path.read_text(encoding="utf-8").splitlines()


def _read_audio_bytes(audio_path: Path) -> bytes:
    """Same discipline as `_read_jsonl_lines` above, isolated to its own
    call so the `FileNotFoundError` it can raise stays exactly where the
    caller already expects to catch it.
    """
    return audio_path.read_bytes()


async def load_voice_golden_cases(
    path: Path, *, audio_dir: Path, ffmpeg_path: str = "ffmpeg", timeout_seconds: int = 30
) -> list[VoiceGoldenCase]:
    """One JSON object per line, `evals/golden/voice_v1.jsonl`'s own shape:
    `id`, `audio` (a filename read from `audio_dir`, git-ignored — ADR-0009),
    `expected` (identical shape to the text set), and
    `expected_transcript_contains` (a list of substrings, never empty).

    Converts every case's audio with `finbot.adapters.telegram.audio.
    convert_to_mp3` — the exact function `core.extraction.pipeline` calls
    for a real incoming voice note, imported directly rather than
    reimplemented here — before handing it to `voice.build_request`. An
    eval that skipped this step would send the model raw OGG/Opus labelled
    `"format": "mp3"` (`core.extraction.voice.AUDIO_FORMAT`), scoring a
    request production never actually sends (ADR-0014 §7: an eval with its
    own input preparation measures the harness, not the model, exactly as
    one with its own prompt or parser would).

    Reads and converts every case's audio file here, eagerly, for the same
    reason `load_eval_settings` checks `OPENROUTER_API_KEY` before any HTTP
    call: with a partial recorded set — the state the owner is in for as
    long as `evals/golden/voice/README.md` describes recording more — a
    missing file, or a broken `ffmpeg`, must fail before the first
    `client.complete`, not after some earlier cases have already been
    billed. `read_bytes()` is synchronous I/O; keeping it out of `evals.
    run`'s `async def run_voice_case` is the same discipline `_save_raw`'s
    own docstring states for writes. This function is itself `async def`
    only because `convert_to_mp3` is — `evals.run.main` awaits it once,
    not per case.
    """
    cases: list[VoiceGoldenCase] = []
    for line in _read_jsonl_lines(path):
        stripped = line.strip()
        if not stripped:
            continue
        payload: dict[str, Any] = json.loads(stripped, parse_float=Decimal)
        case_id = payload["id"]
        audio_filename = payload["audio"]
        audio_path = audio_dir / audio_filename
        try:
            audio_bytes = _read_audio_bytes(audio_path)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"{path}: case {case_id!r} needs {audio_path}, which does not exist — "
                f"see {audio_dir}/README.md for how to produce it"
            ) from exc

        try:
            mp3_bytes = await convert_to_mp3(
                audio_bytes, ffmpeg_path=ffmpeg_path, timeout_seconds=timeout_seconds
            )
        except AudioFetchError as exc:
            raise AudioFetchError(
                f"{path}: case {case_id!r}'s audio ({audio_path}) failed to convert to mp3 "
                f"with ffmpeg: {exc}"
            ) from exc

        transcript_contains = tuple(payload["expected_transcript_contains"])
        if not transcript_contains:
            msg = (
                f"{path}: golden case {case_id!r} has an empty "
                "expected_transcript_contains — all(...) over an empty sequence is "
                "vacuously True, so transcript_ok would pass without checking anything; "
                "name at least one substring the transcript must contain"
            )
            raise ValueError(msg)

        cases.append(
            VoiceGoldenCase(
                case_id=case_id,
                audio_filename=audio_filename,
                audio_base64=base64.b64encode(mp3_bytes).decode("ascii"),
                expected=_parse_expected(payload, path),
                expected_transcript_contains=transcript_contains,
            )
        )
    return cases


def score_voice_case(
    case: VoiceGoldenCase,
    today: date,
    resolved: VoiceExtractionResult,
    *,
    cost_usd: Decimal | None,
    latency_ms: int,
) -> VoiceCaseScore:
    """Mirrors `score_case` for `amount_exact`/`count_exact`/`category_exact`/
    `date_exact` — see that function's docstring — plus `transcript_ok`: all
    of `expected_transcript_contains` present in `resolved.transcript`,
    case-insensitively.
    """
    transcript_ok = all(
        substring.lower() in resolved.transcript.lower()
        for substring in case.expected_transcript_contains
    )

    actual = resolved.expenses
    count_exact = len(actual) == len(case.expected)
    if not count_exact:
        return VoiceCaseScore(
            case_id=case.case_id,
            schema_ok=True,
            count_exact=False,
            amount_exact=False,
            category_exact=False,
            date_exact=False,
            transcript_ok=transcript_ok,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    pairs = list(zip(actual, case.expected, strict=True))
    amount_exact = all(a.amount == e.amount for a, e in pairs)
    category_exact = all(a.category == e.category for a, e in pairs)
    date_exact = all(
        a.occurred_at == today + timedelta(days=e.occurred_offset_days) for a, e in pairs
    )
    return VoiceCaseScore(
        case_id=case.case_id,
        schema_ok=True,
        count_exact=True,
        amount_exact=amount_exact,
        category_exact=category_exact,
        date_exact=date_exact,
        transcript_ok=transcript_ok,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def failed_voice_case_score(
    case_id: str, *, cost_usd: Decimal | None, latency_ms: int | None
) -> VoiceCaseScore:
    """Mirrors `failed_case_score`: the response never became a usable
    `VoiceExtractionResult`, so every metric — `transcript_ok` included, since
    there is no transcript to check — is a miss.
    """
    return VoiceCaseScore(
        case_id=case_id,
        schema_ok=False,
        count_exact=False,
        amount_exact=False,
        category_exact=False,
        date_exact=False,
        transcript_ok=False,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def aggregate_voice(model: str, scores: list[VoiceCaseScore]) -> VoiceModelResult:
    return VoiceModelResult(
        model=model,
        total=len(scores),
        schema_ok=sum(score.schema_ok for score in scores),
        count_exact=sum(score.count_exact for score in scores),
        amount_exact=sum(score.amount_exact for score in scores),
        category_exact=sum(score.category_exact for score in scores),
        date_exact=sum(score.date_exact for score in scores),
        transcript_ok=sum(score.transcript_ok for score in scores),
        costs=tuple(score.cost_usd for score in scores if score.cost_usd is not None),
        latencies_ms=tuple(score.latency_ms for score in scores if score.latency_ms is not None),
    )


def render_voice_table(results: list[VoiceModelResult]) -> str:
    """Mirrors `render_table`, with `transcript_ok` alongside the four exact
    metrics text also has.
    """
    header = (
        "| model | schema_ok | count_exact | amount_exact | category_exact "
        "| date_exact | transcript_ok | mean cost (USD) | p50 latency (ms) "
        "| p95 latency (ms) |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|---|"
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
            f"| {result.transcript_ok}/{result.total} "
            f"| {cost} "
            f"| {result.latency_p50_ms} "
            f"| {result.latency_p95_ms} |"
        )
    return "\n".join(rows)


# --- bank screenshots (docs/plans/stage-2_5-bank-screenshots.md) --------------
#
# A third parallel set of hand-maintained shapes — same reasoning as the
# voice section above, and Approach F's own reasoning for why the case file
# itself is never one of them: `evals/golden/bank/README.md` documents the
# format, but `bank_v1.jsonl` and the screenshots it labels live outside
# this repository, loaded only through `load_bank_golden_cases` below.

# The five wire kinds a hand-labelled row may declare — the same five
# `BankRowKind` accepts on the wire. `unclassified` is a domain-only outcome
# the model's own schema cannot produce (`BankRowKind`'s own docstring), so a
# human labeller has no legitimate reason to write it either; a typo here
# (`"expenses"`, `"Expense"`) is exactly the kind of silent damage this set
# guards `_parse_bank_row` against.
_BANK_ROW_WIRE_KINDS = frozenset(k.value for k in BankRowKind if k is not BankRowKind.UNCLASSIFIED)


@dataclass(frozen=True)
class BankRowExpectation:
    """One hand-labelled row of `evals/golden/bank/README.md`'s case format.

    `category` and `occurred_offset_days` are only ever written for a
    `kind == "expense"` row — the format's own convention, mirrored here as
    `None` for every other kind. Every metric below that reads either field
    treats `None` as "not applicable to this row", never as a miss: judging
    a savings row's category would be judging something the row never had.
    """

    kind: str
    amount: Decimal
    partially_visible: bool
    category: str | None = None
    occurred_offset_days: int | None = None


@dataclass(frozen=True)
class BankGoldenCase:
    case_id: str
    image_filename: str
    # Read, sniffed and base64-encoded eagerly by `load_bank_golden_cases`,
    # through `finbot.adapters.telegram.images.to_data_url` itself — see
    # that import's own note above. Eager for the same reason `load_voice_
    # golden_cases` converts every case's audio upfront: a missing file or
    # an unrecognised image format must fail here, before the first
    # `client.complete`, not mid-run after earlier cases are already billed.
    image_data_url: str
    anchor_date: date
    is_transaction_feed: bool
    rows: tuple[BankRowExpectation, ...]


@dataclass(frozen=True)
class BankCaseScore:
    case_id: str
    schema_ok: bool
    feed_ok: bool
    count_exact: bool
    kind_exact: bool
    dropped_exact: bool
    category_exact: bool
    date_exact: bool
    expense_count_exact: bool
    amount_exact: bool
    # The gate this stage exists for (docs/plans/stage-2_5-bank-
    # screenshots.md, "The metric this step exists for"): deliberately
    # asymmetric, and scoreable even when count_exact does not hold — see
    # score_bank_case's own docstring.
    no_false_expense: bool
    cost_usd: Decimal | None
    latency_ms: int | None


@dataclass(frozen=True)
class BankModelResult:
    model: str
    total: int
    schema_ok: int
    feed_ok: int
    count_exact: int
    kind_exact: int
    dropped_exact: int
    category_exact: int
    date_exact: int
    expense_count_exact: int
    amount_exact: int
    no_false_expense: int
    costs: tuple[Decimal, ...]
    latencies_ms: tuple[int, ...]

    @property
    def cost_mean(self) -> Decimal | None:
        if not self.costs:
            return None
        return sum(self.costs, Decimal("0")) / len(self.costs)

    @property
    def latency_p50_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.50)

    @property
    def latency_p95_ms(self) -> int:
        return _nearest_rank_percentile(self.latencies_ms, 0.95)


def _read_image_bytes(image_path: Path) -> bytes:
    """Same discipline as `_read_audio_bytes` above, isolated to its own
    call so the `FileNotFoundError` it can raise stays exactly where the
    caller already expects to catch it.
    """
    return image_path.read_bytes()


def _parse_bank_row(row: dict[str, Any], *, case_id: str, path: Path) -> BankRowExpectation:
    kind = row["kind"]
    if kind not in _BANK_ROW_WIRE_KINDS:
        msg = (
            f"{path}: golden case {case_id!r} row kind must be one of "
            f"{sorted(_BANK_ROW_WIRE_KINDS)}, got {kind!r}"
        )
        raise TypeError(msg)

    amount = row["amount"]
    if not isinstance(amount, str):
        msg = (
            f"{path}: golden case {case_id!r} row amount must be a JSON string, "
            f"never a bare number, got {amount!r}"
        )
        raise TypeError(msg)

    # `category`/`occurred_offset_days` are the format's own convention
    # (evals/golden/bank/README.md): present exactly on an `expense` row,
    # omitted on every other kind. Enforced here, not merely documented, for
    # the same reason the kind check above is — a hand-typed case file is
    # exactly where this drifts unnoticed.
    has_category = "category" in row
    has_offset = "occurred_offset_days" in row
    is_expense = kind == BankRowKind.EXPENSE.value
    if is_expense and not (has_category and has_offset):
        msg = (
            f"{path}: golden case {case_id!r} expense row must set both "
            "'category' and 'occurred_offset_days'"
        )
        raise TypeError(msg)
    if not is_expense and (has_category or has_offset):
        msg = (
            f"{path}: golden case {case_id!r} non-expense row (kind={kind!r}) must omit "
            "'category' and 'occurred_offset_days'"
        )
        raise TypeError(msg)

    return BankRowExpectation(
        kind=kind,
        amount=Decimal(amount),
        partially_visible=bool(row["partially_visible"]),
        category=row.get("category"),
        occurred_offset_days=(
            int(row["occurred_offset_days"]) if "occurred_offset_days" in row else None
        ),
    )


def load_bank_golden_cases(path: Path, *, images_dir: Path) -> list[BankGoldenCase]:
    """One JSON object per line, `evals/golden/bank/README.md`'s own shape:
    `id`, `image` (a filename read from `images_dir`), `anchor_date`
    (**absolute**, ISO-8601 — the one place this stage diverges from the
    committed sets' run-date-relative rule, since a screenshot's headers are
    baked into its pixels; `--today` does not apply to `--modality bank`),
    `is_transaction_feed`, and `rows` (every visible row, in feed order,
    `evals.scoring.BankRowExpectation`'s own shape).

    **Both `path` and `images_dir` must resolve outside this repository** —
    `evals.paths.ensure_outside_repo`, the same ADR-0016 invariant
    `pull_voice_samples.py`'s `--out` enforces, applied here to a bank
    golden set's cases file and its image directory, because the label is
    exactly as private as the pixels (docs/plans/stage-2_5-bank-
    screenshots.md, Approach F).

    Reads and encodes every case's image here, eagerly, for the same reason
    `load_voice_golden_cases` converts every case's audio eagerly: a missing
    file, or an image that does not sniff as jpeg/png/webp, must fail before
    the first `client.complete`, not after some earlier cases have already
    been billed.
    """
    ensure_outside_repo(path, flag="--cases")
    ensure_outside_repo(images_dir, flag="--images-dir")

    cases: list[BankGoldenCase] = []
    for line in _read_jsonl_lines(path):
        stripped = line.strip()
        if not stripped:
            continue
        payload: dict[str, Any] = json.loads(stripped, parse_float=Decimal)
        case_id = payload["id"]
        image_filename = payload["image"]
        image_path = images_dir / image_filename
        try:
            image_bytes = _read_image_bytes(image_path)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"{path}: case {case_id!r} needs {image_path}, which does not exist — "
                f"see {images_dir}/README.md for the case format"
            ) from exc

        try:
            image_data_url = to_data_url(image_bytes)
        except ImageFetchError as exc:
            raise ImageFetchError(
                f"{path}: case {case_id!r}'s image ({image_path}) does not sniff as a "
                f"supported format (jpeg/png/webp): {exc}"
            ) from exc

        rows = tuple(_parse_bank_row(row, case_id=case_id, path=path) for row in payload["rows"])
        cases.append(
            BankGoldenCase(
                case_id=case_id,
                image_filename=image_filename,
                image_data_url=image_data_url,
                anchor_date=date.fromisoformat(payload["anchor_date"]),
                is_transaction_feed=bool(payload["is_transaction_feed"]),
                rows=rows,
            )
        )
    return cases


def _bank_row_would_write(row: BankRow, *, anchor: date) -> bool:
    """Whether `bank.plan_writes` would write this single row — the
    production classifier itself, called on a synthetic one-row result,
    never a second copy of its rules (ADR-0014 §7): the write decision that
    matters is always `bank.plan_writes`'s own.
    """
    single = BankExtractionResult(is_transaction_feed=True, rows=[row])
    return bool(bank.plan_writes(single, anchor=anchor).writes)


def score_bank_case(
    case: BankGoldenCase,
    result: BankExtractionResult,
    *,
    cost_usd: Decimal | None,
    latency_ms: int,
) -> BankCaseScore:
    """Score one already-parsed bank-extraction response against its case.

    `no_false_expense` — the metric this stage exists for — is **set-based
    and deliberately asymmetric**, computed independently of every
    positional metric below it: every amount `bank.plan_writes` would
    actually *write* must appear, with multiplicity, among this case's own
    `expense`-kind, fully-visible row amounts. `Counter.__le__` is exactly
    multiset inclusion, so `written <= allowed` is true when nothing was
    written that should not have been (the model may still *miss* an
    expense — `written` is then a strict subset — which is the named
    asymmetry: a missed expense is a nuisance, an over-written one destroys
    a report). Because it never zips model rows against case rows, it stays
    meaningful even when `count_exact` does not hold, which is exactly when
    every positional metric below goes blind.

    Every other metric is positional, gated on `count_exact` — mirroring
    `score_case`'s own docstring — except `feed_ok` (a single top-level
    field) and `expense_count_exact` (a total, not a pairing, so a miscount
    still gets a total-count signal `count_exact` cannot give it).
    `category_exact`/`date_exact`/`dropped_exact` are further scoped to
    rows this case actually labelled `expense`: category and date are
    meaningless for the other four kinds, and `dropped_exact` — "was a
    cut-off row correctly left unwritten" — has nothing to say about a row
    that was never going to be written anyway.
    """
    feed_ok = result.is_transaction_feed == case.is_transaction_feed

    plan = bank.plan_writes(result, anchor=case.anchor_date)
    expected_expense_rows = [
        row
        for row in case.rows
        if row.kind == BankRowKind.EXPENSE.value and not row.partially_visible
    ]
    expense_count_exact = len(plan.writes) == len(expected_expense_rows)

    allowed_amounts = Counter(row.amount for row in expected_expense_rows)
    written_amounts = Counter(write.draft.amount for write in plan.writes)
    no_false_expense = written_amounts <= allowed_amounts

    count_exact = len(result.rows) == len(case.rows)
    if count_exact:
        pairs = list(zip(result.rows, case.rows, strict=True))
        amount_exact = all(actual.amount == expected.amount for actual, expected in pairs)
        kind_exact = all(actual.kind.value == expected.kind for actual, expected in pairs)
        category_exact = all(
            expected.category is None or actual.category == expected.category
            for actual, expected in pairs
            if expected.kind == BankRowKind.EXPENSE.value
        )
        date_exact = all(
            expected.occurred_offset_days is None
            or bank_dates.resolve(actual.date_header, anchor=case.anchor_date)
            == case.anchor_date + timedelta(days=expected.occurred_offset_days)
            for actual, expected in pairs
            if expected.kind == BankRowKind.EXPENSE.value
        )
        dropped_exact = all(
            _bank_row_would_write(actual, anchor=case.anchor_date)
            == (not expected.partially_visible)
            for actual, expected in pairs
            if expected.kind == BankRowKind.EXPENSE.value
        )
    else:
        amount_exact = kind_exact = category_exact = date_exact = dropped_exact = False

    return BankCaseScore(
        case_id=case.case_id,
        schema_ok=True,
        feed_ok=feed_ok,
        count_exact=count_exact,
        kind_exact=kind_exact,
        dropped_exact=dropped_exact,
        category_exact=category_exact,
        date_exact=date_exact,
        expense_count_exact=expense_count_exact,
        amount_exact=amount_exact,
        no_false_expense=no_false_expense,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def failed_bank_case_score(
    case_id: str, *, cost_usd: Decimal | None, latency_ms: int | None
) -> BankCaseScore:
    """Mirrors `failed_case_score`: the response never became a usable
    `BankExtractionResult`, so every metric — `no_false_expense` included —
    is a miss. Nothing was ever classified, so there is nothing to trust.
    """
    return BankCaseScore(
        case_id=case_id,
        schema_ok=False,
        feed_ok=False,
        count_exact=False,
        kind_exact=False,
        dropped_exact=False,
        category_exact=False,
        date_exact=False,
        expense_count_exact=False,
        amount_exact=False,
        no_false_expense=False,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def aggregate_bank(model: str, scores: list[BankCaseScore]) -> BankModelResult:
    return BankModelResult(
        model=model,
        total=len(scores),
        schema_ok=sum(score.schema_ok for score in scores),
        feed_ok=sum(score.feed_ok for score in scores),
        count_exact=sum(score.count_exact for score in scores),
        kind_exact=sum(score.kind_exact for score in scores),
        dropped_exact=sum(score.dropped_exact for score in scores),
        category_exact=sum(score.category_exact for score in scores),
        date_exact=sum(score.date_exact for score in scores),
        expense_count_exact=sum(score.expense_count_exact for score in scores),
        amount_exact=sum(score.amount_exact for score in scores),
        no_false_expense=sum(score.no_false_expense for score in scores),
        costs=tuple(score.cost_usd for score in scores if score.cost_usd is not None),
        latencies_ms=tuple(score.latency_ms for score in scores if score.latency_ms is not None),
    )


def render_bank_table(results: list[BankModelResult]) -> str:
    """Mirrors `render_table`/`render_voice_table`: raw counts, never
    percentages. `no_false_expense` sits first among the accuracy columns,
    not last — Gate 1 (docs/plans/stage-2_5-bank-screenshots.md, Owner
    prerequisite 3) is read off this column before any other, and a model
    failing it is disqualified regardless of everything to its right.
    """
    header = (
        "| model | schema_ok | no_false_expense | feed_ok | count_exact | kind_exact "
        "| dropped_exact | expense_count_exact | amount_exact | category_exact "
        "| date_exact | mean cost (USD) | p50 latency (ms) | p95 latency (ms) |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [header, separator]
    for result in results:
        cost = f"{result.cost_mean:.6f}" if result.cost_mean is not None else "n/a"
        rows.append(
            f"| {result.model} "
            f"| {result.schema_ok}/{result.total} "
            f"| {result.no_false_expense}/{result.total} "
            f"| {result.feed_ok}/{result.total} "
            f"| {result.count_exact}/{result.total} "
            f"| {result.kind_exact}/{result.total} "
            f"| {result.dropped_exact}/{result.total} "
            f"| {result.expense_count_exact}/{result.total} "
            f"| {result.amount_exact}/{result.total} "
            f"| {result.category_exact}/{result.total} "
            f"| {result.date_exact}/{result.total} "
            f"| {cost} "
            f"| {result.latency_p50_ms} "
            f"| {result.latency_p95_ms} |"
        )
    return "\n".join(rows)
