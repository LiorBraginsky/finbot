"""Unit tests for evals.run — no network, no model, no Docker.

Verification without spending a cent: `FakeLlmClient` (tests/support/
fake_llm.py) replays the checked-in OpenRouter response bodies
(tests/fixtures/openrouter/) through `finbot.llm.openrouter.
parse_response_body`, the exact function the real client calls, and
`run_case`/`run_model` are exercised against it exactly as `main()` would
exercise the real `OpenRouterClient` — the seam is `LlmClient`, and neither
function can tell the difference.
"""

import base64
import json
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from evals.run import (
    _DEFAULT_CASES_PATH,
    _DEFAULT_VOICE_CASES_PATH,
    MissingApiKeyError,
    _parse_args,
    load_eval_settings,
    run_case,
    run_model,
    run_voice_case,
)
from evals.scoring import (
    ExpectedExpense,
    GoldenCase,
    VoiceGoldenCase,
    load_golden_cases,
    render_table,
)

from finbot.config import Settings as BotSettings
from finbot.core.extraction.ports import LlmError
from finbot.core.money import loads_decimal
from tests.support.fake_llm import FakeLlmClient

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_TODAY = date(2026, 8, 10)
_GOLDEN_PATH = Path(__file__).parents[2] / "evals" / "golden" / "text_v1.jsonl"


def _fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


def _content_json(expected: Sequence[ExpectedExpense], today: date) -> str:
    """Builds a well-formed assistant `content` document whose `amount` is
    the expectation's own `Decimal`, interpolated as a bare JSON number
    literal — never through a Python `float` — so a "perfect" fake response
    is exact by construction, the same discipline `core.money.loads_decimal`
    enforces on the real wire.
    """
    items = [
        f'{{"item": {json.dumps(e.item)}, "amount": {e.amount}, '
        f'"category": {json.dumps(e.category)}, '
        f'"occurred_at": "{(today + timedelta(days=e.occurred_offset_days)).isoformat()}"}}'
        for e in expected
    ]
    return '{"expenses": [' + ", ".join(items) + "]}"


def _response_body(*, model: str, content: str, cost: str | None) -> str:
    """A raw OpenRouter response body around an arbitrary `content` payload.
    `cost` is spliced in as a raw literal (`null` or a bare number) rather
    than through a Python float, for the same reason.
    """
    sentinel = "__COST__"
    body = {
        "id": "gen-test",
        "model": model,
        "object": "chat.completion",
        "created": 0,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": sentinel},
    }
    text = json.dumps(body, ensure_ascii=False)
    literal = "null" if cost is None else cost
    return text.replace(f'"{sentinel}"', literal)


# --- run_case ------------------------------------------------------------------


async def test_run_case_scores_a_recorded_two_item_fixture_exact() -> None:
    case = GoldenCase(
        case_id="multi",
        raw_text="хліб 50, таксі 200",
        expected=(
            ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),
            ExpectedExpense("таксі", Decimal("200.00"), "transport", 0),
        ),
    )
    client = FakeLlmClient(_fixture("ok_two_items"))

    score = await run_case(client, "google/gemini-3.5-flash-lite", case, _TODAY)

    assert score.schema_ok
    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert score.date_exact
    assert score.cost_usd == Decimal("0.000123")
    assert score.latency_ms == 0


async def test_run_case_sends_only_the_one_model_under_test() -> None:
    """Regression: build_request must be called with `models=(model,)`, not
    with a fallback list — an eval measures one model at a time.
    """
    case = GoldenCase(case_id="c", raw_text="хліб 50", expected=())
    client = FakeLlmClient(_fixture("ok_empty"))

    await run_case(client, "openai/gpt-oss-20b", case, _TODAY)

    assert client.requests[0].models == ("openai/gpt-oss-20b",)


