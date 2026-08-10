"""OpenRouter client: the only module allowed to open a socket to a model
provider. `finbot.core.extraction` never imports this package (CLAUDE.md
rule 3) — it depends only on `finbot.core.extraction.ports.LlmClient`, which
`OpenRouterClient` satisfies structurally.
"""
