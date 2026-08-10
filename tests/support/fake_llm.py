"""A project-local fake `LlmClient`, in the spirit of `fake_session.py`
(ADR-0012's standing test pattern, extended to the model as well as to the
Telegram transport).

Parses recorded HTTP response bodies through `finbot.llm.openrouter.
parse_response_body` — the exact function `OpenRouterClient` itself calls —
so a change to cost or model-id extraction cannot pass here while failing in
production. Raises `AssertionError`, not a default response, on any call
beyond what was scripted: "the code called a model behind my back" must be a
loud test failure, never a bill.
"""

from finbot.core.extraction.ports import LlmError, LlmRequest, LlmResponse
from finbot.llm.openrouter import parse_response_body


class FakeLlmClient:
    """`*responses` are either a raw JSON response body (a `str`, exactly
    what OpenRouter's wire would carry) or an `LlmError` to raise for that
    call — one entry per expected `complete()` call, in order.
    """

    def __init__(self, *responses: str | LlmError) -> None:
        self._responses = responses
        self._next = 0
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if self._next >= len(self._responses):
            raise AssertionError(
                "FakeLlmClient.complete() was called beyond its scripted "
                f"responses ({len(self._responses)} given, this is call "
                f"#{self._next + 1}) — the code called a model behind my back"
            )
        item = self._responses[self._next]
        self._next += 1
        if isinstance(item, LlmError):
            raise item
        # latency_ms=0: no real transport happened, and no test in this
        # suite asserts on timing.
        return parse_response_body(item, latency_ms=0)
