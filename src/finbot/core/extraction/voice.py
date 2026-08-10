"""Pure request/response transforms for voice extraction: no I/O, no clock,
no aiogram, no `ffmpeg` — parallel to `text.py`, and for the same reason
(see that module's own docstring). The audio bytes are supplied by the
caller, already downloaded and converted to mp3 by `adapters.telegram.audio`
(CLAUDE.md rule 3: this stays in `core/` and must never import that module
directly) — this module only builds the request around them and parses
what comes back.
"""

from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError

from finbot.core.categories.catalog import CategorySpec
from finbot.core.extraction.common import ExtractionInvalidError, strip_fence
from finbot.core.extraction.ports import LlmRequest
from finbot.core.extraction.schema import ExtractionResult, VoiceExtractionResult, voice_json_schema
from finbot.core.extraction.text import resolve_dates as _resolve_expense_dates
from finbot.core.money import loads_decimal
from finbot.prompts import render_voice_prompt

SCHEMA_NAME = "voice_extraction_result"

# OpenRouter's documented audio-input format list (docs/roadmap.md Stage 2's
# decision 2): the container `adapters.telegram.audio` always converts into,
# unconditionally — never the original OGG/Opus Telegram sends, so there is
# exactly one wire format this module ever has to name.
AUDIO_FORMAT = "mp3"


def build_request(
    *,
    audio_base64: str,
    today: date,
    catalog: Sequence[CategorySpec],
    models: Sequence[str],
) -> LlmRequest:
    """Mirrors `text.build_request`'s shape, with the user turn carrying an
    `input_audio` content part instead of a plain string — the exact shape
    verified against OpenRouter's audio-input documentation (ADR-0004):
    `{"type": "input_audio", "input_audio": {"data": <base64>, "format":
    "mp3"}}`. No accompanying text part: the system prompt already carries
    every instruction, the same division of labour `text.build_request`
    already uses between the system and user turns.
    """
    system_prompt = render_voice_prompt(today=today, catalog=catalog)
    schema = voice_json_schema([category.slug for category in catalog])
    return LlmRequest(
        models=tuple(models),
        messages=(
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_base64, "format": AUDIO_FORMAT},
                    }
                ],
            },
        ),
        json_schema=schema,
        schema_name=SCHEMA_NAME,
    )


def parse_content(content: str) -> VoiceExtractionResult:
    """Strip an optional ```json fence, parse with `Decimal` floats, then
    validate against `VoiceExtractionResult`. Mirrors `text.parse_content`
    exactly — see that function's docstring — against the voice-only result
    shape instead of the text one.
    """
    unfenced = strip_fence(content)
    try:
        payload = loads_decimal(unfenced)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise ExtractionInvalidError(f"not valid JSON: {exc}") from exc

    try:
        return VoiceExtractionResult.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionInvalidError(f"did not match the schema: {exc}") from exc


def resolve_dates(result: VoiceExtractionResult, today: date) -> VoiceExtractionResult:
    """Delegates the actual date-clamping rule to `text.resolve_dates` — see
    that function's docstring — via a throwaway `ExtractionResult`, since the
    rule only ever looks at `.expenses`. `transcript` has no dates to resolve
    and is carried through unchanged.
    """
    resolved = _resolve_expense_dates(ExtractionResult(expenses=result.expenses), today)
    return VoiceExtractionResult(transcript=result.transcript, expenses=resolved.expenses)
