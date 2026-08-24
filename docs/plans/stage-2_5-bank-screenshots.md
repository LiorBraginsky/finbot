# Plan — Stage 2.5: bank-app screenshots → expenses

> **For the worker:** every design decision here is already made. Do not re-decide. If
> something contradicts `CLAUDE.md`, the spec or an ADR, stop at the BLOCK bar
> (`.claude/orchestration.md` → `## Escalation`).

**Branch:** `stage-2_5-bank-screenshots`.

**Goal:** one screenshot of a bank feed becomes the right expenses, with savings jars,
own-card transfers, outgoing transfers and incoming money visibly skipped rather than
silently dropped — and re-sending the same screenshot records nothing new.

---

## Reality check

Truth read in full: `.claude/orchestration.md`, `CLAUDE.md`, `docs/vision.md`,
`docs/roadmap.md`, the spec, ADRs 0001–0016, the top four journal entries. Code read:
`core/extraction/*`, `core/{models,categories/catalog}.py`, `llm/openrouter.py`,
`prompts/`, `adapters/telegram/*`, `repo/*`, `config.py`, `evals/*`, `tests/support/*`,
`pyproject.toml`, `.gitignore`, migration `0003`.

**The spike is done.** ADR-0004 required verifying image pass-through before planning
around it. Three real Privat24 screenshots, `google/gemini-3.5-flash-lite`, an
OpenAI-style `image_url` content part carrying `data:image/jpeg;base64,…`, strict
`response_format`, `provider: {data_collection: deny, require_parameters: true}`.
Images pass through, strict output holds, **$0.0018–0.0020 per screenshot**, and the
model classified every row correctly across all three images — including `Скарбничка`
and `Округлення залишку` as savings, `На свою картку *0000` as an own transfer, `+a five-figure sum` (an incoming transfer)
as income, an empty date header kept empty, and a row cut off at the edge flagged rather
than guessed.

**Verified — the brief's claims hold:**

| Claim | Evidence |
|---|---|
| A third modality reuses the repair loop | `pipeline._run_extraction_round[ResultT](..., parse: Callable[[str], ResultT])` is already generic; `_extract_text`/`_extract_voice` differ only in guards, request builder and post-processing |
| `MessageKind.PHOTO` already exists end to end | `core/models.py`; `mapping.to_incoming` maps `message.photo[-1].file_id` (largest size — now load-bearing, `photo[0]` is an unreadable thumbnail); `repo/models.Message.kind` already carries `photo` |
| Voice was added beside text without duplication | `voice.py` beside `text.py`, `voice_json_schema` beside `text_json_schema` (deliberately duplicated literals, ADR-0014 consequence), one shared `common.py` |
| `MODEL_VISION` is unset | `Settings` has `model_text`, `model_voice`, **no vision field at all**. It must be added (default `""`) plus a `vision_model_candidates` property and inclusion in `_forbid_free_model_ids`, or a `:free` vision id would slip past the ban that catches text and voice |
| Three real screenshots exist outside the repo | `~/finbot-vision-samples/photo_2026-08-24 14.41.{49,52,54}.jpeg`. **Filenames contain spaces** — they must be renamed on the way into a case set |
| `image_url` needs no port change | `LlmRequest.messages: tuple[Mapping[str, Any], ...]`; `openrouter._build_body` does `dict(message)` and never inspects `content`. Voice already sends a content-part list through it |

**Found — five things the brief did not name, three of them load-bearing:**

1. **Making photos `PENDING` without removing the `F.photo` handler produces two
   replies.** `handlers.build_router` registers `@router.message(F.photo)` →
   `UNSUPPORTED_MODALITY_REPLY`, answered *inline* by the fast lane, while the drain
   would separately send the confirmation. The handler and the constant must go in the
   same step that flips `_initial_status`.

2. **`_rerender_group` destroys any modality-specific confirmation body on the first
   tap.** It calls `render_confirmation(lines, today=today)` — no transcript, no summary.
   Consequence today, unreported: **a voice confirmation already loses its `🎤 «…»` line
   the moment anyone taps ✏️ or 🗑.** That is Stage 2's cosmetic bug, not fixed here, but
   it is decisive: any bank-specific prose inside the button-bearing message is
   guaranteed to vanish. This is what forces the two-message design.

3. **Committing the bank golden labels would publish real household finances.**
   `voice_v1.jsonl` is committed safely because its `expected` comes from an agreed
   script the owner deliberately spoke — synthetic content, private audio. A bank
   screenshot's labels are the opposite: real amounts, real merchants, a third party's
   name. Committing them contradicts **CLAUDE.md rule 4** and **ADR-0009**. The pixels
   being git-ignored does not help; the labels are the same data in another container.

4. **`--save-raw` is a hole in rule 4 for this modality.** ADR-0012's Stage-1 amendment
   permits fixture refresh "from synthetic golden cases only". There are none here, so
   `--modality bank --save-raw` would write real bank bodies into
   `tests/fixtures/openrouter/`. It must be refused mechanically.

5. **`repo/expenses.create` has no `ON CONFLICT` path** (ORM `session.add` + `flush`), so
   dedup-on-insert needs a second, keyed insert function; `create` stays untouched.

