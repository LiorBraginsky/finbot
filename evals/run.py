"""Runs the golden set against real models through the production code path
and prints a model x accuracy x cost x latency table.

It calls exactly two production modules — `finbot.core.extraction.text`
(build the request, parse and date-resolve the response) and
`finbot.llm.openrouter` (perform the HTTP call) — the same two Step 2 wired
into `core.extraction.pipeline`. Nothing here has its own prompt or its own
parser: an eval with either would measure the harness, not the model.
`--modality voice` extends this the one place it needs to: golden audio is
converted with `finbot.adapters.telegram.audio.convert_to_mp3` itself
(`evals.scoring.load_voice_golden_cases`), never a second implementation —
the same rule applied to input preparation, not just the request/response
path (ADR-0014 §7). `--modality bank` (docs/plans/stage-2_5-bank-
screenshots.md) extends it once more: `--cases`/`--images-dir` are required,
with no default, and refused when they resolve inside this repository
(`evals.paths.ensure_outside_repo`) — the case labels are real household
screenshots, as private as the pixels (Approach F) — and `--save-raw` is
refused outright for this modality, since there are no synthetic bank cases
to refresh a fixture from (ADR-0012's Stage-1 amendment).

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
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from evals.scoring import (
    BankCaseScore,
    BankGoldenCase,
    BankModelResult,
    CaseScore,
    GoldenCase,
    ModelResult,
    VoiceCaseScore,
    VoiceGoldenCase,
    VoiceModelResult,
    aggregate,
    aggregate_bank,
    aggregate_voice,
    failed_bank_case_score,
    failed_case_score,
    failed_voice_case_score,
    load_bank_golden_cases,
    load_golden_cases,
    load_voice_golden_cases,
    render_bank_table,
    render_table,
    render_voice_table,
    score_bank_case,
    score_case,
    score_voice_case,
)
from finbot.config import Settings as _BotSettings
from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction import bank as bank_extraction
from finbot.core.extraction import voice as voice_extraction
from finbot.core.extraction.common import ExtractionInvalidError
from finbot.core.extraction.ports import AudioFetchError, ImageFetchError, LlmClient, LlmError
from finbot.core.extraction.text import build_request, parse_content, resolve_dates
from finbot.llm.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

_DEFAULT_CASES_PATH = Path(__file__).parent / "golden" / "text_v1.jsonl"
_DEFAULT_VOICE_CASES_PATH = Path(__file__).parent / "golden" / "voice_v1.jsonl"
_DEFAULT_VOICE_AUDIO_DIR = Path(__file__).parent / "golden" / "voice"
# Matches adapters/telegram/main.py's own default: "today" for date-offset
# cases is a Kyiv-local calendar date, not a UTC one.
_DEFAULT_TZ = ZoneInfo("Europe/Kyiv")

# Errors `load_bank_golden_cases` can raise while preparing a case, all
# meant to fail the run with one clear line rather than a raw traceback —
# `RepoPathError` is a `ValueError` subclass (evals.paths), so catching
# `ValueError` also covers it, a missing `anchor_date`/`rows` key raises
# `KeyError`, and a malformed `amount` raises `TypeError`.
_BANK_LOAD_ERRORS = (FileNotFoundError, ImageFetchError, KeyError, TypeError, ValueError)


class EvalSettings(BaseSettings):
    """Only what this runner needs from `.env` — never the bot's Telegram or
    database configuration, so `python -m evals.run` works without any of
    that being set.

    `ffmpeg_timeout_seconds` is the one exception to "never the bot's ...
    configuration" above, and deliberately so: its *default* is read off
    `finbot.config.Settings` itself (never duplicated as a second literal
    `30`), because `--modality voice` converts golden audio with the same
    `ffmpeg` deadline discipline the bot applies on the drain path — see
    that field's own docstring for why the deadline exists at all. Still
    independently overridable here, for a machine whose `ffmpeg` is simply
    slower, without touching the bot's own setting.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    openrouter_api_key: SecretStr
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_timeout_seconds: int = 60
    ffmpeg_timeout_seconds: int = _BotSettings.model_fields["ffmpeg_timeout_seconds"].default


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


def _save_raw(save_raw: Path, model: str, case_id: str, repeat_index: int, raw_text: str) -> None:
    """A plain, synchronous write — kept out of `run_case` so an async
    function never calls a `pathlib.Path` method directly (ASYNC240). Only
    used with `--save-raw`, an owner-run refresh of `tests/fixtures/openrouter/`
    (docs/plans/stage-1-text-to-expense.md's owner prerequisite 6), never in
    the normal scoring path.

    Writes `raw_text` — the untouched wire body — verbatim, never
    `json.dumps(response.raw, ...)`: `response.raw` is already parsed
    through `core.money.loads_decimal`, so re-serializing it with
    `default=str` would render every `Decimal` (e.g. `usage.cost`) as a
    *string*, corrupting the very fixtures this flag exists to refresh.
    """
    save_raw.mkdir(parents=True, exist_ok=True)
    raw_path = save_raw / _raw_filename(model, case_id, repeat_index)
    raw_path.write_text(raw_text, encoding="utf-8")


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
        # Not always None: a malformed 200 can still carry a legible
        # usage.cost next to whatever else is wrong with the body — see
        # llm/openrouter.py's parse_response_body.
        return failed_case_score(case.case_id, cost_usd=exc.cost_usd, latency_ms=None)

    if save_raw is not None:
        _save_raw(save_raw, model, case.case_id, repeat_index, response.raw_text)

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


