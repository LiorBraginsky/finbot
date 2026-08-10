"""Unit tests for the request body finbot.llm.openrouter.OpenRouterClient
builds. No network: `_build_body()` is exercised directly, and the fixture
below never calls `.post()`.

These assertions are the byte-exact contract from
docs/plans/stage-1-text-to-expense.md's Reality check: no `usage: {include:
true}` (deprecated and inert — usage is always returned), `provider.
require_parameters: true` (structured-output support is per *endpoint*, so
without it a request can be routed to a provider that ignores
`response_format` and returns prose), and `provider.data_collection: "deny"`
(household financial data; ADR-0004).
"""

from collections.abc import AsyncIterator
from datetime import date

import aiohttp
import pytest_asyncio
from pydantic import SecretStr

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.ports import LlmRequest
from finbot.core.extraction.text import build_request
from finbot.llm.openrouter import OpenRouterClient


@pytest_asyncio.fixture
async def client() -> AsyncIterator[OpenRouterClient]:
    # http://127.0.0.1:9 is the discard port: even if a bug made this open a
    # socket, it would fail instantly rather than reach a real provider.
    async with aiohttp.ClientSession() as session:
        yield OpenRouterClient(
            session=session,
            api_key=SecretStr("sk-or-fake-not-a-real-key"),
            base_url="http://127.0.0.1:9",
            timeout_seconds=5,
        )


def _request() -> LlmRequest:
    return build_request(
        raw_text="хліб 50, таксі 200",
        today=date(2026, 8, 10),
        catalog=CATALOG,
        models=("openai/gpt-5.6-luna", "qwen/qwen3.7-flash"),
    )


async def test_body_sends_the_full_model_candidate_list(client: OpenRouterClient) -> None:
    body = client._build_body(_request())
    assert body["models"] == ["openai/gpt-5.6-luna", "qwen/qwen3.7-flash"]


async def test_body_provider_denies_data_collection_and_requires_parameters(
    client: OpenRouterClient,
) -> None:
    body = client._build_body(_request())
    assert body["provider"] == {"data_collection": "deny", "require_parameters": True}


async def test_body_response_format_is_a_strict_json_schema(client: OpenRouterClient) -> None:
    body = client._build_body(_request())
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "extraction_result"
    assert response_format["json_schema"]["schema"]["type"] == "object"


async def test_body_never_sends_a_usage_key(client: OpenRouterClient) -> None:
    # "usage: {include: true}" is deprecated and inert; usage is now always
    # returned, so sending it at all would be dead configuration nobody
    # would ever notice stopped mattering.
    body = client._build_body(_request())
    assert "usage" not in body


async def test_body_temperature_is_zero(client: OpenRouterClient) -> None:
    body = client._build_body(_request())
    assert body["temperature"] == 0


async def test_body_messages_round_trip_from_the_request(client: OpenRouterClient) -> None:
    request = _request()
    body = client._build_body(request)
    assert body["messages"] == [dict(message) for message in request.messages]