**Also verified, small but gate-relevant:** `bank_dates.py` needs the same
`per-file-ignores = ["RUF001","RUF002","RUF003"]` entry `currency.py` has, or
`ruff check .` fails on the Cyrillic month table. No new `Enum` column is introduced, so
ADR-0012's type-bound-CHECK exclusion list is **untouched**. `fake_session.py`'s
`voice_files: dict[str, bytes]` is a generic `file_id → bytes` map and serves an image
download unchanged; renaming it is churn and is not done. `Message.created_at` is
`DateTime(timezone=True)` with `server_default=func.now()`, so an arrival anchor needs no
new column. `expenses.occurred_at` is indexed, `amount` is not — the manual-duplicate
probe is a row-value `IN` over a handful of pairs, fine at this volume.

**Hypotheses, flagged rather than designed around:** Telegram's hard limit on keyboard
*rows* is undocumented, so `MAX_CONFIRMATION_ROWS = 12` is **not** raised. Monobank's
layout is unobserved — the prompt describes *a bank transaction feed*, carries Ukrainian
and Russian date forms, and assumes no fixed row geometry and no always-present time.
Cash withdrawals and refunds appear in no sample; the prompt rules (withdrawal →
`own_transfer`, refund → `income`) are reasoned, not measured, and both err toward *not
writing*. A screenshot sent as a **document** is silently ignored today
(`to_incoming` → `None`, and `PersistMessageMiddleware` short-circuits before any
handler); out of scope, recorded, and self-correcting — the user sees no reply and
re-sends as a photo.

**Tension with `docs/vision.md`, resolved not ignored:** vision.md excludes "bank or card
integrations" and "income, savings, investments". A screenshot is neither — no API, no
token, no account link, no sync; it is a capture modality for the same manual act, and
the owner declined both a bank token and CSV import. The second exclusion is what makes
Decision A ("only `expense` rows are written, nothing else is stored") the only reading
consistent with Truth.

---

## Requirements

**R1** A photo becomes `pending` and drains through the same ADR-0013 lane as text and
voice — same claim, same retries, same guarantee. No second pipeline, no second repair
loop, no second cost-accounting path.

**R2** One call, one strict JSON document, no tools, no loop (rule 1, ADR-0003). The
image rides as the content part the spike measured, with `response_format` strict and
`provider: {data_collection: deny, require_parameters: true}`.

**R3** Every row is classified into exactly one of five kinds. **Only `expense` rows are
written to `expenses`.** Everything else is reported and stored nowhere.

**R4** A row is written only if `kind == expense` **and** `partially_visible == false`
**and** `amount > 0` **and** its date resolved deterministically. Anything else is
counted and reported, never guessed.

**R5** Dates are not the model's job beyond transcription. The model returns the header
**verbatim**; code resolves it, infers the year as the most recent past occurrence, and
**verifies the header's own weekday against the computed date**. A mismatch means
unresolved → not written, reported.

**R6** Re-sending the same or an overlapping screenshot writes nothing new. Enforced by
the database, not by application logic.

**R7** A bank row colliding with a manually typed expense (same date, same amount) is
**never merged and never suppressed**. Both exist; the collision is named; the human
resolves it with 🗑.

**R8** The user always learns: what was recorded, what was skipped and why, how many rows
were cut off, how many were already recorded, and **which date the relative headers were
resolved against**.

**R9** A batch write is fully undoable in one tap, including above the 12-row cap.

**R10** With `MODEL_VISION` unset the bot refuses a photo with an explanation and a
findable `last_error`, before any download or model call — Stage 2's pattern.

**R11** No real household data enters the repository: not the images, not the labels, not
recorded response bodies. Enforced mechanically.

**R12** `MODEL_VISION` is chosen by an eval through the production request path, with a
pre-registered gate whose primary metric is *"was a non-expense wrongly recorded"*.

**R13** No new runtime dependency. No resizing, no OCR library, no Pillow.

**R14** R1–R11 are verifiable on a laptop with `pytest`, a fake LLM client and Docker.
No step depends on an owner prerequisite.

---

## Approaches

### A. Are non-expense rows persisted?

| | Option | Pros | Cons |
|---|---|---|---|
| A1 | **Nothing new is stored.** Non-expense rows live in `extractions.raw_response` (already written verbatim per attempt, rule 6) and in the reply | Zero schema cost. Already queryable. Respects vision.md's exclusions | "How much went to the jar this month" is a JSONB query, not a report |
| A2 | A `bank_rows` table holding every row | Direct SQL over transfers | **A fourth copy of data already in `extractions`** — the "two answers to one question" ADR-0013 §2 and ADR-0009 both rejected. Starts tracking income and savings, which vision.md excludes. A table nothing reads, migrated and backed up forever |
| A3 | Write them to `expenses` with a `kind` column, excluded from reports | One table | Catastrophic: every report and every future query must remember the predicate. One forgotten `WHERE` counts a five-figure transfer as spending |

**Chosen: A1.** Provenance is already satisfied by ADR-0006's three tables; a fourth
weakens the property ADR-0006 exists to protect. Visibility is a *reply* requirement.

### B. Date anchor, and who owns the calendar

| | Option | Pros | Cons |
|---|---|---|---|
| B1 | Model returns `occurred_at`, as text and voice do | Zero new code | Year inference becomes an unverifiable guess and `Сьогодні` has no checksum. Violates "never guess where you can compute" |
| B2 | **Model returns the header verbatim; code resolves and cross-checks the weekday** | Exhaustively testable with no model, no network, no Docker — the largest block of this stage's own verification. The weekday redundancy becomes a real checksum. The model is never told today's date, so it *cannot* resolve one | ~40 table entries. An unrecognised header means unresolved rows on an unseen layout |
| B3 | Both, and compare | Two signals | The disagreement case has no good rule, and it is a second thing to keep in sync |

