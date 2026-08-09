# Architecture decision records

Short records of decisions that were not obvious, written so that neither a human nor an
agent has to re-derive them months later. Each one states the context, the decision, its
consequences, and what was rejected.

An ADR is never edited to change its decision. A new ADR supersedes it and says so.

| # | Decision |
|---|---|
| [0001](0001-postgres-over-spreadsheet.md) | Postgres, not a spreadsheet |
| [0002](0002-vps-with-docker-compose.md) | Self-hosted VPS with docker compose |
| [0003](0003-single-step-extraction-not-agent.md) | Single-step extraction, not an agent loop |
| [0004](0004-openrouter-and-model-routing.md) | OpenRouter gateway, one model per modality |
| [0005](0005-controlled-category-taxonomy.md) | Controlled category taxonomy with a human gate |
| [0006](0006-separate-provenance-tables.md) | Separate input, extraction and truth tables |
| [0007](0007-confirmation-with-inline-buttons.md) | Confirm after writing, correct with buttons |
| [0008](0008-currency-conversion-at-write-time.md) | Convert currency at write time, store the rate |
| [0009](0009-public-repo-private-eval-data.md) | Public repository, private evaluation data |
| [0010](0010-journal-over-state-file.md) | Append-only journal instead of a state file |
| [0011](0011-at-least-once-delivery-is-not-free.md) | At-least-once delivery is not free; the spec's redelivery claim is withdrawn |
| [0012](0012-stage-0-verification-strategy.md) | Stage-0 verification: a real Postgres in tests, Telegram through the dispatcher |
