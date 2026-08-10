"""Unit tests for evals.scoring — pure, deterministic, no network, no model.

Mirrors tests/unit/test_extraction_text.py's style: values in, values out.
"""

import base64
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from evals.scoring import (
    ExpectedExpense,
    GoldenCase,
    VoiceGoldenCase,
    aggregate,
    aggregate_voice,
    failed_case_score,
    failed_voice_case_score,
    load_golden_cases,
    load_voice_golden_cases,
    render_table,
    render_voice_table,
    score_case,
    score_voice_case,
)

from finbot.core.extraction.schema import ExpenseDraft, ExtractionResult, VoiceExtractionResult

_GOLDEN_PATH = Path(__file__).parents[2] / "evals" / "golden" / "text_v1.jsonl"
_VOICE_GOLDEN_PATH = Path(__file__).parents[2] / "evals" / "golden" / "voice_v1.jsonl"
_TODAY = date(2026, 8, 10)


def _draft(item: str, amount: str, category: str, occurred_at: date) -> ExpenseDraft:
    return ExpenseDraft(
        item=item, amount=Decimal(amount), category=category, occurred_at=occurred_at
    )


def _case(
    *expected: ExpectedExpense, raw_text: str = "irrelevant", case_id: str = "c"
) -> GoldenCase:
    return GoldenCase(case_id=case_id, raw_text=raw_text, expected=tuple(expected))


# --- load_golden_cases -------------------------------------------------------


def test_load_golden_cases_reads_all_eleven_from_the_real_golden_set() -> None:
    cases = load_golden_cases(_GOLDEN_PATH)
    assert len(cases) == 11
    assert [case.case_id for case in cases] == [
        "single-01",
        "multi-02",
        "multi-03",
        "relative-date-04",
        "words-05",
        "russian-06",
        "donation-07",
        "no-amount-08",
        "not-expense-09",
        "separators-10",
        "quantity-11",
    ]


def test_load_golden_cases_parses_amounts_as_decimal_never_float() -> None:
    cases = load_golden_cases(_GOLDEN_PATH)
    separators = next(case for case in cases if case.case_id == "separators-10")
    assert separators.expected[0].amount == Decimal("1250.50")
    assert isinstance(separators.expected[0].amount, Decimal)


def test_load_golden_cases_relative_date_case_is_offset_not_absolute() -> None:
    cases = load_golden_cases(_GOLDEN_PATH)
    relative = next(case for case in cases if case.case_id == "relative-date-04")
    assert relative.expected[0].occurred_offset_days == -1


def test_load_golden_cases_zero_expense_cases_have_an_empty_expected_list() -> None:
    cases = load_golden_cases(_GOLDEN_PATH)
    no_amount = next(case for case in cases if case.case_id == "no-amount-08")
    not_expense = next(case for case in cases if case.case_id == "not-expense-09")
    assert no_amount.expected == ()
    assert not_expense.expected == ()


# --- score_case ---------------------------------------------------------------


def test_score_case_all_exact_when_output_matches_expectation() -> None:
    case = _case(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0))
    resolved = ExtractionResult(expenses=[_draft("хліб", "50.00", "groceries", _TODAY)])

    score = score_case(case, _TODAY, resolved, cost_usd=Decimal("0.0001"), latency_ms=120)

    assert score.schema_ok
    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert score.date_exact
    assert score.cost_usd == Decimal("0.0001")
    assert score.latency_ms == 120


def test_score_case_count_mismatch_fails_every_comparison_metric() -> None:
    case = _case(
        ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
        ExpectedExpense("таксі", Decimal("200.00"), "transport", 0),
    )
    resolved = ExtractionResult(expenses=[_draft("хліб", "50.00", "groceries", _TODAY)])

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.schema_ok
    assert not score.count_exact
    assert not score.amount_exact
    assert not score.category_exact
    assert not score.date_exact


def test_score_case_wrong_amount_only_fails_amount_exact() -> None:
    case = _case(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0))
    resolved = ExtractionResult(expenses=[_draft("хліб", "55.00", "groceries", _TODAY)])

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.count_exact
    assert not score.amount_exact
    assert score.category_exact
    assert score.date_exact


def test_score_case_wrong_category_only_fails_category_exact() -> None:
    case = _case(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0))
    resolved = ExtractionResult(expenses=[_draft("хліб", "50.00", "other", _TODAY)])

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.count_exact
    assert score.amount_exact
    assert not score.category_exact
    assert score.date_exact