Anchor within B2: `now()` at drain time versus **`message.created_at`** (arrival). Arrival
wins — a whole feed inherits one anchor, so a drain crossing midnight (backoff runs 30 s
to 30 min) would misdate *every* row by a day.

**Chosen: B2, anchored on `message.created_at` in the configured timezone.** Yesterday's
screenshot resolves `Сьогодні` to today and lands a day late; unavoidable, so R8 requires
the reply to **state the anchor date**, and the remedy is 🗑 + retype.

### C. Deduplication

| | Option | Pros | Cons |
|---|---|---|---|
| C1 | `SELECT` existing keys, filter, `INSERT` | No migration | The guarantee lives in application code. ADR-0012's reasoning: `ON CONFLICT DO NOTHING` against a unique index *is* the guarantee, and this project tests against real Postgres to rely on it |
| C2 | **`expenses.bank_txn_key` + `UNIQUE (user_id, bank_txn_key)`, keyed insert with `ON CONFLICT DO NOTHING … RETURNING id`** | One statement, database-enforced, idempotent under redelivery *and* a repair retry. A `None` return is exactly the "already recorded" counter R8 needs. NULLs are distinct in Postgres, so text and voice rows are untouched | One migration; a column meaningless for two of three modalities |
| C3 | A `bank_rows` table keyed on the natural key | Also covers non-expense rows | Rejected in A2 |

Key content: `date | time | amount` at two decimals — **merchant deliberately excluded.**
Merchant strings are the noisiest field, and OCR variance between two reads of the same
pixels would defeat dedup and **double-count money**, the worst outcome. Excluding it
trades that for a rare false collision (two same-amount transactions in one minute),
which under-counts *and is reported*. `user_id` sits in the index, not the string: two
banks are two ledgers.

**Chosen: C2.** For the screenshot↔manual case no key exists, so after inserting, probe
for non-deleted expenses with `bank_txn_key IS NULL` matching any written
`(occurred_at, amount)` pair and name them in the reply. Exact amount only — a rounded
manual entry ("150" for 148.50) will not match, and that is stated, not hidden.

### D. Reply shape, and the 12-row cap

Constraint from Reality check #2: anything modality-specific inside the button-bearing
message is destroyed by `_rerender_group` on the first tap.

| | Option | Pros | Cons |
|---|---|---|---|
| D1 | One message: rows + summary + warnings, buttons attached | ADR-0007 literally | The summary vanishes on the first tap. Fixing it means re-deriving the summary inside a callback handler, or grouping siblings by `bot_message_id`, which `handlers.py` documents as deliberately not done |
| D2 | **Two messages: a summary note (no buttons, never re-rendered), then the existing confirmation + keyboard** | The note survives every tap because nothing edits it. `_rerender_group` needs no modality knowledge. ADR-0007's actual rule — one confirmation per incoming message — is intact | One extra message per screenshot, once a day |
| D3 | One message per date group | Solves the cap by splitting | Breaks `_rerender_group`, which groups by incoming `message_id`: a tap on one group re-renders *all* rows into whichever message was tapped |

For the cap: today, >12 active rows makes `confirmation_keyboard` return `None` — **no
buttons at all**, so a misread batch of real money would be uncorrectable.

**Chosen: D2, plus one `🗑 Видалити все` row appended on the bank path and kept when the
per-row buttons are dropped above 12.** The cap stays 12. The >12 case degrades from
*uncorrectable* to *one-tap-undoable*, which is the property that matters for a stage
writing real money unattended. Passed as
`confirmation_keyboard(lines, delete_all_message_id=...)`, non-`None` only on the bank
path, so text and voice keyboards stay byte-identical and no existing test changes.

### E. Where the code lives, and photo ≠ receipt

Follow Stage 2: `core/extraction/bank.py` beside `text.py`/`voice.py`, its own prompt and
its own hand-built strict schema, sharing `_run_extraction_round`. The date resolver
splits into `core/extraction/bank_dates.py` for the reason `currency.py` is not inside
`text.py`: a pure calendar function with its own exhaustive table-driven test.

Naming matters because Stage 4 is also `MessageKind.PHOTO`. **Not** `photo.py`,
`image.py` or `vision.py` — Stage 4 would find the obvious name taken by the wrong thing.
`MODEL_VISION` is the *modality* (ADR-0004: one model per modality); bank-feed and
receipt are two *prompts* under it, which is what lets Stage 4 reuse the chosen model
without a second eval.

Between this stage shipping and Stage 4, a photographed receipt would run the bank prompt
and could write garbage. **Chosen: E2 — one boolean `is_transaction_feed` the model must
set.** False → write nothing, say so. Same cost as `partially_visible`, closes the one
hole that matters, and becomes Stage 4's natural seam. (E1, relying on `rows: []` for an
unrelated image, is true for a cat and weak for a receipt, which also shows amounts, a
merchant and a date.)

### F. Where the bank golden set lives

