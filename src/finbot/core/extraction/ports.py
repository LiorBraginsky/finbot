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
    """

    models: tuple[str, ...]
    messages: tuple[Mapping[str, str], ...]
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