def test_score_case_wrong_date_only_fails_date_exact() -> None:
    case = _case(ExpectedExpense("таксі", Decimal("200.00"), "transport", -1))
    resolved = ExtractionResult(expenses=[_draft("таксі", "200.00", "transport", _TODAY)])

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert not score.date_exact


def test_score_case_empty_expected_and_empty_actual_is_exact_across_the_board() -> None:
    case = _case()
    resolved = ExtractionResult(expenses=[])

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert score.date_exact


def test_score_case_matches_pairs_positionally_not_by_content() -> None:
    """Two items, swapped order: count matches but nothing else does — the
    scorer must not silently sort either list before comparing.
    """
    case = _case(
        ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
        ExpectedExpense("таксі", Decimal("200.00"), "transport", 0),
    )
    resolved = ExtractionResult(
        expenses=[
            _draft("таксі", "200.00", "transport", _TODAY),
            _draft("хліб", "50.00", "groceries", _TODAY),
        ]
    )

    score = score_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.count_exact
    assert not score.amount_exact
    assert not score.category_exact


# --- failed_case_score ---------------------------------------------------------


def test_failed_case_score_marks_every_comparison_metric_false() -> None:
    score = failed_case_score("case-x", cost_usd=None, latency_ms=0)

    assert not score.schema_ok
    assert not score.count_exact
    assert not score.amount_exact
    assert not score.category_exact
    assert not score.date_exact
    assert score.case_id == "case-x"


# --- aggregate / ModelResult ----------------------------------------------------


def test_aggregate_sums_raw_counts_and_collects_costs_and_latencies() -> None:
    scores = [
        score_case(
            _case(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0)),
            _TODAY,
            ExtractionResult(expenses=[_draft("хліб", "50.00", "groceries", _TODAY)]),
            cost_usd=Decimal("0.0001"),
            latency_ms=100,
        ),
        failed_case_score("bad", cost_usd=None, latency_ms=50),
    ]

    result = aggregate("some/model", scores)

    assert result.model == "some/model"
    assert result.total == 2
    assert result.schema_ok == 1
    assert result.count_exact == 1
    assert result.amount_exact == 1
    assert result.category_exact == 1
    assert result.date_exact == 1
    assert result.costs == (Decimal("0.0001"),)
    assert result.latencies_ms == (100, 50)


def test_model_result_cost_mean_is_none_when_nothing_reported_a_cost() -> None:
    result = aggregate("m", [failed_case_score("a", cost_usd=None, latency_ms=0)])
    assert result.cost_mean is None


def test_model_result_cost_mean_averages_reported_costs() -> None:
    scores = [
        score_case(
            _case(),
            _TODAY,
            ExtractionResult(expenses=[]),
            cost_usd=Decimal("0.0002"),
            latency_ms=0,
        ),
        score_case(
            _case(), _TODAY, ExtractionResult(expenses=[]), cost_usd=Decimal("0.0004"), latency_ms=0
        ),
    ]
    result = aggregate("m", scores)
    assert result.cost_mean == Decimal("0.0003")


def test_model_result_latency_percentiles_use_nearest_rank() -> None:
    scores = [
        score_case(_case(), _TODAY, ExtractionResult(expenses=[]), cost_usd=None, latency_ms=ms)
        for ms in (10, 20, 30, 40, 50)
    ]
    result = aggregate("m", scores)
    # Nearest-rank on 5 sorted values: p50 -> ceil(0.5*5)=3rd value (30);
    # p95 -> ceil(0.95*5)=5th value (50).
    assert result.latency_p50_ms == 30
    assert result.latency_p95_ms == 50


def test_model_result_latency_percentiles_are_zero_with_no_samples() -> None:
    result = aggregate("m", [])
    assert result.latency_p50_ms == 0
    assert result.latency_p95_ms == 0
    assert result.total == 0


# --- render_table ---------------------------------------------------------------


def test_render_table_reports_raw_counts_not_percentages() -> None:
    result = aggregate(
        "cheap/model",
        [
            score_case(
                _case(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0)),
                _TODAY,
                ExtractionResult(expenses=[_draft("хліб", "50.00", "groceries", _TODAY)]),
                cost_usd=Decimal("0.00002"),
                latency_ms=100,
            ),
            failed_case_score("bad", cost_usd=None, latency_ms=200),
        ],
    )

    table = render_table([result])

    assert "cheap/model" in table
    assert "1/2" in table
    assert "%" not in table


