# Roadmap

Status: ⬜ not started · 🚧 in progress · ✅ done

Each stage is a thin vertical slice — something usable the same evening — not a
horizontal layer. A stage that never reaches a human produces no signal about whether
the model is any good.

---

## ✅ Stage 0 — Skeleton

Infrastructure only. **No LLM anywhere in this stage.**

- Hetzner VPS, `docker compose` with `bot` + `postgres`
- aiogram long polling, whitelist of two user IDs, `/ping`
- Raw incoming messages persisted to `messages`
- Alembic wired up
- `pg_dump` cron + off-site copy
- `gitleaks` hook enabled

**Done when:** the bot answers `/ping` from the VPS and messages land in the table.

*Closed 2026-08-09, verified in production: `/ping` answered from the VPS, five messages
from two whitelisted senders persisted, `count(*) == count(distinct telegram_update_id)`.
Daily `pg_dump` on cron, restore never yet exercised. **One item remains open: the dump
lives on the same host as the database**, so it survives a bad `DELETE` but not a dead
server. Off-site copy is the first thing to fix outside a stage.*

*Why separate: if infrastructure and model land together, the first failure costs half a
day of guessing whether it was Docker, the network, Telegram, Postgres or the prompt.*

---

## 🚧 Stage 1 — MVP: text → expense

- Extraction from text with a fixed Pydantic schema, repair loop on invalid output
- Fixed category list (~12), model must choose from it
- Write to `expenses`, record the call in `extractions`
- Confirmation message with ✏️ / 🗑 inline buttons
- `/day`, `/week`, `/month` — plain SQL
- ~10 hand-written eval cases and a throwaway runner, enough to pick a cheap model

**Done when:** a week of recording expenses by text, without opening a spreadsheet.

*Software complete: extraction, the thirteen categories, the confirmation/report flow, and
the eleven-case golden set with a runner (`python -m evals.run`) are all built and covered
by `pytest`. **The eval ran** against the five candidate models on 2026-08-10 (33 calls
each) and the model is chosen — `MODEL_TEXT=google/gemini-3.5-flash-lite`,
`MODEL_FALLBACKS=google/gemini-3.6-flash`, both clearing the pre-registered `schema_ok`
gate, the cheaper and faster of the two — see `docs/journal.md`'s top entry for the table,
the choice, and the criterion that produced it. **What remains is not software:**
deploying with these settings, and then the stage's actual done-criterion — a week of
recording expenses by text without opening a spreadsheet — which no gate can prove and has
not started yet. Not marked done until it has.*

---

## ⬜ Stage 1.5 — Currencies

- USD / EUR alongside UAH
- NBU rate for the expense date, cached in `fx_rates`
- Degrade to the last known rate and say so in the confirmation

**Done when:** *"100 dollars for hosting"* is stored with a correct `amount_uah`.

Two deferrals from Stage 1 land here, both recorded in ADR-0013 and the journal:

- **`fx_rate_date` is currently set on rows that were never converted** (every UAH row
  gets `fx_rate_date = occurred_at`, with `fx_rate = 1`). Stage 1.5's inevitable "which
  rows used a stale or missing rate" query would otherwise find a year of rows claiming a
  rate date they never had. Decide the honest value — `NULL` for unconverted rows — and
  backfill.
- **A lease column (`claimed_until`) instead of the crash-release guard.** One predicate
  in `claim_next` would then cover a caught exception, a SIGKILL and a stalled worker
  alike, replacing the startup `reset_processing` and the conditional release. Rejected
  in Stage 1 as a migration plus a renewal loop for two users; revisit if a second worker
  ever appears.

---

## ⬜ Stage 2 — Voice

- OGG/Opus from Telegram → multimodal model → `{transcript, expenses[]}`
- `ffmpeg` conversion as the fallback path
- Transcript shown in the confirmation and stored as provenance

**Done when:** five expenses dictated in one voice note all land correctly.

---

## ⬜ Stage 3 — Evaluation harness

- Metrics: `amount_exact`, `count_exact`, `category_exact`, `date_exact`,
  `item_similar` (the only one that needs a judge), `cost_per_item`, latency p50/p95
- Real cases read straight from Postgres (`messages` + `expenses` + `corrections`),
  never copied into files; synthetic cases stay in `evals/golden/`
- Prompt versioning compared version-to-version
- Runner across N models × M cases

**Done when:** there is a model × accuracy × cost × latency table built on real data.

*Do not defer this further. After Stage 2 there is real traffic and real corrections —
the harness is cheapest and most useful exactly then.*

*This is also the first stage where an unattended loop earns its place: the stopping
criterion is numeric (metrics stop improving), so an agent can iterate on prompts and
model choice without supervision. Autonomy follows gate quality, not the other way
round.*

---

## ⬜ Stage 4 — Receipt photos

- Image → vision model → `{merchant, receipt_total, expenses[]}`
- Cross-check `sum(expenses) ≈ receipt_total`; flag the mismatch instead of hiding it

**Done when:** a supermarket receipt decomposes into line items and the total reconciles.

---

## ⬜ Stage 5 — Category proposals

- Model returns `proposed_category` when nothing fits, never invents silently
- Confirmation offers *Create "Pets"?* / *Put in "Other"*
- Duplicate check against existing categories before creating
- Rejected proposals are remembered so they are not offered again

*Deliberately late: first find out on real data which categories are actually missing.
Finding out that twelve were enough is the best possible outcome.*

---

## ⬜ Stage 6 — Web UI for corrections (C)

FastAPI as a second adapter over the same `core`, plus a minimal frontend in `web/`.
Only if the inline buttons prove insufficient in practice.

---

## ⬜ Stage 7 — Dashboard and free-form questions (D)

Charts, and questions like *"how much did we spend on coffee in June?"*.

**This is where a real agent belongs** — read-only tools or text-to-SQL, with its own
budget and its own evaluation set. Nowhere earlier.

---

## Continuous, from day one

- Backups: `pg_dump` on cron, copy stored **off the VPS**
- Every LLM call recorded in `extractions` with cost and latency
