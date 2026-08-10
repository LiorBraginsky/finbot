"""The OpenRouter chat-completions client.

Structurally satisfies `finbot.core.extraction.ports.LlmClient` — proven by
mypy in `tests/unit/test_llm_protocol.py` — without `core` ever importing
this module (CLAUDE.md rule 3). `core/extraction/text.py` builds the request
and parses the response; this module only performs the HTTP call and reads
the response's authoritative fields (`model`, `usage.cost`) off the wire.

Every non-negotiable detail below is verified against the OpenRouter API and
the installed `aiohttp` (see docs/plans/stage-1-text-to-expense.md's Reality
check):

- No `usage: {include: true}` — deprecated and inert; usage is always
  returned, so sending it would be configuration nobody could tell had
  stopped doing anything.
- `provider.require_parameters: true` is mandatory: structured-output
  support is per *endpoint*, not per model, so without it a request can be
  routed to a provider that silently ignores `response_format`.
- `models` (plural) carries the whole fallback list; there is no separate
  retry wrapper.
- `model_id` is read from the response body's `model` field — the model that
  actually served the request — never from config, which would silently
  mislabel every fallback row in the evaluation dataset.
- `cost_usd` is `usage.get("cost")`, already a `Decimal` because the whole
  body is parsed with `finbot.core.money.loads_decimal`; `null` stays
  `None`.
- A total-timeout expiry surfaces from `aiohttp.ClientSession.post()` as the
  builtin `TimeoutError` (`asyncio.TimeoutError is TimeoutError` as of
  Python 3.11), not as an `aiohttp.ClientError` subclass — confirmed by
  reading `aiohttp.helpers.TimerContext.__exit__` in the installed
  3.14.3 package, not assumed from documentation. Both are therefore caught
  explicitly below.
"""

import time
from decimal import Decimal
from typing import Any

import aiohttp
from pydantic import SecretStr

from finbot.core.extraction.ports import LlmError, LlmRequest, LlmResponse
from finbot.core.money import loads_decimal

_CHAT_COMPLETIONS_PATH = "/chat/completions"


def _extract_cost(body: Any) -> Decimal | None:
    """Best-effort `usage.cost`: `None` for anything that isn't cleanly a
    reported number, never a raise. Called *before* the shape checks in
    `parse_response_body` that can themselves raise, so a `LlmError` about
    something else being wrong with the body (a null model, an empty
    `choices`) can still carry the cost that really was billed for the
    call — CLAUDE.md rule 6 names `cost_usd` explicitly, and `NULL` there is
    indistinguishable from "nothing was charged". This function only ever
    answers "is there a clean value to carry along"; `parse_response_body`
    is what turns "reported, but not a number" into a `LlmError` of its own.
    """
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if cost is None or isinstance(cost, bool):
        return None
    if isinstance(cost, int):
        return Decimal(cost)
    return cost if isinstance(cost, Decimal) else None


def parse_response_body(text: str, *, latency_ms: int) -> LlmResponse:
    """Parse a raw OpenRouter chat-completions HTTP response body into an
    `LlmResponse`. Shared verbatim between `OpenRouterClient.complete()` and
    `tests/support/fake_llm.py`'s `FakeLlmClient`, so a change to cost or
    model-id extraction cannot pass in tests while failing in production.

    A 200 response is not a guarantee of any particular shape, starting
    before the first index: the body itself may not even be JSON (an empty
    body, a proxy's HTML error page, a misconfigured `OPENROUTER_BASE_URL`
    pointed at something that isn't OpenRouter at all). Past that, a
    provider error can come back wrapped in a 200 (`{"error": {...}}`, no
    `choices` at all), a route can return `{"choices": []}`, `"model"` or
    `message.content` can be `null`, and `usage` can be present but not a
    mapping (`"usage": "n/a"`) or `usage.cost` can be present but not a
    number (`"usage": {"cost": "0.1"}`, a string). Every one of these used to
    raise straight out of this function — `json.JSONDecodeError`, `KeyError`,
    `IndexError`, `TypeError`, `AttributeError` — none of which
    `core.extraction.pipeline.extract_and_store` catches as `LlmError` or
    `ExtractionInvalidError`, so the call was billed (or at least attempted)
    with no `extractions` row ever written, breaking CLAUDE.md rule 6; a
    `null` model_id or a non-`Decimal` cost went further still, reaching a
    `NOT NULL` column or a `Numeric` bind and raising *there* instead.
    Raising `LlmError` here — as early as the first line that touches
    untrusted input, not the first line that indexes it — with the body (or
    the raw text, if it was never JSON) as `raw`, makes "the response made
    no sense" the record.
    """
    try:
        body = loads_decimal(text)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise LlmError(
            f"response body was not JSON: {exc}",
            raw={"error": text[:2000], "type": "not_json", "status": None},
        ) from exc

    # Read before any of the shape checks below can raise: a LlmError about
    # something *else* being wrong with the body (a null model, an empty
    # choices) still carries the cost that was really billed for the call.
    known_cost = _extract_cost(body)

    try:
        model_id = body["model"]
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        raw_cost = usage.get("cost")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LlmError(
            f"malformed OpenRouter response body: {exc}", raw=body, cost_usd=known_cost
        ) from exc

    if not isinstance(model_id, str) or not isinstance(content, str):
        raise LlmError(
            f"OpenRouter response model/content had the wrong type "
            f"(model={model_id!r}, content={content!r})",
            raw=body,
            cost_usd=known_cost,
        )
    # bool first: bool is a subclass of int, and a boolean cost is exactly
    # as nonsensical as a string one, never a legitimate zero/one. No
    # cost_usd on this one: raw_cost *is* the broken value, so there is
    # nothing trustworthy to carry.
    if raw_cost is not None and (
        isinstance(raw_cost, bool) or not isinstance(raw_cost, Decimal | int)
    ):
        raise LlmError(f"usage.cost was not a number (got {raw_cost!r})", raw=body)

    return LlmResponse(
        model_id=model_id,
        content=content,
        cost_usd=known_cost,
        latency_ms=latency_ms,
        raw=body,
        raw_text=text,
    )


class OpenRouterClient:
    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        api_key: SecretStr,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    def _build_body(self, request: LlmRequest) -> dict[str, Any]:
        return {
            "models": list(request.models),
            "messages": [dict(message) for message in request.messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": dict(request.json_schema),
                },
            },
            # ADR-0004: household financial data, so every request denies
            # training/logging. require_parameters=True is what makes
            # "strict structured output" a hard requirement of the route
            # rather than a hint a provider is free to ignore.
            "provider": {"data_collection": "deny", "require_parameters": True},
            "temperature": 0,
        }

    async def complete(self, request: LlmRequest) -> LlmResponse:
        body = self._build_body(request)
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        url = f"{self._base_url}{_CHAT_COMPLETIONS_PATH}"

        started = time.perf_counter()
        try:
            async with self._session.post(
                url, json=body, headers=headers, timeout=timeout
            ) as response:
                text = await response.text()
                status = response.status
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise LlmError(
                str(exc),
                raw={"error": str(exc), "type": type(exc).__name__, "status": None},
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if status < 200 or status >= 300:
            raise LlmError(
                f"OpenRouter returned HTTP {status}",
                raw={"error": text, "type": "http_error", "status": status},
            )

        return parse_response_body(text, latency_ms=latency_ms)
