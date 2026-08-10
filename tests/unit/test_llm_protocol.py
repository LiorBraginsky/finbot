"""Proves `OpenRouterClient` satisfies `core.extraction.ports.LlmClient`
structurally, without `core` importing `finbot.llm` (CLAUDE.md rule 3).

The real assertion here is mypy's, run as part of the `mypy src/` gate:
`_conforms` only type-checks if `OpenRouterClient` has a compatible
`complete()` method. This file also runs under pytest so a broken import
fails loudly there too, not only under mypy.
"""

from finbot.core.extraction.ports import LlmClient
from finbot.llm.openrouter import OpenRouterClient


def _conforms(client: OpenRouterClient) -> LlmClient:
    return client


def test_openrouter_client_satisfies_the_llm_client_protocol() -> None:
    assert callable(_conforms)
