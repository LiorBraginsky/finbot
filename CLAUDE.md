# finbot — instructions for AI agents

Read `docs/vision.md` and the most recent entries in `docs/journal.md` before doing
anything. They carry context that is not derivable from the code.

## What this project is

A Telegram bot that converts speech, text and receipt photos into structured household
expenses in Postgres. Two users. Public repository, private data.

## Non-negotiable rules

1. **The model has no tools and no loop.** Extraction is a single call with a fixed
   output schema. Application code performs every side effect. Do not introduce tool
   use or an agent loop outside Stage 7. See ADR-0003.
2. **Money is `numeric`, never `float`.** Anywhere. No exceptions.
3. **`core/` does not import `adapters/` or `llm/`.** Dependencies point inward. This
   is what lets a web UI become a second adapter instead of a rewrite.
4. **Never commit secrets or real household data.** No `.env`, no DB dumps, no
   screenshots with real amounts, no Telegram user IDs. `evals/golden/` holds
   hand-written synthetic cases only — real cases are queried from Postgres, never
   written to files.
5. **Reports are SQL.** Do not route aggregation through a language model.
6. **Every LLM call is recorded** in `extractions` with `model_id`, `prompt_version`,
   `attempt`, `status`, `cost_usd`, `latency_ms` and the raw response. This is not
   optional logging — it is the evaluation dataset.

## Layout

```
src/finbot/
  core/        extraction, categories, fx, reporting, models   ← no I/O frameworks
  repo/        SQLAlchemy; the only layer that knows SQL
  llm/         OpenRouter client: routing, fallbacks, cost accounting
  adapters/
    telegram/  entry point 1
    http/      entry point 2 (stage 6)
  prompts/     versioned prompt files, e.g. extract_text.v1.md
evals/         golden dataset + runner
infra/         docker compose, Dockerfile, backup script
```

## Gates before claiming done

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

All three must pass. "Should work" is not a result — run them and read the output.

## Prompts

Prompts are versioned files in `src/finbot/prompts/`, never inline strings. Changing a
prompt means a new version file and a new `prompt_version` value. Any prompt change is
compared against the previous version with `python -m evals.run` before it ships.

## Git

- Agents may commit in this repository.
- Never `--force`, never `--no-verify`, never amend a pushed commit.
- The `gitleaks` pre-commit hook must stay enabled. If it blocks a commit, fix the
  content — do not bypass the hook.
- Conventional-ish messages: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

## Journal

Any agent finishing a unit of work appends an entry to the **top** of
`docs/journal.md`, in English, following the format documented in that file. Maximum
five lines. Do not restate the diff — git already has it. Record intent, surprises,
dead ends and open questions.