def test_render_table_shows_n_a_when_no_cost_was_recorded() -> None:
    result = aggregate("m", [failed_case_score("a", cost_usd=None, latency_ms=0)])
    table = render_table([result])
    assert "n/a" in table


# --- voice (docs/roadmap.md Stage 2) ------------------------------------------


def _voice_case(
    *expected: ExpectedExpense,
    audio_filename: str = "irrelevant.oga",
    audio_base64: str = "aXJyZWxldmFudA==",  # "irrelevant"
    case_id: str = "c",
    expected_transcript_contains: tuple[str, ...] = (),
) -> VoiceGoldenCase:
    return VoiceGoldenCase(
        case_id=case_id,
        audio_filename=audio_filename,
        audio_base64=audio_base64,
        expected=tuple(expected),
        expected_transcript_contains=expected_transcript_contains,
    )


def _stub_audio_dir(tmp_path: Path, jsonl_path: Path) -> Path:
    """A placeholder (never real audio) file for every case a golden
    `.jsonl` references, named exactly as that case's own `audio` field —
    just enough for `load_voice_golden_cases` to succeed reading it without
    needing the owner's real recordings, which are git-ignored and absent
    on a fresh clone by design (ADR-0009).
    """
    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        (audio_dir / payload["audio"]).write_bytes(b"stub-audio-bytes")
    return audio_dir


def test_load_voice_golden_cases_reads_all_five_from_the_real_golden_set(tmp_path: Path) -> None:
    audio_dir = _stub_audio_dir(tmp_path, _VOICE_GOLDEN_PATH)
    cases = load_voice_golden_cases(_VOICE_GOLDEN_PATH, audio_dir=audio_dir)
    assert len(cases) == 5
    assert [case.case_id for case in cases] == [
        "single-01",
        "multi-02",
        "self-correction-03",
        "relative-date-04",
        "no-amount-05",
    ]


def test_load_voice_golden_cases_parses_amounts_as_decimal_never_float(tmp_path: Path) -> None:
    audio_dir = _stub_audio_dir(tmp_path, _VOICE_GOLDEN_PATH)
    cases = load_voice_golden_cases(_VOICE_GOLDEN_PATH, audio_dir=audio_dir)
    multi = next(case for case in cases if case.case_id == "multi-02")
    assert multi.expected[1].amount == Decimal("200.00")
    assert isinstance(multi.expected[1].amount, Decimal)


def test_load_voice_golden_cases_reads_the_audio_filename_and_transcript_substrings(
    tmp_path: Path,
) -> None:
    audio_dir = _stub_audio_dir(tmp_path, _VOICE_GOLDEN_PATH)
    cases = load_voice_golden_cases(_VOICE_GOLDEN_PATH, audio_dir=audio_dir)
    single = next(case for case in cases if case.case_id == "single-01")
    assert single.audio_filename == "single-01.oga"
    assert single.expected_transcript_contains == ("хліб", "50")


def test_load_voice_golden_cases_encodes_the_actual_audio_bytes_as_base64(tmp_path: Path) -> None:
    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    (audio_dir / "one.oga").write_bytes(b"real-bytes-stand-in")
    jsonl_path = tmp_path / "cases.jsonl"
    jsonl_path.write_text(
        '{"id": "c1", "audio": "one.oga", "expected": [], "expected_transcript_contains": ["x"]}\n',
        encoding="utf-8",
    )

    cases = load_voice_golden_cases(jsonl_path, audio_dir=audio_dir)

    assert base64.b64decode(cases[0].audio_base64) == b"real-bytes-stand-in"


