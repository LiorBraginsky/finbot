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
| [`.claude/orchestration.md`](.claude/orchestration.md) | How work moves: authoritative docs, gates, roles, resuming after a pause |

## Running it

Secrets live in `.env` (see `.env.example`) and never in the repository. A
`gitleaks` pre-commit hook enforces this — enable it once after cloning:

```bash
git config core.hooksPath .githooks
cp .env.example .env   # then fill it in
```

### Local development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy src/
pytest
```

The integration suite starts a real, throwaway Postgres via `testcontainers`, so a
running Docker daemon is required. **These tests fail rather than skip when Docker is
unavailable — by design:** a skipped DB test in a project that auto-merges on green
gates is a green gate that proves nothing. The suite must also be deterministic rather
than merely usually green — on an auto-merging branch, an intermittent failure either
blocks merges at random or teaches people to re-run until it passes, and either way
"green" stops carrying information — which is why `tests/conftest.py` disables
testcontainers' Ryuk sidecar rather than leaving its known startup race for someone to
re-run past (see the comment there).

### Running the stack

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

This builds the `bot` image (Python 3.12 + `ffmpeg`, needed from Stage 2 for voice
notes), starts `postgres`, waits for it to be healthy, then runs `alembic upgrade head`
before starting the bot. Postgres publishes no port — long polling needs no inbound
connection at all, so there is nothing to expose on a public VPS. Inspect the database
with `docker compose --env-file .env -f infra/docker-compose.yml exec postgres psql -U finbot -d finbot`.

## Data and privacy

This is a public repository for a private ledger. The two are kept apart deliberately:

- No database dumps, no screenshots with real amounts, no Telegram user IDs.
- `evals/golden/` holds hand-written synthetic cases only. Real evaluation cases are
  never files — they are queried from Postgres, where the input and the corrected answer
  already live.

See [ADR-0009](docs/decisions/0009-public-repo-private-eval-data.md).

## License

MIT
