"""The seam between `core/extraction` and `llm/openrouter.py` (CLAUDE.md rule
3): `core` depends only on this Protocol, never on `finbot.llm` itself.
`llm/openrouter.py`'s `OpenRouterClient` satisfies `LlmClient` structurally —
proven by mypy in `tests/unit/test_llm_protocol.py` — so the dependency arrow
points from `llm` toward `core`, never the reverse.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmRequest:
    """Everything needed to make one chat-completions call. Immutable so a
    repair request can be built from a previous one without aliasing bugs.

    `messages[n]["content"]` is `Mapping[str, Any]`, not `Mapping[str, str]`:
    `core.extraction.text` sends a plain string, but `core.extraction.voice`
    sends a list of content parts (`[{"type": "input_audio", ...}]`) — the
    exact shape OpenRouter's audio-input API requires (ADR-0004,
    docs/roadmap.md Stage 2). `llm/openrouter.py` never inspects a message
    beyond `dict(message)`, so it does not care which shape it is passing
    through.
    """

    models: tuple[str, ...]
    messages: tuple[Mapping[str, Any], ...]
    json_schema: Mapping[str, Any]
    schema_name: str


@dataclass(frozen=True)
class LlmResponse:
    model_id: str  # from the response body, never from config — the model that actually served
    content: str
    cost_usd: Decimal | None  # OpenRouter types usage.cost as nullable
    latency_ms: int
    raw: Mapping[str, Any]
    # The untouched response body, exactly as the wire carried it — never
    # `json.dumps(raw)`. `raw` has already been parsed once through
    # `core.money.loads_decimal`, so re-serializing it with the stdlib's
    # `default=str` renders every Decimal as a *string* (`0.000123` becomes
    # `"0.000123"`), which is what corrupted `evals/run.py --save-raw`'s own
    # fixture refreshes. Byte-for-byte is the only representation that can't
    # lie about what the wire actually sent.
    raw_text: str


class LlmError(Exception):
    """Transport / provider failure: non-2xx, timeout, connection error.

    Carries `raw` — the error object recorded verbatim into
    `extractions.raw_response`, because CLAUDE.md rule 6 records every call,
    including failed ones, and "we had no response" is itself the record.

    `cost_usd` defaults to `None` (no response ever arrived, or the response
    reported nothing usable) but is not always `None`: a malformed 200 can
    still carry a legible `usage.cost` next to whatever *else* is wrong with
    the body (a null model, an empty `choices`) — `llm/openrouter.py`'s
    `parse_response_body` fills it in exactly then, because rule 6 names
    `cost_usd` explicitly and a `NULL` there is indistinguishable from "this
    call was free".
    """

    def __init__(
        self, message: str, *, raw: Mapping[str, Any], cost_usd: Decimal | None = None
    ) -> None:
        super().__init__(message)
        self.raw = raw
        self.cost_usd = cost_usd


class LlmClient(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...


class AudioFetchError(Exception):
    """Downloading a voice note or converting it to mp3 failed, before any
    model call was ever made — a Telegram download failure, an oversized
    file, or a nonzero `ffmpeg` exit. Raised by `adapters.telegram.audio`,
    the only place in this project that touches aiogram's download API or
    shells out to `ffmpeg`, and caught by `core.extraction.pipeline`, which
    must never import that module directly (CLAUDE.md rule 3). This class is
    the seam between the two, exactly as `LlmClient` is for the model call
    itself: `core` depends on the exception type, never on what raises it.
    """


class ImageFetchError(Exception):
    """Downloading a bank-feed screenshot failed, before any model call was
    ever made — a Telegram download failure, an oversized file, or content
    that does not sniff as a supported image format. Raised by
    `adapters.telegram.images` (Stage 2.5 Step 2), the only place in this
    project that touches aiogram's download API for a photo, and caught by
    `core.extraction.pipeline`, which must never import that module directly
    (CLAUDE.md rule 3). Parallel to `AudioFetchError` in every respect: the
    seam is the exception type, never what raises it.
    """