| | Option | Verdict |
|---|---|---|
| F1 | Commit `bank_v1.jsonl`, git-ignore only the images (voice's arrangement) | **Disqualified** — publishes real amounts, real merchants and a named third party |
| F2 | Commit anonymised labels | The amounts *are* the private data, and `amount_exact` needs the real numbers |
| F3 | Hand-draw synthetic screenshots, commit both | Measures a synthetic layout; the spike's whole value is that these are real. Worth doing later as a public smoke case |
| F4 | **The whole case file lives outside the repository.** `--cases` and `--images-dir` required, no defaults, refused inside the repo — the same guard and reasoning as ADR-0016's `--out` | One rule for pixels and labels alike, mechanically enforced and `pytest`-testable. Cost: a fresh clone cannot run this eval at all |

**Chosen: F4**, with the guard extracted into `evals/paths.py` and
`pull_voice_samples.py`'s private copy refactored onto it — one implementation of
ADR-0016's invariant, not two. Plus `--modality bank` **refuses `--save-raw`**.

### G. Fallback model list for vision

**Chosen: G1 — reuse `MODEL_FALLBACKS`** (what voice does), with the invariant stated
loudly in the property's docstring and in the deploy prerequisite: every listed fallback
must be multimodal, or an image silently routes to a text-only model. A third variable
would fix half the wart and make the codebase less consistent. `google/gemini-3.6-flash`,
today's fallback, is multimodal, so the invariant currently holds.

---

## Chosen approach

A1 + B2(arrival anchor) + C2 + D2(+delete-all) + E(`bank.py`, `bank_dates.py`,
`is_transaction_feed`) + F4 + G1.

**Wire schema** (`bank_json_schema(slugs)`, hand-built and strict, a fully independent
literal from the text and voice ones per ADR-0014's proximity-not-abstraction rule):

```
{ is_transaction_feed: bool,
  rows: [ { date_header: string,      // verbatim, e.g. "Сьогодні" / "Сб, 22 серпня"
            time: string|null,        // "HH:MM", null when not printed
            merchant: string,
            amount: number,           // absolute value, account currency, no separators
            kind: enum[expense,income,savings,own_transfer,transfer_out],
            category: enum[<13 catalog slugs, catalog order>],
            partially_visible: bool } ] }
```

Every object node carries `additionalProperties: false` and a `required` naming all its
properties. `category` is required by strict mode even for non-expense rows and is
ignored for them.

**Domain shapes** (`core/extraction/schema.py`): `BankRowKind(StrEnum)` with the five
wire values **plus a sixth, `UNCLASSIFIED`, the wire enum cannot produce** — a validator
coerces any unknown value to it with a WARNING, exactly as
`ExpenseDraft._fallback_unknown_category` coerces an unknown slug to `other`, because
filing one row as unclassified beats spending a repair call and losing a whole
screenshot. `BankRow`, `BankExtractionResult` (`frozen=True, extra="forbid"`).

**Pure classifier** `bank.plan_writes(result, *, anchor: date) -> BankPlan`: the
`ExpenseDraft`s to write with their keys, in the model's own row order (top to bottom,
matching the screenshot), plus counters `skipped_by_kind`, `cut_off`, `unresolved_date`,
`bad_amount`, `unclassified`, and the anchor. `BankSummary` is that plus the insert
results (`duplicates`, `manual_collisions`), filled by the pipeline.

**Date resolution** `bank_dates.resolve(header, *, anchor) -> date | None`:
`Сьогодні`/`Сегодня` → anchor; `Вчора`/`Вчера` → anchor − 1;
`[<weekday>, ]<day> <month-genitive>` → most recent occurrence at or before anchor, then
**weekday cross-check when a weekday is present** — mismatch returns `None`; an absolute
header with no weekday resolves without a checksum (safe: "most recent past" is only
wrong for a screenshot over a year old); anything unrecognised returns `None`. Tables
carry Ukrainian and Russian forms.

**Key:** `f"{occurred_at.isoformat()}|{time or ''}|{amount:.2f}"` in `String(64)`, unique
per `(user_id, bank_txn_key)`.

**Reply:** note first (always, for a photo), confirmation second (only when ≥1 row was
written). Zero-valued lines omitted, warnings capped at five with "…і ще N", the whole
note bounded as `transcript_line` is:

```
🧾 Скріншот за 24.08 — дати рахував від цього дня.
Записав: 4 (нижче).
Пропустив: скарбничка 2, переказ собі 1, переказ 1, надходження 1.
Обрізано на краю: 1 — не вгадував.
Вже було: 1.
Не зрозумів дату: 1.
⚠️ Можливий дубль: «кава» 150.00 за 24.08 уже записано вручну.
```

**Prompt** `extract_bank.v1.md` receives `$categories` only — **not `$today`**, because
the model must not resolve a date and cannot be asked to if it does not know today. Nine
rules: read only what is printed; header verbatim; time or null; amount absolute, account
currency, ignore any smaller original-currency line, drop the thousands space; merchant
as printed without trailing reference digits; the five kinds with "a cash withdrawal is
`own_transfer` — the money is not spent yet" and "never `expense` when unsure";
`partially_visible` with "leave unreadable fields empty rather than guessing"; `category`
meaningful only for `expense`; `is_transaction_feed` false → empty `rows`. It describes
*a bank transaction feed*, never one app's chrome.

**No foreign-currency guard on this path.** A feed's amounts are the account's own
currency by construction, and running the marker regex over merchant strings would only
misfire. Stated in `_extract_bank`'s docstring beside the two paths that do run it.

**`messages.raw_text` is not written for a photo.** Voice stores its transcript because
the transcript *is* the input; an image has none, the pixels are not archived (ADR-0009),
and the rows are already in `extractions.raw_response`. A caption is persisted by
`to_incoming` and **ignored** by extraction — a second input channel steering a
money-writing prompt is not worth it. "Caption as a date override" is a named non-goal.

Cost from the spike: $0.0018–0.0020 per screenshot; worst case for one message is
`max_extraction_attempts × max_message_attempts` = 10 billed image calls ≈ $0.02 —
ADR-0015's audio consequence restated for images, and left as-is for the same reason.

---

## ADR worthy: yes

1. **"Only `expense` rows are written; the other four kinds are shown, not stored"** — the
   classification decision, why `expenses` gains no `kind` column and the schema gains no
   fourth table (against ADR-0006), and why vision.md's exclusion of income, savings and
   transfers makes this the only consistent reading.
2. **"A bank-feed eval's labels are as private as its pixels"** — amends ADR-0009 and
   extends ADR-0016's guard from `--out` to `--cases`/`--images-dir`; records the
   `--save-raw` refusal and why the voice set's committed labels were safe when these are
   not.
3. **"The model reads the header; the code owns the calendar; the weekday is the
   checksum"** — the model/code split for dates, the arrival anchor and its known wrong
   case, and the `date|time|amount` key with the deliberate exclusion of merchant.

**Recurring technical rules that belong in the gate, not in prose** (both delivered as
tests in this stage): *a golden-set loader for a private modality must refuse a path
inside the repository*, and *`--save-raw` is only ever pointed at synthetic cases*.

**Not ADR-worthy, recorded in the roadmap instead:** Stage 4 inherits the
photo-disambiguation debt; Stage 5 gains an `education` candidate (an education service →
`other`); Stage 6 owns date and amount editing, the only real remedy for a wrong anchor;
the voice-transcript-line-lost-on-rerender bug is Stage 2's, for the reviewer to log.

---

## Owner prerequisites

No step depends on these. Everything in `## Steps` is verifiable on a laptop with Docker
and a fake LLM client.

1. **Build the private bank case set.** Copy the three screenshots out of
   `~/finbot-vision-samples/` into a directory outside the repo, renamed to case ids (the
   current names contain spaces), and hand-label every visible row into a `bank_v1.jsonl`
   in that same directory. `evals/golden/bank/README.md` carries the format. Optional:
   crop account balances out of future samples.
2. **Pre-flight the candidates against the live catalogue** — `GET /api/v1/models`,
   keeping only ids whose `architecture.input_modalities` contains `image` **and** whose
   `supported_parameters` contains `structured_outputs`. Two are required by name:
   `google/gemini-3.5-flash-lite` (the spike's, measured working) and
   `google/gemini-3.6-flash` (the standing pricier control). Add one or two from a
   different vendor **from that filtered list** — no further ids are hard-coded here,
   because the journal already records `gpt-5.6-luna` 404ing on every call despite
   appearing in the catalogue with `structured_outputs`. No `:free`.
3. **Run the eval and apply the pre-registered gate:**
   `python -m evals.run --modality bank --models <ids> --cases ~/…/bank_v1.jsonl --images-dir ~/… --repeats 2`
   - **Gate 1 — `no_false_expense` must be perfect.** A model that records a savings jar
     or a transfer as spending is disqualified at any price.
   - **Gate 2 — `schema_ok` and `amount_exact` perfect.** A wrong amount is wrong money.
   - **Gate 3 — among survivors, the cheapest; tie-break on p95 latency.**
   Then set `MODEL_VISION`. Post the table and the choice in `docs/journal.md` — **counts,
   costs and latencies only, never a merchant name or an amount.**
4. **Confirm `MODEL_FALLBACKS` is multimodal.** It is shared across modalities; a
   text-only fallback would silently ignore the image.
5. **Deploy:** `alembic upgrade head` (migration `0004`) plus the new `MODEL_VISION`.

---

## Steps

Gates for every step, output read, not assumed:

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

Docker must be running (ADR-0012: the DB tests fail rather than skip — no `skipif`, no
rerun plugin).

### Step 1 — The pure core: schema, prompt, classifier, calendar

**Create:** `core/extraction/bank.py`, `core/extraction/bank_dates.py`,
`prompts/extract_bank.v1.md`.
**Modify:** `core/extraction/schema.py` (`BankRowKind`, `BankRow`,
`BankExtractionResult`, `bank_json_schema`), `core/extraction/ports.py`
(`ImageFetchError`, docstring parallel to `AudioFetchError`), `prompts/__init__.py`
(`PROMPT_VERSION_BANK`, `render_bank_prompt(*, catalog)` — no `today`), `pyproject.toml`
(`per-file-ignores` for `RUF001/002/003` on `bank_dates.py`, `RUF001` on the new
Cyrillic-asserting tests).

Content exactly as `## Chosen approach` specifies. `bank.build_request(*, image_data_url,
catalog, models)` mirrors `voice.build_request` with an `image_url` content part and no
accompanying text part. `bank.parse_content` mirrors `voice.parse_content`.
`bank.bank_txn_key(...)` and `bank.plan_writes(result, *, anchor)` are pure.

**Verification this step brings:**

- `tests/unit/test_bank_schema.py` — the recursive strictness walk already applied to
  text and voice: every object node has `additionalProperties: false` and
  `sorted(required) == sorted(properties)`, no `$ref`/`$defs`, at least two object nodes
  so the walk cannot pass vacuously, a valid-instance round-trip, and the `kind` enum
  equals the five wire values and **does not** contain `unclassified`.
- `tests/unit/test_bank_dates.py` — table-driven: both relative forms in both languages;
  an absolute header resolving within the year; one crossing a year boundary (anchor
  `2026-01-05`, header `Сб, 27 грудня` → `2025-12-27`, weekday-checked); **a weekday
  mismatch returning `None`**; an absolute header with no weekday resolving anyway; an
  unrecognised header, an empty header and an impossible date (`31 лютого`) all returning
  `None`; controls that must resolve.
- `tests/unit/test_bank_plan.py` — the stage's most important test. Each of `savings`,
  `own_transfer`, `transfer_out`, `income`, `unclassified` produces **no** draft;
  `partially_visible` produces none; `amount <= 0` produces none; an unresolvable header
  produces none; every exclusion increments exactly its own counter; a multi-day result
  produces drafts across two dates in feed order; `is_transaction_feed: false` produces
  no drafts regardless of `rows`; an unknown wire `kind` coerces to `UNCLASSIFIED` with a
  WARNING rather than raising.
- `tests/unit/test_bank_key.py` — determinism, two-decimal formatting, `time=None` and
  `time=""` producing the same key, and two rows differing only by merchant producing the
  **same** key (pinning the deliberate exclusion, so a future "improvement" fails here
  and has to argue with the ADR).
- `tests/unit/test_prompt_render.py` extended — `render_bank_prompt` substitutes the
  catalog, and the template contains no `$today`/`$weekday` placeholder (pinning that the
  model is never told the date).

Nothing user-facing changes; a photo is still answered "Фото — скоро".

**Commit:** `feat: bank-feed schema, prompt, classifier and calendar resolver`

### Step 2 — It writes: storage, dedup, config, image fetch, pipeline

**Create:** `migrations/versions/0004_stage2_5_bank_txn_key.py`,
`adapters/telegram/images.py`.
**Modify:** `repo/models.py` (`Expense.bank_txn_key: Mapped[str | None]` `String(64)`,
`UniqueConstraint("user_id","bank_txn_key", name="uq_expenses_user_bank_txn_key")`),
`repo/expenses.py` (`create_bank_row(...) -> int | None` via
`postgresql.insert(...).on_conflict_do_nothing(...).returning(Expense.id)`;
`manual_duplicate_candidates(session, pairs)` filtered on
`deleted_at IS NULL AND bank_txn_key IS NULL` with a row-value `IN`), `config.py`
(`model_vision: str = ""`, `vision_model_candidates` with the shared-fallback invariant
in its docstring, inclusion in `_forbid_free_model_ids`), `core/extraction/pipeline.py`
(`_extract_bank`, routing on `MessageKind.PHOTO`, `anchor_date`, `fetch_image`,
`VISION_NOT_CONFIGURED_ERROR`, `ExtractionOutcome.vision_not_configured` and
`.bank_summary`), `.env.example`.

**Migration `0004`:** add the nullable column and the unique constraint. **No backfill** —
existing rows are text and voice, and `NULL` is the honest value. Deliberately the mirror
image of `0002`'s lesson: there a server default made old rows claimable; here nothing
changes meaning for a single existing row.

**`images.py`** — the only module touching aiogram's download API for a photo, parallel
to `audio.py`: `download_photo(bot, file_id)` (in-memory, no temp file, 20 MB ceiling
matching Telegram's own `getFile` limit), `sniff_mime(data)` (JPEG `FF D8 FF`, PNG
`89 50 4E 47 0D 0A 1A 0A`, WebP `RIFF….WEBP`; anything else → `ImageFetchError`),
`to_data_url(data)`, `fetch_as_data_url(bot, file_id)`. **No resizing, no new
dependency.** `core` imports only `ImageFetchError` from `core.extraction.ports`, never
this module (rule 3).

**`_extract_bank` order**, mirroring `_extract_voice`: empty `models` →
`mark_skipped(VISION_NOT_CONFIGURED_ERROR)`, no download, no call. `fetch_image is None`
or `anchor_date is None` → `ValueError` (the existing `fetch_audio is None` idiom).
`ImageFetchError` → `_schedule_retry`, **no `extractions` row** (nothing reached a model).
Then `_run_extraction_round(parse=bank.parse_content,
prompt_version=PROMPT_VERSION_BANK)`, `plan_writes`, keyed inserts counting `None`
returns as duplicates, the manual-collision probe over the rows actually inserted,
`mark_done`, one commit. `expense_ids` and `drafts` stay index-aligned for
`zip(strict=True)`.

**`_initial_status` is NOT changed in this step** — photos stay `skipped`, so the running
bot is unaffected and no message can reach a pipeline whose adapter is not yet wired.

**Verification this step brings:**

- `tests/fixtures/openrouter/bank_feed_ok.json`, `bank_multi_day.json`,
  `bank_not_a_feed.json` — whole recorded bodies, **hand-written from the documented
  schema with invented merchants and amounts** (`--save-raw` is not an option here).
  `tests/fixtures/openrouter/README.md` updated.
- `tests/integration/test_bank_pipeline.py` — exactly the expense rows written with the
  right amounts, dates and categories; the non-expense rows **absent from `expenses` and
  present in `extractions.raw_response`** (asserting A1's provenance claim rather than
  assuming it); one `extractions` row `status='ok'` with the served `model_id` and
  `cost_usd`; `messages.status='done'`; `messages.raw_text` untouched; the multi-day
  fixture producing rows on two dates.
- `tests/integration/test_bank_dedup.py` — the same body twice writes nothing the second
  time and reports the count; an overlapping body writes only the new rows; two rows in
  *one* body sharing a key insert once and are counted; a manually typed expense with the
  same date and amount is **left alone and reported**, both rows existing; a bank row that
  was 🗑'd is **not resurrected** by re-sending (the unique constraint covers deleted rows
  — pinned, because it is a choice, not an accident).
- `tests/integration/test_bank_guards.py` — `MODEL_VISION` unset marks `skipped` with
  `last_error='vision_not_configured'` and **no download and no model call** (proven by
  `FakeLlmClient` raising on any unscripted call and the stub `fetch_image` recording zero
  invocations); `ImageFetchError` schedules a retry and writes no `extractions` row.
- `tests/unit/test_images.py` — mime sniffing per format, unknown magic raising, the
  size ceiling, a `None` from `bot.download`, the data-URL prefix.
- `tests/unit/test_settings.py` extended — `vision_model_candidates` empty when unset,
  `(model_vision, *fallbacks)` when set, a `:free` vision id refused at startup.

**Commit:** `feat: bank rows persist with a database-enforced dedup key`

### Step 3 — The human sees it: the note, the confirmation, the undo, the switch

**Modify:** `repo/messages.py` (`_initial_status`: `PHOTO → PENDING`),
`adapters/telegram/runner.py` (photo branch: `models = settings.vision_model_candidates`,
`fetch_image`, `anchor_date = message.created_at.astimezone(settings.tz).date()`; the
`vision_not_configured` reply; note-then-confirmation order; `set_bot_message_id` stamped
from the **confirmation**, not the note), `adapters/telegram/render.py`
(`render_bank_note(summary, *, anchor)`, `VISION_NOT_CONFIGURED_REPLY`,
`NOT_A_BANK_FEED_REPLY`, `HELP_TEXT` gains a screenshot line, **`UNSUPPORTED_MODALITY_REPLY`
deleted**), `keyboards.py` (`confirmation_keyboard(lines, *, delete_all_message_id=None)`
— the delete-all row appended when given, surviving the >12 drop), `callbacks.py`
(`MessageAction(prefix="msg")`, `action: Literal["delall"]`, `message_id: int`),
`handlers.py` (**remove `@router.message(F.photo)`**, add the `delall` handler,
`_rerender_group` reads `messages.kind` and re-passes `delete_all_message_id` for a photo).

**`delall` handler:** loads `expenses.siblings(message_id)`, and for each row not already
deleted writes a `corrections` row and soft-deletes it; one commit; `_rerender_group`;
`query.answer("Видалив усе")`. Idempotent under a redelivered tap. Wrapped in the
module's standard `try/except → CALLBACK_FAILURE_REPLY`.

**Verification this step brings:**

- `tests/integration/test_bank_flow.py` — through the real `Dispatcher.feed_raw_update`
  with `FakeSession`, never by calling handlers: a photo persists as `pending`; the drain
  sends **exactly two** messages, note first; the note names the anchor date and every
  non-zero counter; the confirmation carries the numbered rows and a keyboard whose last
  row is `🗑 Видалити все`; **no second reply from any inline handler** — the regression
  test for Reality check #1, which would have shipped two contradictory answers to one
  screenshot.
- `tests/integration/test_delete_all_flow.py` — the callback soft-deletes every sibling,
  writes one `corrections` row per row, re-renders struck through with no keyboard, and
  answers; a second identical tap writes no further corrections and still answers.
- `tests/unit/test_render_bank.py` — the note's wording for a clean screenshot, every
  skip reason at once, zero written rows (no "нижче"), `is_transaction_feed: false`, more
  than five manual collisions (capped), and a note bounded well under Telegram's 4096
  limit for a worst-case 20-row feed.
- `tests/unit/test_keyboards.py` extended — with `delete_all_message_id`: 3 rows → 3 rows
  + delete-all; 13 active rows → **only** delete-all; without it, output byte-identical
  to today's.
- `tests/unit/test_mapping.py` extended — `to_incoming` picks the **largest** `PhotoSize`.
- `tests/integration/test_message_repo.py` extended — a new photo is `pending`; a
  pre-existing `skipped` photo row is **not** claimable (the no-backfill decision, pinned).
- `tests/integration/test_telegram_flow.py` updated — the "photo answered
  `UNSUPPORTED_MODALITY_REPLY`" assertion is replaced, not deleted: a photo now produces a
  `pending` row and **no inline reply at all**.
- `tests/unit/test_main.py` — the `allowed_updates` and no-`dp.errors` tests must stay
  green after the router change.

**Commit:** `feat: bank screenshots reach the drain, with a note and a one-tap undo`

### Step 4 — The eval, the guard rails, the record

**Create:** `evals/paths.py` (`REPO_ROOT`, `ensure_outside_repo(path, *, flag)`,
`RepoPathError`), `evals/golden/bank/README.md`.
**Modify:** `evals/pull_voice_samples.py` (its private `_REPO_ROOT`/`_validate_out_dir`
refactored onto `evals/paths.py`; behaviour and its existing test unchanged),
`evals/scoring.py` (`BankGoldenCase`, `BankCaseScore`, `BankModelResult`,
`load_bank_golden_cases`, `score_bank_case`, `failed_bank_case_score`, `aggregate_bank`,
`render_bank_table` — parallel hand-maintained shapes, deliberately not the text/voice
ones with fields bolted on), `evals/run.py` (`--modality bank`, `run_bank_case`,
`run_bank_model`, required `--cases`/`--images-dir` both through `ensure_outside_repo`,
`--save-raw` refused for this modality with one clear line), `evals/README.md`,
`.gitignore` (an explicit `evals/golden/bank/*` + `!README.md` block), `docs/roadmap.md`
(**Stage 2.5 inserted between Stage 2 and Stage 3; Stage 4 untouched**),
`docs/journal.md`.

**Case format** (documented in `evals/golden/bank/README.md`; the file itself lives
outside the repo):

```json
{"id": "privat-day-01", "image": "privat-day-01.jpeg", "anchor_date": "2026-08-24",
 "is_transaction_feed": true,
 "rows": [
   {"kind": "savings",      "amount": "6.35",   "partially_visible": false},
   {"kind": "own_transfer", "amount": "123.60", "partially_visible": false},
   {"kind": "expense",      "amount": "320.50", "category": "groceries",     "occurred_offset_days": 0,  "partially_visible": false},
   {"kind": "expense",      "amount": "43.19",  "category": "subscriptions", "occurred_offset_days": 0,  "partially_visible": true}
 ]}
```

`rows` labels **every visible row** in feed order. Amounts are JSON **strings**, never
bare numbers. `anchor_date` is **absolute and per-case** — the one place this stage must
diverge from the committed sets' run-date-relative rule, because a screenshot's absolute
headers are baked into the pixels: offsets relative to the *run* date would drift a day
every day, while offsets relative to a per-case anchor are stable and exercise exactly
what production does. `--today` therefore does not apply to `--modality bank`.

**The runner converts nothing** (unlike voice) but must still exercise the production
path: it reads the image, builds the data URL through
**`finbot.adapters.telegram.images.to_data_url`/`sniff_mime` themselves** — imported,
never reimplemented — and calls `bank.build_request`, `bank.parse_content` and
`bank.plan_writes`. One call per case, no repair loop. Files and mime sniffing happen
eagerly in `load_bank_golden_cases`, before the first `client.complete`. Dedup is out of
scope for the eval (it needs Postgres).

**Metrics.** `count_exact`, `kind_exact`, `dropped_exact`, `category_exact`, `date_exact`
(positional, computed only when the relevant count holds), `expense_count_exact`,
`amount_exact`, `feed_ok`, mean cost, latency p50/p95, and:

> **`no_false_expense` — set-based, and deliberately asymmetric.** Every amount
> `plan_writes` would *write* must appear, with multiplicity, among the amounts labelled
> `expense` and fully visible. It counts one direction only — money recorded that was
> never spent — and it is scoreable even when the model miscounts rows, which is exactly
> when the positional metrics go blind. This is the metric the model choice turns on,
> because a savings jar written as spending costs more than a mislabelled category ever
> will.

**Verification this step brings:**

- `tests/unit/test_evals_bank.py` — with `tmp_path` (naturally outside the repo) and a
  tiny synthetic JPEG/PNG built in the test: `load_bank_golden_cases` **refuses a
  `--cases` or `--images-dir` inside the repository** (table-driven over the repo root
  itself, a subdirectory, and a symlinked/relative path); a missing image raises naming
  the file; an unrecognised format raises; a bare-number `amount` raises `TypeError`; a
  missing `anchor_date` raises. Plus an **identity pin** — the loader's data-URL builder
  *is* `finbot.adapters.telegram.images.to_data_url`, the same shape as the existing pin
  on `convert_to_mp3`.
- `tests/unit/test_evals_bank_scoring.py` — `no_false_expense` fails when a `savings` row
  is classified `expense` **while every other metric still passes** (proving the metric is
  not redundant with `amount_exact`); it passes when the model merely *misses* an expense
  (asymmetry pinned); `dropped_exact` catches a cut-off row that was guessed; `date_exact`
  runs through the production resolver against `anchor_date`, including a weekday-mismatch
  case; `feed_ok` catches a receipt labelled as a feed.
- `tests/unit/test_evals_run.py` extended — `--modality bank --save-raw` exits non-zero
  with one clear line and opens no socket; a bank run through `FakeLlmClient` produces a
  table, spending nothing.
- `tests/unit/test_pull_voice_samples.py` stays green across the `evals/paths.py`
  refactor — the proof that ADR-0016's guard was moved, not weakened.

Journal entry (five lines plus `## Learning notes` on the model-reads-the-header /
code-owns-the-calendar split, and on why the eval's labels had to leave the repository).
Roadmap: Stage 2.5's **Done when:** *one day's screenshot lands as the right expenses,
savings and transfers visibly skipped, and re-sending the same screenshot records nothing
new* — with the standing note that no gate can prove it and it has not been tried.

**Commit:** `feat: bank eval with a no-false-expense gate, and one guard for private sets`

**Then:** merge once all three gates are green and review is clean.

**Hand-off to `doc-curator`:** three ADRs, listed under `## ADR worthy`.

---

## Status: Done
