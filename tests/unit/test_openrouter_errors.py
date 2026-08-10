"""Unit tests for `OpenRouterClient.complete()`'s failure paths.

No network: `aiohttp.ClientSession.post()` is never awaited directly, only
entered via `async with` — so a transport failure is simulated by making a
fake session's `.post()` return an async context manager whose `__aenter__`
raises, exactly where aiohttp itself raises it (see openrouter.py's module
docstring Reality check). A non-2xx status or a 200 with a broken body is
simulated by returning a fake response instead.

Every case here answers the question the owner's eval run exposed: a
timeout logged as `errored on case single-01:` with nothing after the
colon, because `str(TimeoutError())` is the empty string and `LlmError`'s
message was built from `str(exc)` alone. These cases are derived from the
failure space `complete()` actually catches, not from the one exception the
owner happened to see: timeout, a connection error that is *also* silent,
non-2xx, a non-JSON body, and a malformed-but-JSON envelope.
"""

from datetime import date
from typing import Any, cast

import aiohttp
import pytest
from pydantic import SecretStr

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.ports import LlmError, LlmRequest
from finbot.core.extraction.text import build_request
from finbot.llm.openrouter import OpenRouterClient

_TIMEOUT_SECONDS = 5


class _FakeResponse:
    def __init__(self, *, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text


class _FakeRequestContext:
    """Stands in for aiohttp's `_RequestContextManager`. `OpenRouterClient`
    never awaits `session.post()` directly, only `async with`s what it
    returns, so a transport failure has to raise from `__aenter__` — the
    same place a real total-timeout or connection failure surfaces.
    """

    def __init__(
        self, *, response: _FakeResponse | None = None, error: BaseException | None = None
    ) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSession:
    """`OpenRouterClient.complete()` only ever calls `.post()` on the
    session it was given, so that is the entire surface this needs.
    """

    def __init__(self, context: _FakeRequestContext) -> None:
        self._context = context

    def post(self, url: str, **kwargs: Any) -> _FakeRequestContext:
        return self._context


def _client(context: _FakeRequestContext) -> OpenRouterClient:
    return OpenRouterClient(
        session=cast(aiohttp.ClientSession, _FakeSession(context)),
        api_key=SecretStr("sk-or-fake-not-a-real-key"),
        base_url="http://127.0.0.1:9",
        timeout_seconds=_TIMEOUT_SECONDS,
    )


def _request() -> LlmRequest:
    return build_request(
        raw_text="хліб 50",
        today=date(2026, 8, 10),
        catalog=CATALOG,
        models=("openai/gpt-5.6-luna",),
    )


async def test_a_timeout_names_the_configured_timeout_in_seconds() -> None:
    # str(TimeoutError()) == "" -- the exact silence the owner's eval run hit.
    client = _client(_FakeRequestContext(error=TimeoutError()))

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert message
    assert "TimeoutError" in message
    assert f"{_TIMEOUT_SECONDS}s" in message


async def test_a_silent_connection_error_still_names_its_type() -> None:
    # aiohttp.ClientConnectionError() with no args is exactly as silent as
    # TimeoutError() -- str() is "" -- so the fix cannot be timeout-specific.
    client = _client(_FakeRequestContext(error=aiohttp.ClientConnectionError()))

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert message
    assert "ClientConnectionError" in message


async def test_a_connection_error_with_detail_keeps_it() -> None:
    client = _client(
        _FakeRequestContext(error=aiohttp.ClientConnectionError("connection reset by peer"))
    )

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert "ClientConnectionError" in message
    assert "connection reset by peer" in message


async def test_a_non_2xx_status_names_the_status_code() -> None:
    client = _client(
        _FakeRequestContext(response=_FakeResponse(status=504, text="Gateway Timeout"))
    )

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert message
    assert "504" in message


async def test_a_non_json_body_names_the_parse_failure() -> None:
    client = _client(
        _FakeRequestContext(response=_FakeResponse(status=200, text="<html>Bad Gateway</html>"))
    )

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert message
    assert "JSON" in message


async def test_a_malformed_envelope_names_the_missing_field() -> None:
    # Well-formed JSON, but no "model" key at all -- the KeyError case.
    client = _client(
        _FakeRequestContext(response=_FakeResponse(status=200, text='{"choices": []}'))
    )

    with pytest.raises(LlmError) as excinfo:
        await client.complete(_request())

    message = str(excinfo.value)
    assert message
    assert "KeyError" in message