async def test_run_case_treats_invalid_json_content_as_a_failed_case() -> None:
    case = GoldenCase(case_id="c", raw_text="whatever", expected=())
    client = FakeLlmClient(_fixture("invalid_json"))

    score = await run_case(client, "openai/gpt-5.6-luna", case, _TODAY)

    assert not score.schema_ok
    assert not score.count_exact
    assert not score.amount_exact
    assert not score.category_exact
    assert not score.date_exact
    # The call still cost money and took time even though it was unusable —
    # both are still recorded.
    assert score.cost_usd == Decimal("0.0000891")


async def test_run_case_treats_a_transport_error_as_a_failed_case_with_no_cost() -> None:
    case = GoldenCase(case_id="c", raw_text="whatever", expected=())
    client = FakeLlmClient(LlmError("boom", raw={"error": "boom"}))

    score = await run_case(client, "openai/gpt-5.6-luna", case, _TODAY)

    assert not score.schema_ok
    assert score.cost_usd is None
    # None, not 0: no response ever came back, so there is no real duration
    # to report — 0 would make a timing-out model look like the fastest one
    # in the very percentile (`aggregate`'s `latencies_ms`) that is supposed
    # to catch that.
    assert score.latency_ms is None


async def test_run_case_tolerates_a_null_cost() -> None:
    case = GoldenCase(
        case_id="c",
        raw_text="хліб",
        expected=(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),),
    )
    client = FakeLlmClient(_fixture("no_cost"))

    score = await run_case(client, "qwen/qwen3.7-flash", case, _TODAY)

    assert score.schema_ok
    assert score.cost_usd is None


async def test_run_case_writes_the_raw_response_body_verbatim_when_save_raw_is_given(
    tmp_path: Path,
) -> None:
    """Byte-for-byte the wire text, never a re-serialization through
    `json.dumps(response.raw, default=str)` — which would have rendered the
    fixture's `Decimal` cost as the *string* `"0.000123"`, corrupting the
    very fixtures `--save-raw` exists to refresh.
    """
    case = GoldenCase(case_id="two-items-case", raw_text="хліб 50, таксі 200", expected=())
    client = FakeLlmClient(_fixture("ok_two_items"))

    await run_case(
        client,
        "google/gemini-3.5-flash-lite",
        case,
        _TODAY,
        save_raw=tmp_path,
        repeat_index=0,
    )

    written = list(tmp_path.glob("*.json"))  # noqa: ASYNC240 -- test-only, tmp_path is in-memory-fast
    assert len(written) == 1
    assert written[0].name == "google_gemini-3.5-flash-lite__two-items-case__1.json"
    saved_text = written[0].read_text(encoding="utf-8")
    assert saved_text == _fixture("ok_two_items")
    saved = loads_decimal(saved_text)
    assert saved["usage"]["cost"] == Decimal("0.000123")


async def test_run_case_does_not_write_anything_when_save_raw_is_none() -> None:
    case = GoldenCase(case_id="c", raw_text="хліб", expected=())
    client = FakeLlmClient(_fixture("ok_empty"))

    # No exception, no filesystem access — save_raw defaults to None.
    await run_case(client, "google/gemini-3.5-flash-lite", case, _TODAY)


# --- run_model -------------------------------------------------------------------


async def test_run_model_aggregates_across_repeats() -> None:
    case = GoldenCase(
        case_id="c",
        raw_text="хліб 50",
        expected=(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),),
    )
    body = _response_body(model="m", content=_content_json(case.expected, _TODAY), cost="0.0001")
    client = FakeLlmClient(body, body)

    result = await run_model(client, "m", [case], _TODAY, repeats=2)

    assert result.total == 2
    assert result.schema_ok == 2
    assert result.amount_exact == 2
    assert result.costs == (Decimal("0.0001"), Decimal("0.0001"))