async def run_voice_case(
    client: LlmClient,
    model: str,
    case: VoiceGoldenCase,
    today: date,
    *,
    save_raw: Path | None = None,
    repeat_index: int = 0,
) -> VoiceCaseScore:
    """Mirrors `run_case` — one call, no repair loop, same reasoning.

    `case.audio_base64` is already read and encoded by `load_voice_golden_
    cases`, eagerly, for every case, before this function or `client.
    complete` is ever called — so a missing audio file fails fast, before
    anything is billed, rather than mid-run after some cases already were.
    """
    request = voice_extraction.build_request(
        audio_base64=case.audio_base64, today=today, catalog=CATALOG, models=(model,)
    )
    try:
        response = await client.complete(request)
    except LlmError as exc:
        logger.warning("model %s errored on case %s: %s", model, case.case_id, exc)
        return failed_voice_case_score(case.case_id, cost_usd=exc.cost_usd, latency_ms=None)

    if save_raw is not None:
        _save_raw(save_raw, model, case.case_id, repeat_index, response.raw_text)

    try:
        result = voice_extraction.parse_content(response.content)
    except ExtractionInvalidError as exc:
        logger.warning("model %s produced invalid output on case %s: %s", model, case.case_id, exc)
        return failed_voice_case_score(
            case.case_id, cost_usd=response.cost_usd, latency_ms=response.latency_ms
        )

    resolved = voice_extraction.resolve_dates(result, today)
    return score_voice_case(
        case, today, resolved, cost_usd=response.cost_usd, latency_ms=response.latency_ms
    )


async def run_voice_model(
    client: LlmClient,
    model: str,
    cases: Sequence[VoiceGoldenCase],
    today: date,
    *,
    repeats: int = 1,
    save_raw: Path | None = None,
) -> VoiceModelResult:
    scores = [
        await run_voice_case(
            client, model, case, today, save_raw=save_raw, repeat_index=repeat_index
        )
        for case in cases
        for repeat_index in range(repeats)
    ]
    return aggregate_voice(model, scores)


async def run_bank_case(
    client: LlmClient,
    model: str,
    case: BankGoldenCase,
) -> BankCaseScore:
    """Mirrors `run_case`/`run_voice_case` — one call, no repair loop, same
    reasoning. No `today` parameter: `case.anchor_date` is already absolute
    (`evals.scoring.load_bank_golden_cases`'s own docstring), and no
    `save_raw` parameter either — `--modality bank` refuses `--save-raw`
    outright (`main` below), so there is no code path here that could ever
    write a real bank response body into `tests/fixtures/openrouter/`.
    """
    request = bank_extraction.build_request(
        image_data_url=case.image_data_url, catalog=CATALOG, models=(model,)
    )
    try:
        response = await client.complete(request)
    except LlmError as exc:
        logger.warning("model %s errored on case %s: %s", model, case.case_id, exc)
        return failed_bank_case_score(case.case_id, cost_usd=exc.cost_usd, latency_ms=None)

    try:
        result = bank_extraction.parse_content(response.content)
    except ExtractionInvalidError as exc:
        logger.warning("model %s produced invalid output on case %s: %s", model, case.case_id, exc)
        return failed_bank_case_score(
            case.case_id, cost_usd=response.cost_usd, latency_ms=response.latency_ms
        )

    return score_bank_case(case, result, cost_usd=response.cost_usd, latency_ms=response.latency_ms)