def test_load_voice_golden_cases_raises_before_returning_when_audio_is_missing(
    tmp_path: Path,
) -> None:
    """The review's own scenario: a partially recorded set (audio/README.md
    describes exactly this state) must fail loudly, here, rather than the
    caller discovering it mid-run after billing earlier cases.
    """
    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    (audio_dir / "one.oga").write_bytes(b"present")
    # "two.oga" is deliberately never written.
    jsonl_path = tmp_path / "cases.jsonl"
    jsonl_path.write_text(
        '{"id": "c1", "audio": "one.oga", "expected": [], '
        '"expected_transcript_contains": ["x"]}\n'
        '{"id": "c2", "audio": "two.oga", "expected": [], '
        '"expected_transcript_contains": ["x"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="c2"):
        load_voice_golden_cases(jsonl_path, audio_dir=audio_dir)


def test_load_voice_golden_cases_rejects_an_empty_expected_transcript_contains(
    tmp_path: Path,
) -> None:
    """`all(...)` over an empty sequence is vacuously `True` — an empty
    `expected_transcript_contains` would make `transcript_ok` pass without
    checking anything.
    """
    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    (audio_dir / "one.oga").write_bytes(b"present")
    jsonl_path = tmp_path / "cases.jsonl"
    jsonl_path.write_text(
        '{"id": "c1", "audio": "one.oga", "expected": [], "expected_transcript_contains": []}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vacuously"):
        load_voice_golden_cases(jsonl_path, audio_dir=audio_dir)


def test_score_voice_case_all_exact_when_transcript_and_expenses_match() -> None:
    case = _voice_case(
        ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
        expected_transcript_contains=("хліб", "50"),
    )
    resolved = VoiceExtractionResult(
        transcript="хліб 50",
        expenses=[_draft("хліб", "50.00", "groceries", _TODAY)],
    )

    score = score_voice_case(case, _TODAY, resolved, cost_usd=Decimal("0.0002"), latency_ms=150)

    assert score.schema_ok
    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert score.date_exact
    assert score.transcript_ok
    assert score.cost_usd == Decimal("0.0002")
    assert score.latency_ms == 150


def test_score_voice_case_transcript_ok_is_case_insensitive() -> None:
    case = _voice_case(expected_transcript_contains=("ХЛІБ",))
    resolved = VoiceExtractionResult(transcript="хліб пʼятдесят", expenses=[])

    score = score_voice_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert score.transcript_ok


def test_score_voice_case_transcript_not_ok_when_a_substring_is_missing() -> None:
    case = _voice_case(expected_transcript_contains=("таксі",))
    resolved = VoiceExtractionResult(transcript="хліб пʼятдесят", expenses=[])

    score = score_voice_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert not score.transcript_ok


def test_score_voice_case_count_mismatch_still_scores_transcript_ok_independently() -> None:
    """`transcript_ok` does not depend on `count_exact` — the two are
    unrelated axes: the model can hear correctly and still miscount, or
    mishear and still land on the right count by luck.
    """
    case = _voice_case(
        ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
        ExpectedExpense("таксі", Decimal("200.00"), "transport", 0),
        expected_transcript_contains=("хліб",),
    )
    resolved = VoiceExtractionResult(
        transcript="хліб пʼятдесят", expenses=[_draft("хліб", "50.00", "groceries", _TODAY)]
    )

    score = score_voice_case(case, _TODAY, resolved, cost_usd=None, latency_ms=0)

    assert not score.count_exact
    assert not score.amount_exact
    assert score.transcript_ok


def test_failed_voice_case_score_marks_every_comparison_metric_false() -> None:
    score = failed_voice_case_score("case-x", cost_usd=None, latency_ms=0)

    assert not score.schema_ok
    assert not score.count_exact
    assert not score.amount_exact
    assert not score.category_exact
    assert not score.date_exact
    assert not score.transcript_ok
    assert score.case_id == "case-x"


def test_aggregate_voice_sums_transcript_ok_alongside_the_exact_metrics() -> None:
    scores = [
        score_voice_case(
            _voice_case(
                ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
                expected_transcript_contains=("хліб",),
            ),
            _TODAY,
            VoiceExtractionResult(
                transcript="хліб 50", expenses=[_draft("хліб", "50.00", "groceries", _TODAY)]
            ),
            cost_usd=Decimal("0.0001"),
            latency_ms=100,
        ),
        failed_voice_case_score("bad", cost_usd=None, latency_ms=50),
    ]

    result = aggregate_voice("some/model", scores)

    assert result.model == "some/model"
    assert result.total == 2
    assert result.schema_ok == 1
    assert result.transcript_ok == 1
    assert result.costs == (Decimal("0.0001"),)
    assert result.latencies_ms == (100, 50)


def test_render_voice_table_reports_raw_counts_and_the_transcript_ok_column() -> None:
    result = aggregate_voice(
        "cheap/model",
        [
            score_voice_case(
                _voice_case(
                    ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
                    expected_transcript_contains=("хліб",),
                ),
                _TODAY,
                VoiceExtractionResult(
                    transcript="хліб 50", expenses=[_draft("хліб", "50.00", "groceries", _TODAY)]
                ),
                cost_usd=Decimal("0.00002"),
                latency_ms=100,
            ),
            failed_voice_case_score("bad", cost_usd=None, latency_ms=200),
        ],
    )

    table = render_voice_table([result])

    assert "cheap/model" in table
    assert "transcript_ok" in table
    assert "1/2" in table
    assert "%" not in table
