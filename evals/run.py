"""Runs the golden set against real models through the production code path
and prints a model x accuracy x cost x latency table.

It calls exactly two production modules — `finbot.core.extraction.text`
(build the request, parse and date-resolve the response) and
`finbot.llm.openrouter` (perform the HTTP call) — the same two Step 2 wired
into `core.extraction.pipeline`. Nothing here has its own prompt or its own
parser: an eval with either would measure the harness, not the model.

Never a gate. Costs real money per call and its results vary between runs —
see `evals/README.md` and ADR-0014.

    python -m evals.run --models mistralai/mistral-nemo,google/gemini-3.6-flash

Fails fast, before opening a socket, when `OPENROUTER_API_KEY` is absent —
see `load_eval_settings`. Verified without spending anything by feeding a
`FakeLlmClient` (`tests/support/fake_llm.py`) recorded response bodies
(`tests/unit/test_evals_run.py`); `run_model` and `render_table` never know
the difference between that and the real `OpenRouterClient`, because both
satisfy the same `LlmClient` protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from evals.scoring import (
    CaseScore,
    GoldenCase,
    ModelResult,
    aggregate,
    failed_case_score,
    load_golden_cases,
    render_table,
    score_case,
)
from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.ports import LlmClient, LlmError
from finbot.core.extraction.text import (
    ExtractionInvalidError,
    build_request,
    parse_content,
    resolve_dates,
)
from finbot.llm.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

_DEFAULT_CASES_PATH = Path(__file__).parent / "golden" / "text_v1.jsonl"
# Matches adapters/telegram/main.py's own default: "today" for date-offset
# cases is a Kyiv-local calendar date, not a UTC one.
_DEFAULT_TZ = ZoneInfo("Europe/Kyiv")


class EvalSettings(BaseSettings):
    """Only what this runner needs from `.env` — never the bot's Telegram or
    database configuration, so `python -m evals.run` works without any of
    that being set.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    openrouter_api_key: SecretStr
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_timeout_seconds: int = 60


class MissingApiKeyError(RuntimeError):
    """`OPENROUTER_API_KEY` is absent from the environment and from `.env`."""


def load_eval_settings(*, env_file: str | Path | None = ".env") -> EvalSettings:
    """Raises `MissingApiKeyError` with one clear line — never a raw
    `ValidationError` — so a missing key fails before any HTTP call is even
    attempted, rather than surfacing as a confusing 401 from OpenRouter.
    """
    try:
        return EvalSettings(_env_file=env_file)
    except ValidationError as exc:
        raise MissingApiKeyError(
            "OPENROUTER_API_KEY is not set. Put it in .env or export it before "
            "running `python -m evals.run`."
        ) from exc


def _raw_filename(model: str, case_id: str, repeat_index: int) -> str:
    slug = model.replace("/", "_")
    return f"{slug}__{case_id}__{repeat_index + 1}.json"


def _save_raw(save_raw: Path, model: str, case_id: str, repeat_index: int, raw: Any) -> None:
    """A plain, synchronous write — kept out of `run_case` so an async
    function never calls a `pathlib.Path` method directly (ASYNC240). Only
    used with `--save-raw`, an owner-run refresh of `tests/fixtures/openrouter/`
    (docs/plans/stage-1-text-to-expense.md's owner prerequisite 6), never in
    the normal scoring path.
    """
    save_raw.mkdir(parents=True, exist_ok=True)
    raw_path = save_raw / _raw_filename(model, case_id, repeat_index)
    raw_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )


async def run_case(
    client: LlmClient,
    model: str,
    case: GoldenCase,
    today: date,
    *,
    save_raw: Path | None = None,
    repeat_index: int = 0,
) -> CaseScore:
    """One call, scored. No repair loop: `schema_ok` means valid on the
    first attempt (the plan's stated criterion) — repairing here would
    silently turn a real "invalid on attempt 1" into a hidden pass.
    """
    request = build_request(raw_text=case.raw_text, today=today, catalog=CATALOG, models=(model,))
    try:
        response = await client.complete(request)
    except LlmError as exc:
        logger.warning("model %s errored on case %s: %s", model, case.case_id, exc)
        return failed_case_score(case.case_id, cost_usd=None, latency_ms=0)

    if save_raw is not None:
        _save_raw(save_raw, model, case.case_id, repeat_index, response.raw)

    try:
        result = parse_content(response.content)
    except ExtractionInvalidError as exc:
        logger.warning("model %s produced invalid output on case %s: %s", model, case.case_id, exc)
        return failed_case_score(
            case.case_id, cost_usd=response.cost_usd, latency_ms=response.latency_ms
        )

    resolved = resolve_dates(result, today)
    return score_case(
        case, today, resolved, cost_usd=response.cost_usd, latency_ms=response.latency_ms
    )


async def run_model(
    client: LlmClient,
    model: str,
    cases: Sequence[GoldenCase],
    today: date,
    *,
    repeats: int = 1,
    save_raw: Path | None = None,
) -> ModelResult:
    scores = [
        await run_case(client, model, case, today, save_raw=save_raw, repeat_index=repeat_index)
        for case in cases
        for repeat_index in range(repeats)
    ]
    return aggregate(model, scores)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Score OpenRouter models against the golden set through the production "
        "extraction code path.",
    )
    parser.add_argument(
        "--models", required=True, help="comma-separated OpenRouter model ids, e.g. a,b,c"
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=_DEFAULT_CASES_PATH,
        help=f"path to a golden .jsonl file (default: {_DEFAULT_CASES_PATH})",
    )
    parser.add_argument(
        "--repeats", type=int, default=1, help="calls per case per model (default: 1)"
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        dest="save_raw",
        help="directory to write every raw response body into, for refreshing "
        "tests/fixtures/openrouter/",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="override the run date occurred_offset_days resolves against "
        "(default: today in Europe/Kyiv)",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)

    try:
        settings = load_eval_settings()
    except MissingApiKeyError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201 -- CLI error, not a debug print
        raise SystemExit(1) from exc

    models = [model.strip() for model in args.models.split(",") if model.strip()]
    if not models:
        print("--models must name at least one OpenRouter model id", file=sys.stderr)  # noqa: T201
        raise SystemExit(1)

    cases = load_golden_cases(args.cases)
    today = args.today or datetime.now(tz=_DEFAULT_TZ).date()

    async with aiohttp.ClientSession() as http:
        client = OpenRouterClient(
            session=http,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        results = [
            await run_model(
                client, model, cases, today, repeats=args.repeats, save_raw=args.save_raw
            )
            for model in models
        ]

    print(render_table(results))  # noqa: T201 -- the whole point of this CLI is this line


if __name__ == "__main__":
    asyncio.run(main())
