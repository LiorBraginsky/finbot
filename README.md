# finbot

A Telegram bot that turns everyday speech — text, voice notes, receipt photos — into a
structured household expense ledger.

You say *"bread 50, taxi 200"* in a family Telegram group. The bot extracts the
individual expenses, converts foreign currency at the rate of the day, files each one
under a category, writes it to Postgres, and replies with a confirmation you can fix
with one tap.

> Built as a hands-on exercise in applied LLM engineering: model routing across
> modalities, structured output with schema validation, evaluation harnesses, cost
> accounting, and prompt versioning — on a system that two real people use every day.

## Status

Early. See [`docs/roadmap.md`](docs/roadmap.md) for stages and
[`docs/journal.md`](docs/journal.md) for what actually happened.

## How it works

```
Telegram update
  → dedupe by update_id
  → route by modality  (text | voice | photo)
  → LLM call with a fixed JSON schema  →  { transcript?, expenses[] }
  → Pydantic validation (repair loop on failure)
  → currency conversion at the expense date
  → write to Postgres
  → confirmation message with ✏️ / 🗑 buttons
```

The model is **not** an agent. It has no tools and no loop: one call in, one validated
JSON document out. Application code owns every side effect. See
[ADR-0003](docs/decisions/0003-single-step-extraction-not-agent.md).

Reports (`/day`, `/week`, `/month`) are plain SQL — no model involved.

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Telegram | aiogram 3 (long polling) |
| Schemas | Pydantic v2 |
| Database | Postgres + SQLAlchemy 2 / asyncpg, Alembic migrations |
| LLM access | OpenRouter (single gateway, per-modality model routing) |
| FX rates | NBU public API, cached daily |
| Deployment | Hetzner VPS, docker compose |

## Documentation

| Document | Purpose |
|---|---|
| [`docs/vision.md`](docs/vision.md) | Why this exists, who it is for, what it will never do |
| [`docs/roadmap.md`](docs/roadmap.md) | Stages 0–7 with status |
| [`docs/journal.md`](docs/journal.md) | Append-only work log — intent, dead ends, findings |
| [`docs/specs/`](docs/specs) | Frozen design documents |
| [`docs/decisions/`](docs/decisions) | ADRs — why things are the way they are |
| [`CLAUDE.md`](CLAUDE.md) | Conventions and gates for AI agents working in this repo |

## Running it

Nothing to run yet. Setup instructions land with Stage 0.

Secrets live in `.env` (see `.env.example`) and never in the repository. A
`gitleaks` pre-commit hook enforces this — enable it once after cloning:

```bash
git config core.hooksPath .githooks
```

## Data and privacy

This is a public repository for a private ledger. The two are kept apart deliberately:

- No database dumps, no screenshots with real amounts, no Telegram user IDs.
- `evals/golden/` holds hand-written synthetic cases only. Real evaluation cases are
  never files — they are queried from Postgres, where the input and the corrected answer
  already live.

See [ADR-0009](docs/decisions/0009-public-repo-private-eval-data.md).

## License

MIT
