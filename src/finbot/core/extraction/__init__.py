"""Extraction: builds the LLM request, parses the response, orchestrates
persistence. May import `finbot.core` and `finbot.repo` and its own
`ports.py`; must never import `finbot.adapters` or `finbot.llm` (CLAUDE.md
rule 3, enforced by `tests/unit/test_layering.py`). `finbot.llm.openrouter`
satisfies `ports.LlmClient` structurally, proven by mypy in
`tests/unit/test_llm_protocol.py` — the dependency arrow points from `llm`
toward `core`, never the other way.
"""
