# Design — expense capture and reporting

**Date:** 2026-08-09
**Status:** accepted, frozen. Later changes go into ADRs, not into this file.

---

## 1. Scope

A Telegram bot in one private group with two members. It accepts text, voice notes and
receipt photos, extracts individual expenses, stores them in Postgres, and answers
`/day`, `/week`, `/month`.

Every message in the group is an expense. There is no chit-chat to classify, because the
group exists only for this. Telegram privacy mode is disabled, or the bot is a group
admin, so it sees all messages.

## 2. Deployment

Hetzner VPS, `docker compose` with two services: `bot` (Python, long polling) and
`postgres`. A third service `api` appears at Stage 6 and does not exist before then.

**Long polling, not webhooks.** No public IP, domain or TLS certificate is needed.
Telegram retains undelivered updates for about 24 hours, so an outage does not lose
messages.

## 3. Layers

```
adapters/telegram/   handlers, keyboards, callback queries
                     knows Telegram; knows neither SQL nor LLM
core/
  extraction/        (text | audio | image) → list[ExpenseDraft]
  categories/        match against the list, propose, deduplicate
  fx/                rates, cache, conversion to UAH
  reporting/         aggregations for /day /week /month
  models.py          Pydantic domain — the single truth about shape
llm/                 OpenRouter client: routing, fallbacks, cost accounting
repo/                SQLAlchemy — the only layer that knows SQL
evals/               golden dataset + runner
```

**Dependencies point inward.** `core` imports neither `adapters` nor `llm`. This is what
makes the Stage 6 web UI a second adapter rather than a rewrite.

## 4. Flow for one message

1. Update arrives → deduplicate by `update_id` → determine modality.
2. Extraction, routed by modality:
   - **text** → cheap model; prompt carries the schema and the current category list
   - **voice** → base64 audio → multimodal model → `{transcript, expenses[]}`
   - **photo** → base64 image → vision model → `{merchant, receipt_total, expenses[]}`
3. Pydantic validation. On failure, retry with the validation error in the prompt; at
   most two attempts. Every attempt is its own `extractions` row.
4. Currency conversion at the expense date.
5. Anything outside the category list is marked `proposed` (acted on from Stage 5).
6. **Write to the database before replying.** If the bot dies after this, the data is
   already safe.
7. Reply: summary plus inline buttons; the reply's `message_id` is stored on the rows it
   controls.
8. ✏️ / 🗑 → edit or soft-delete, and a row in `corrections`.

Multiple expenses in one message produce **one** confirmation with a numbered list, not
one message per item.

Offsets advance only after successful processing. If Postgres is down, the update is not
acknowledged and Telegram redelivers it. The queue is built into the protocol.

## 5. Data model

```sql
users        (id, telegram_user_id UNIQUE, display_name, created_at)

categories   (id, name UNIQUE, emoji, is_system,
              status,            -- active | rejected | merged
              merged_into_id, created_by, created_at)

messages     (id, telegram_update_id UNIQUE, telegram_message_id, chat_id, user_id,
              kind,              -- text | voice | photo
              raw_text,          -- original text, or transcript
              file_id, created_at)

extractions  (id, message_id, model_id, prompt_version, attempt,
              status,            -- ok | invalid_json | failed
              raw_response jsonb, cost_usd, latency_ms, created_at)

expenses     (id, message_id, user_id, category_id,
              item,
              amount numeric(12,2), currency char(3),
              amount_uah numeric(12,2),
              fx_rate numeric(14,6), fx_rate_date date,
              occurred_at date, created_at timestamptz, deleted_at timestamptz,
              bot_message_id)

corrections  (id, expense_id, before jsonb, after jsonb, corrected_by, created_at)

fx_rates     (currency, rate_date, rate_uah, source, fetched_at)
              PRIMARY KEY (currency, rate_date)
```

Notes:

- **`numeric`, never `float`.** Binary floating point loses cents, and the discrepancy
  is unexplainable months later.
- **Three tables, three lifecycles.** `messages` is what arrived, `extractions` is what
  the model made of it, `expenses` is the truth after corrections. Keeping them apart is
  what allows replaying old inputs through a new model and comparing.