async def test_run_model_scores_a_perfect_model_eleven_of_eleven_on_the_real_golden_set() -> None:
    """Also doubles as a self-consistency check on evals/golden/text_v1.jsonl
    itself: if a model reproduces every case's `expected` exactly, every
    metric must read 11/11 — a wrong pairing in the golden file or a scoring
    bug would show up here as something less than perfect.
    """
    cases = load_golden_cases(_GOLDEN_PATH)
    bodies = [
        _response_body(
            model="control/model", content=_content_json(case.expected, _TODAY), cost="0.001"
        )
        for case in cases
    ]
    client = FakeLlmClient(*bodies)

    result = await run_model(client, "control/model", cases, _TODAY)

    assert result.total == 11
    assert result.schema_ok == 11
    assert result.count_exact == 11
    assert result.amount_exact == 11
    assert result.category_exact == 11
    assert result.date_exact == 11

    table = render_table([result])
    assert "control/model" in table
    assert "11/11" in table
    assert "%" not in table


async def test_run_model_catches_a_wrong_category_without_failing_other_metrics() -> None:
    cases = load_golden_cases(_GOLDEN_PATH)
    bodies = []
    for case in cases:
        if case.case_id == "donation-07":
            # Model files the donation under the wrong category.
            wrong = tuple(
                ExpectedExpense(e.item, e.amount, "other", e.occurred_offset_days)
                for e in case.expected
            )
            bodies.append(
                _response_body(model="m", content=_content_json(wrong, _TODAY), cost="0.001")
            )
        else:
            bodies.append(
                _response_body(
                    model="m", content=_content_json(case.expected, _TODAY), cost="0.001"
                )
            )
    client = FakeLlmClient(*bodies)

    result = await run_model(client, "m", cases, _TODAY)

    assert result.total == 11
    assert result.schema_ok == 11
    assert result.count_exact == 11
    assert result.amount_exact == 11
    assert result.category_exact == 10
    assert result.date_exact == 11


# --- load_eval_settings ----------------------------------------------------------


def test_load_eval_settings_raises_missing_api_key_error_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError):
        load_eval_settings(env_file=tmp_path / "no-such-file.env")


def test_load_eval_settings_succeeds_from_an_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake-not-a-real-key")
    # tests/conftest.py's session-wide guard forces OPENROUTER_BASE_URL to the
    # discard port for the whole suite; unset it here to see EvalSettings'
    # own default rather than the guard's.
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    settings = load_eval_settings(env_file=tmp_path / "no-such-file.env")

    assert settings.openrouter_api_key.get_secret_value() == "sk-or-fake-not-a-real-key"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_timeout_seconds == 60


def test_eval_settings_ffmpeg_timeout_default_matches_the_bots_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins "reuse Settings.ffmpeg_timeout_seconds's default so the eval and
    the bot agree" mechanically: this must read `finbot.config.Settings`'s
    own field, never a second hand-copied literal `30` that could drift
    from it silently.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake-not-a-real-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    settings = load_eval_settings(env_file=tmp_path / "no-such-file.env")

    assert (
        settings.ffmpeg_timeout_seconds
        == BotSettings.model_fields["ffmpeg_timeout_seconds"].default
    )


# --- _parse_args -----------------------------------------------------------------


def test_parse_args_splits_comma_separated_models() -> None:
    args = _parse_args(["--models", "a,b,c"])
    assert args.models == "a,b,c"


def test_parse_args_defaults() -> None:
    args = _parse_args(["--models", "a"])
    assert args.cases == _DEFAULT_CASES_PATH
    assert args.repeats == 1
    assert args.save_raw is None
    assert args.today is None


def test_parse_args_accepts_an_explicit_today_override() -> None:
    args = _parse_args(["--models", "a", "--today", "2026-08-10"])
    assert args.today == date(2026, 8, 10)


def test_parse_args_modality_defaults_to_text() -> None:
    args = _parse_args(["--models", "a"])
    assert args.modality == "text"