async def run_bank_model(
    client: LlmClient,
    model: str,
    cases: Sequence[BankGoldenCase],
    *,
    repeats: int = 1,
) -> BankModelResult:
    scores = [await run_bank_case(client, model, case) for case in cases for _ in range(repeats)]
    return aggregate_bank(model, scores)


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
        "--modality",
        choices=("text", "voice", "bank"),
        default="text",
        help="which extraction modality to evaluate (default: text)",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=_DEFAULT_CASES_PATH,
        help=f"path to a golden .jsonl file (default: {_DEFAULT_CASES_PATH} for "
        f"--modality text, {_DEFAULT_VOICE_CASES_PATH} for --modality voice; "
        "required, with no default, for --modality bank — see evals/golden/bank/README.md)",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=_DEFAULT_VOICE_AUDIO_DIR,
        dest="audio_dir",
        help=f"directory `--modality voice` reads case audio from "
        f"(default: {_DEFAULT_VOICE_AUDIO_DIR})",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        dest="images_dir",
        help="directory `--modality bank` reads case screenshots from; required, with no "
        "default, and refused when it resolves inside this repository (evals.paths."
        "ensure_outside_repo) — see evals/golden/bank/README.md",
    )
    parser.add_argument(
        "--ffmpeg",
        dest="ffmpeg_path",
        default="ffmpeg",
        help="path to the ffmpeg binary `--modality voice` uses to convert golden audio, "
        "exactly as the bot converts a real voice note before extraction (default: "
        "resolved from PATH)",
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

    if args.modality == "bank" and args.save_raw is not None:
        # Checked before load_eval_settings, before any socket is even
        # considered: ADR-0012's Stage-1 amendment permits refreshing
        # tests/fixtures/openrouter/ only from synthetic golden cases, and
        # there are none for bank — every bank case is a real household
        # screenshot (docs/plans/stage-2_5-bank-screenshots.md, Reality
        # check #4). `tests/fixtures/openrouter/bank_*.json` are hand-
        # written instead; see that directory's own README.
        print(  # noqa: T201 -- CLI error, not a debug print
            "--save-raw is refused for --modality bank: there are no synthetic bank "
            "golden cases to refresh a fixture from, only real household screenshots "
            "(docs/plans/stage-2_5-bank-screenshots.md, ADR-0012's Stage-1 amendment); "
            "tests/fixtures/openrouter/bank_*.json are hand-written instead",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        settings = load_eval_settings()
    except MissingApiKeyError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201 -- CLI error, not a debug print
        raise SystemExit(1) from exc

    models = [model.strip() for model in args.models.split(",") if model.strip()]
    if not models:
        print("--models must name at least one OpenRouter model id", file=sys.stderr)  # noqa: T201
        raise SystemExit(1)

    today = args.today or datetime.now(tz=_DEFAULT_TZ).date()
    # args.cases stays at its own argparse default (the text file) unless
    # the caller actually named one — --modality voice on its own should
    # read voice_v1.jsonl, not text_v1.jsonl.
    cases_path = (
        _DEFAULT_VOICE_CASES_PATH
        if args.modality == "voice" and args.cases == _DEFAULT_CASES_PATH
        else args.cases
    )

    # Loaded before any socket is opened, same discipline as
    # load_eval_settings's own API-key check: a missing golden case's audio
    # file, or a broken ffmpeg (evals.scoring.load_voice_golden_cases reads,
    # converts and base64-encodes every one, eagerly) must fail here, for
    # free, rather than mid-run after some earlier cases were already
    # billed.
    if args.modality == "bank":
        # No default for either flag — F4's whole point is that a fresh
        # clone has no bank golden set to fall silently back to.
        if args.cases == _DEFAULT_CASES_PATH:
            print(  # noqa: T201 -- CLI error, not a debug print
                "--cases is required for --modality bank (no default: the case file "
                "holds real household labels and lives outside this repository — see "
                "evals/golden/bank/README.md)",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if args.images_dir is None:
            print(  # noqa: T201 -- CLI error, not a debug print
                "--images-dir is required for --modality bank (no default) — see "
                "evals/golden/bank/README.md",
                file=sys.stderr,
            )
            raise SystemExit(1)
        try:
            bank_cases = load_bank_golden_cases(args.cases, images_dir=args.images_dir)
        except _BANK_LOAD_ERRORS as exc:
            # One clear line, never a raw traceback from deep inside the
            # loader — same discipline as load_eval_settings's own
            # MissingApiKeyError and --modality voice's AudioFetchError.
            print(f"failed to prepare golden bank cases: {exc}", file=sys.stderr)  # noqa: T201
            raise SystemExit(1) from exc
    elif args.modality == "voice":
        try:
            voice_cases = await load_voice_golden_cases(
                cases_path,
                audio_dir=args.audio_dir,
                ffmpeg_path=args.ffmpeg_path,
                timeout_seconds=settings.ffmpeg_timeout_seconds,
            )
        except AudioFetchError as exc:
            # One clear line, never the subprocess mechanics inside
            # convert_to_mp3 as an uncaught traceback — same discipline as
            # load_eval_settings's own MissingApiKeyError.
            print(f"failed to prepare golden voice audio: {exc}", file=sys.stderr)  # noqa: T201
            raise SystemExit(1) from exc
    else:
        cases = load_golden_cases(cases_path)

    async with aiohttp.ClientSession() as http:
        client = OpenRouterClient(
            session=http,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if args.modality == "bank":
            bank_results = [
                await run_bank_model(client, model, bank_cases, repeats=args.repeats)
                for model in models
            ]
            print(render_bank_table(bank_results))  # noqa: T201 -- the point of this CLI
            return

        if args.modality == "voice":
            voice_results = [
                await run_voice_model(
                    client, model, voice_cases, today, repeats=args.repeats, save_raw=args.save_raw
                )
                for model in models
            ]
            print(render_voice_table(voice_results))  # noqa: T201 -- the point of this CLI
            return

        results = [
            await run_model(
                client, model, cases, today, repeats=args.repeats, save_raw=args.save_raw
            )
            for model in models
        ]

    print(render_table(results))  # noqa: T201 -- the whole point of this CLI is this line


if __name__ == "__main__":
    asyncio.run(main())