- `occurred_at` is a date, separate from `created_at` — *"I bought bread yesterday"* is
  a normal case.
- Nothing is deleted physically. 🗑 sets `deleted_at`.
- No `confidence` field. Self-reported model confidence is poorly calibrated; the honest
  quality signal is the share of rows corrected with ✏️.

## 6. The model's role

One call in, one validated JSON document out. **No tools, no loop, no state.** The model
cannot reach the database; application code performs every side effect.

Off-topic input needs no instruction to refuse: the output schema has no field for free
text, so *"what colour is the sky"* can only produce `expenses: []`, which the bot
answers with a clarifying question. Constraining the output shape is stronger than
asking politely in the prompt.

Reports involve no model at all — `SELECT ... GROUP BY category`.

## 7. Failure handling

| Failure | Response |
|---|---|
| Provider down / rate-limited | OpenRouter fallback model list, then backoff; if all fail, do not acknowledge the update |
| Output does not match schema | Repair loop: retry with the validation error, max two attempts |
| Schema valid but `expenses: []` | Not a technical failure — ask the user "was that an expense?" |
| Schema valid, values wrong | Not detectable in code. Caught by the confirmation UI and measured by evals |
| FX API down | Last known rate from `fx_rates`, stated in the confirmation |
| Postgres down | Update not acknowledged; Telegram redelivers |
| Voice longer than `MAX_VOICE_SECONDS` | Rejected with an explanation |
| Duplicate update | `UNIQUE(telegram_update_id)` + `ON CONFLICT DO NOTHING` |

Guardrails: a whitelist of two Telegram user IDs; everything else is ignored **silently**
— an "access denied" reply is an invitation to keep poking. A receipt photo is untrusted
input, but the model holds no tools, so the worst case of an injected instruction is a
wrongly recorded expense.

## 8. Evaluation

**Dataset, from two sources.**

- **Synthetic, committed** — `evals/golden/*.jsonl`, roughly thirty hand-written cases
  chosen to be awkward: several expenses in one sentence, relative dates, foreign
  currency, mixed Ukrainian/Russian speech, a creased receipt. These are files because
  they must run on a fresh clone with no database.
- **Real, never files** — the production set is a query, not a directory. `messages`
  holds the input, `expenses` holds the truth after corrections, and `corrections` marks
  which rows the model got wrong. The runner reads them from Postgres when it runs on the
  VPS. Nothing real is copied into the repository.

Media is not archived: voice and photo inputs are referenced by `file_id` and transcript
only, so the production set covers text. Voice and image evaluation uses samples recorded
deliberately for the purpose. See ADR-0009.

**Scoring, deterministic wherever possible.** Separate metrics, not one similarity
score: `amount_exact`, `count_exact` (nothing missed or invented), `category_exact`,
`date_exact`, and `item_similar` — the only place a judge model is warranted. Plus
`cost_per_item` and latency p50/p95.

Never call a judge where an exact check exists. A sum is a number; it compares with `==`.

**Runner.** `python -m evals.run --models a,b,c` over N models × M cases, printing a
table. This replaces intuition when choosing a model.

**Prompt versioning.** Prompts are files (`prompts/extract_text.v1.md`), and the version
is written into every `extractions` row. Change the prompt → new version → run the evals
→ compare. Without this, "did it get better?" has no answer.

## 9. Currencies

UAH, USD, EUR. Stored as `amount` + `currency` + `amount_uah` + `fx_rate` +
`fx_rate_date`, converted **at write time** using the rate for the expense date. A March
report recalculated at today's rate is a lie. Rates come from the NBU public API and are
cached daily in `fx_rates`. An amount with no stated currency defaults to UAH.

## 10. Categories

A fixed list of about twelve, supplied to the model in the prompt; the model must choose
from it. When nothing fits it returns `proposed_category` with a reason instead of
inventing one silently. From Stage 5, the confirmation offers to create it, after a
duplicate check. Rejected proposals are remembered so the same one is not offered
repeatedly.

Left to itself, a model with no memory of yesterday's labels produces *Food*, *Groceries*
and *Supermarket* as three distinct categories within a month.

## 11. Explicitly out of the first release

No web interface. No budgets or limits. No recurring payments. No charts. No per-person
split in reports. No export. No CI.