def test_parse_args_accepts_modality_voice() -> None:
    args = _parse_args(["--models", "a", "--modality", "voice"])
    assert args.modality == "voice"


def test_parse_args_rejects_an_unknown_modality() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--models", "a", "--modality", "photo"])


def test_parse_args_ffmpeg_defaults_to_resolving_from_path() -> None:
    args = _parse_args(["--models", "a"])
    assert args.ffmpeg_path == "ffmpeg"


def test_parse_args_accepts_an_explicit_ffmpeg_path() -> None:
    args = _parse_args(["--models", "a", "--ffmpeg", "/opt/homebrew/bin/ffmpeg"])
    assert args.ffmpeg_path == "/opt/homebrew/bin/ffmpeg"


# --- run_voice_case ----------------------------------------------------------


def _voice_response_body(
    *, model: str, transcript: str, expenses: list[dict], cost: float | None
) -> str:
    content = json.dumps({"transcript": transcript, "expenses": expenses}, ensure_ascii=False)
    assistant_message = {"role": "assistant", "content": content}
    return json.dumps(
        {
            "id": "gen-voice-test",
            "model": model,
            "object": "chat.completion",
            "created": 0,
            "choices": [{"index": 0, "message": assistant_message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": cost},
        }
    )


def _voice_case(
    *,
    case_id: str = "v1",
    audio_filename: str = "case.oga",
    audio_base64: str = "aXJyZWxldmFudA==",  # "irrelevant"
    expected: tuple[ExpectedExpense, ...] = (),
    expected_transcript_contains: tuple[str, ...] = ("хліб",),
) -> VoiceGoldenCase:
    return VoiceGoldenCase(
        case_id=case_id,
        audio_filename=audio_filename,
        audio_base64=audio_base64,
        expected=expected,
        expected_transcript_contains=expected_transcript_contains,
    )


async def test_run_voice_case_scores_a_matching_transcript_and_expense_exact() -> None:
    case = _voice_case(
        expected=(ExpectedExpense("хліб", Decimal("50.00"), "groceries", 0),),
        expected_transcript_contains=("хліб", "50"),
    )
    body = _voice_response_body(
        model="google/gemini-2.5-flash",
        transcript="хліб 50",
        expenses=[{"item": "хліб", "amount": 50, "category": "groceries", "occurred_at": None}],
        cost=0.0002,
    )
    client = FakeLlmClient(body)

    score = await run_voice_case(client, "google/gemini-2.5-flash", case, _TODAY)

    assert score.schema_ok
    assert score.count_exact
    assert score.amount_exact
    assert score.category_exact
    assert score.date_exact
    assert score.transcript_ok
    assert score.cost_usd == Decimal("0.0002")


async def test_run_voice_case_sends_the_case_audio_as_base64_input_audio() -> None:
    case = _voice_case(audio_base64=base64.b64encode(b"raw-audio-bytes").decode("ascii"))
    client = FakeLlmClient(_voice_response_body(model="m", transcript="", expenses=[], cost=None))

    await run_voice_case(client, "m", case, _TODAY)

    sent_content = client.requests[0].messages[-1]["content"]
    assert sent_content[0]["type"] == "input_audio"
    assert sent_content[0]["input_audio"]["data"] == base64.b64encode(b"raw-audio-bytes").decode(
        "ascii"
    )


async def test_run_voice_case_treats_a_transport_error_as_a_failed_case() -> None:
    case = _voice_case()
    client = FakeLlmClient(LlmError("boom", raw={"error": "boom"}))

    score = await run_voice_case(client, "m", case, _TODAY)

    assert not score.schema_ok
    assert not score.transcript_ok
    assert score.cost_usd is None


def test_default_voice_cases_path_points_at_the_committed_voice_golden_set() -> None:
    assert _DEFAULT_VOICE_CASES_PATH.name == "voice_v1.jsonl"
    assert _DEFAULT_VOICE_CASES_PATH.exists()
