# Stage 1 — MVP: text → expense: implementation plan

> **For the worker:** every design decision in this document is already made. Do not
> re-decide. If something here contradicts `CLAUDE.md`,
> `docs/specs/2026-08-09-expense-capture-design.md` or an ADR, stop and escalate at the
> BLOCK bar (`.claude/orchestration.md` → `## Escalation`).

**Goal:** a message like *"хліб 50 і таксі 200"* becomes two `expenses` rows and **one**
numbered confirmation with ✏️/🗑 buttons that work; every model call is recorded in
`extractions` with its real cost; `/day` `/week` `/month` answer from SQL; no update can be
acknowledged before it is durably stored.

**Branch:** `stage-1-text-to-expense`.

---

## Reality check

Verified means the file was read, or the API was fetched today and quoted.

| Claim | Verdict | Evidence |
|---|---|---|
| `AllowlistMiddleware` and `PersistMessageMiddleware` both gate on `update.message is None` | **Verified — worse than stated** | `middlewares.py:52` returns `None` when `update.message is None`; line 102–103 same. A `callback_query` dies at the *first* middleware, before the session opens. No log, no reply |
| `main.py` passes `allowed_updates=["message"]` | **Verified** | `main.py:49`. Telegram would not even deliver the tap |
| aiogram advances the offset before handlers finish (ADR-0011) | **Verified in installed source** | `dispatcher.py:247-253` — `get_updates.offset = update.update_id + 1` runs immediately after `yield update`; `_polling` runs handlers as background tasks (397-401); `_process_update` (319-332) catches everything and returns `True` |
| A global `dp.errors` handler would silently break offset withholding | **Verified, and it is a trap** | `ErrorsMiddleware` is registered **first** on `dp.update` (`dispatcher.py:80`). It re-raises **only** when no error handler is registered (`middlewares/error.py:36-38`). **This plan forbids `dp.errors` and tests for it** |
| `Dispatcher.resolve_used_update_types()` exists | **Verified** | `router.py:133` |
| `feed_raw_update` re-raises handler exceptions | **Verified** | `dispatcher.py:186-195` → `_feed_webhook_update` (420-434) logs then `raise` |
| `data["event_from_user"]` is set for callbacks | **Verified** | `middlewares/user_context.py:91-109`. **Not used** — Stage 0's rule is to read the `Update` directly, so nothing depends on aiogram's registration order |
| `callback_query.message` can be `InaccessibleMessage` | **Verified** | `types/callback_query.py:30`. Carries `chat` + `message_id`, `date == 0`. **Never narrow it** — edit by `(chat_id, message_id)` |
| `callback_query.message.from_user` is the sender to check | **FALSE, and the trap** | For a button on a bot message that field is the **bot**. The tapper is `callback_query.from_user` (`types/callback_query.py:26`). Checking the wrong one rejects both household members |
| OpenRouter needs `usage: {include: true}` for cost | **FALSE as of today** | Usage-accounting docs: *"The `usage: { include: true }` and `stream_options: { include_usage: true }` parameters are deprecated and have no effect. Full usage details are now always included automatically."* Send nothing; read `usage.cost` |
| `usage.cost` is USD | **Verified** | Cost is "in credits"; 1 credit = $1 |
| `usage.cost` is always present | **FALSE** | Response schema types `cost` as nullable. `extractions.cost_usd` must be nullable, and a failed transport call has no cost at all |
| The response names the model that actually served | **Verified** | *"Requests are priced using the model that was ultimately used, which will be returned in the `model` attribute"*. **`extractions.model_id` comes from the response, never from config** |
| Structured output shape | **Verified verbatim** | `{"type":"json_schema","json_schema":{"name":…,"strict":true,"schema":{…,"required":[…],"additionalProperties":false}}}` |
| Provider data policy per request | **Verified verbatim** | `provider.data_collection: "allow" \| "deny"` (default `"allow"`), `provider.require_parameters: boolean` (default `false`) |
| Structured-output support is per **endpoint**, not per model | **Verified** | *"the same model may be served by multiple providers, and only some of those providers may support structured outputs"* — which is why `require_parameters: true` is mandatory |
| Per-key spend cap exists | **Verified** | `GET /api/v1/key` returns `limit`, `limit_remaining`, `limit_reset` |
| `:free` variants are rate-limited and likeliest to train | **Verified (limits)** | *"20 requests per minute, 50 per day"* under 10 credits. This plan bans `:free` ids, with a test |
| Candidate model ids exist today | **Verified 2026-08-10** against `GET /api/v1/models` | `google/gemini-3.5-flash-lite` ($0.30/$2.50 per M), `openai/gpt-5.6-luna` ($0.10/$0.60), `qwen/qwen3.7-flash` ($0.03/$0.13), `google/gemini-3.6-flash` ($1.50/$7.50) |
| A `supported_parameters` array lists `structured_outputs` | **NOT verified** | The web filter `openrouter.ai/models?supported_parameters=structured_outputs` is documented and real; the **JSON field name** is inferred. Prerequisite check only; no code depends on it |
| `pydantic.model_json_schema()` is strict-mode ready | **FALSE** | Pydantic emits `$defs`/`$ref`, omits `additionalProperties: false`, renders `Decimal` as `anyOf[number,string]`. A derived schema **must be post-processed, and that post-processing tested** |
| `aiohttp` is already in the tree | **Verified** | `aiohttp-3.14.3`, pulled by aiogram, ships `py.typed`. Declaring it adds **no package to the image** |
| Installed toolchain | **Verified** | aiogram 3.30.0, SQLAlchemy 2.0.51, alembic 1.19.1, pydantic 2.13.4, pytest 9.1.1, mypy 2.3.0, ruff 0.16.2 |
| Stage 0 harness reusable | **Verified, one required edit** | `tests/conftest.py:66` truncates only `messages, users`. Stage 1 tables must be added — **and `categories` must never be truncated**, because the migration seeds it |
| ADR-0012's drift-guard exception list must grow | **Verified consequence** | Two more `Enum(native_enum=False)` columns arrive; same spurious diff for each |

### Contradictions with Truth, resolved here rather than silently

1. **Spec §5 gives `messages` no processing state.** The inbox mechanism adds four columns.
   Authorised by ADR-0011, which defers the mechanism to Stage 1 and requires a new ADR;
   recorded as ADR-0013.
2. **Spec §10 / ADR-0005 mention `proposed_category`.** The roadmap puts proposals in Stage
   5. Stage 1 ships none; `other` is the escape hatch. `categories` still gets `status`,
   `merged_into_id`, `created_by`, so Stage 5 needs no migration to it.
3. **Spec §9 / ADR-0008 require FX.** Stage 1.5. `expenses` gets every currency column now,
   filled with UAH constants, so Stage 1.5 changes values and adds `fx_rates` — never
   `ALTER TABLE expenses`. **`fx_rates` is not created**: Stage 0's precedent is that a
   table with no writer is the schema equivalent of an empty package.

---

## Requirements

1. **R1 — Delivery.** An update is acknowledged **only after** its durable record is
   committed. Processing happens from the table, with retries. ADR-0011 closed.
2. **R2 — `callback_query` reaches handlers.** Middleware chain, `allowed_updates`, and a
   test that makes the Stage-0 bug class unmergeable.
3. **R3 — Extraction.** One call, fixed Pydantic schema, `response_format` strict JSON
   schema derived from it, repair loop of at most two attempts (spec §4.3).
4. **R4 — Thirteen categories**, seeded by migration, injected into the prompt, enforced by a
   JSON-Schema `enum` — not by asking politely (ADR-0005 made mechanical).
5. **R5 — Every call recorded** in `extractions` with `model_id` (from the response),
   `prompt_version`, `attempt`, `status`, `cost_usd`, `latency_ms`, `raw_response`.
6. **R6 — Money is `numeric`, never `float`** — including across the JSON boundary, which is
   the only place rule 2 can actually be violated. Enforced by a test.
7. **R7 — One confirmation per message**, numbered, per-row ✏️/🗑 (ADR-0007).
8. **R8 — `/day` `/week` `/month`** are SQL (rule 5), `Europe/Kyiv`.
9. **R9 — Zero expenses asks, never stays silent** (spec §7).
10. **R10 — The test suite touches no network.** The LLM client gets the `FakeSession`
    treatment: transport replaced, real object graph, unexpected call fails loudly.
11. **R11 — Eleven golden cases and a runner** good enough to choose a model by measurement.
12. **R12 — Out of scope:** currencies beyond UAH, voice, photos, the full eval harness,
    category proposals, judge metrics, any web UI.

---

## Approaches

### A. Closing ADR-0011 — the delivery mechanism

| Option | Pros | Cons |
|---|---|---|
| **A1. Custom polling loop withholding the offset until the unit of work commits** | ~30 lines; no schema change | Processing becomes the unit — an OpenRouter outage means the offset never advances and **the queue head blocks every later message**. A poison update blocks forever. A crash after ack and before processing still loses the update |
| **A2. A new `outbox`/`inbox` table of raw update JSON** | Survives a crash between ack and processing | A second table duplicating `messages`, which already stores what arrived, already has `UNIQUE(telegram_update_id)`, and already commits before any handler |
| **A3. `messages` *is* the inbox** ✅ | Everything A2 gives, on a table Stage 0 already built. Four columns and a drain task. Retries are per message with backoff, so a dead provider delays one message, not the queue. Poison messages end at `status='failed'`, loudly. `UNIQUE(telegram_update_id)` turns at-least-once into effectively-once | Needs a claim query and a status machine; a crash mid-processing leaves a `processing` row (handled by a startup reset) |

**Chosen: A3.** ADR-0011 asked for *"an outbox: persist the raw update, acknowledge, and
process from the table with retries"* — A3 is exactly that, on the table that already plays
the role. A second table beside `messages` would create two answers to "what arrived", which
is what ADR-0006 exists to prevent.

Both A1 and A3 require replacing `dp.start_polling`. The difference is *what* the ack waits
for:

> **The guarantee, in one sentence:** an update is acknowledged only once its durable record
> is committed; everything after that — replies, reports, button handling, extraction — is
> best-effort and recoverable from `messages`.

```python
try:
    await feed(update)
except PersistenceError:      # raised only by PersistMessageMiddleware
    raise                     # abort the batch; offset unchanged; Telegram redelivers
except Exception:
    logger.exception(...)     # handler / Telegram failure: loud, but must not block the queue
```

Without that split, one failing `sendMessage` would wedge the household's bot forever.

### B. Where extraction orchestration lives

Rule 3 bans `core → adapters` and `core → llm`. It does **not** ban `core → repo`, and spec
§3 assumes it.

| Option | Verdict |
|---|---|
| A new `src/finbot/service/` layer | **Rejected** — contradicts `CLAUDE.md`'s layout, needs an ADR for no gain |
| Orchestrate inside `adapters/telegram/` | **Rejected** — `evals/` and the Stage 6 HTTP adapter would each need a copy, and a divergent eval path measures nothing |
| **`core/extraction/pipeline.py` depending on the client through a `Protocol` in `core/extraction/ports.py`** ✅ | `core` imports `repo` (allowed) and its own port; `llm/openrouter.py` satisfies it structurally. `test_layering.py` stays green with no exception. mypy proves conformance |

### C. Fixtures for the LLM fake: recorded vs hand-written

Recorded responses beat hand-written ones, because the failures they catch are *shape*
failures — `content` being a **string** containing JSON rather than an object, a provider
wrapping it in a ```json fence, `usage.cost` arriving as `null`, an error body with a
different envelope. Nobody invents those correctly.

But no key exists yet, and prerequisites must not gate steps:

- Fixtures are **whole HTTP response bodies** in `tests/fixtures/openrouter/*.json`, never
  hand-written `ExpenseDraft` objects. The unit under test is the parser, so the input must
  be what the wire carries.
- They are **seeded** in Step 2 from the documented response schema, so the suite runs today.
- Step 4 ships `python -m evals.run --save-raw DIR`, writing real bodies from the
  **synthetic golden cases**. Refreshing is an owner prerequisite, not a step. Real
  household data never enters the fixtures (ADR-0009).

### D. What ✏️ does in Stage 1

| Option | Verdict |
|---|---|
| Full edit via FSM text input | **Rejected for Stage 1** — a new parsing surface and a state machine, for the field *least* often wrong |
| Reply-to-message correction parsed by the model | **Rejected** — a second extraction path, and a second thing to evaluate |
| **✏️ → one-tap category picker; 🗑 → soft delete; amount fixes are 🗑 + retype** ✅ | Vision says *"one tap fixes it"* — category **is** one tap, amount is not. Category corrections are also the highest-value labels (ADR-0006), feeding `category_exact` |

Record this in the journal so it is visible when Stage 6 revisits editing.

---

## Chosen approach

- **Delivery:** own the polling loop; `messages` is the inbox; a second asyncio task drains
  `pending` rows. Both in one `asyncio.TaskGroup`, one stop event, SIGTERM-clean.
- **Lanes:** `callback_query`, `/ping`, `/day|/week|/month` and unsupported-modality replies
  are answered **inline** in the dispatcher (fast, no LLM). Plain text goes to the **inbox
  lane** (slow, costs money, external dependency). Stages 2 and 4 inherit the lane.
- **Extraction:** `core/extraction/` builds the request and parses the response (pure);
  `llm/openrouter.py` performs it; `core/extraction/pipeline.py` orchestrates and persists.
- **New declared runtime dependency:** `aiohttp` — already installed via aiogram, promoted
  from transitive to declared. **No new package enters the image.** `httpx` rejected for
  exactly that reason.
- **Money:** `Decimal` end to end, `json.loads(text, parse_float=Decimal)` at the JSON
  boundary, `numeric` in Postgres, enforced by an AST test.

## ADR worthy: yes

**ADR-0013 — The `messages` table is the inbox: acknowledge on durable write, process from
the table.** **Supersedes ADR-0011.** Records: the two options ADR-0011 named and why the
second won; the guarantee sentence; that `PersistenceError` is the only exception that
withholds the offset; the five-value status machine and the startup reset; that redelivery
is a no-op via `UNIQUE(telegram_update_id)`; and that a global `dp.errors` handler would
silently void the guarantee, which is why a test forbids it.

**ADR-0014 — Structured output under a schema derived from the domain model; the model is
measured in `evals/`, not in `pytest`.** Records: `response_format` with `strict: true` plus
`provider.require_parameters: true` (support is per endpoint); `additionalProperties:false`
+ full `required` are not produced by Pydantic and must be derived, so the derivation is
tested rather than trusted; the category `enum` is what makes ADR-0005 mechanical;
`model_id` comes from the response; `json.loads(parse_float=Decimal)` is how rule 2 survives
the JSON boundary; and the pytest/evals split — **pytest proves plumbing deterministically
against recorded bodies and never calls a model; `evals/` measures the model, costs money,
and is never a gate before Stage 3.**

**Amendment to ADR-0012**, not a new ADR: the standing test pattern gains a fake **LLM**
client alongside the fake **transport** session, recorded response bodies, and two new
executable rules (`tests/unit/test_no_float_money.py`, the `allowed_updates` equality test).

---

## Owner prerequisites — separate from the steps; nothing below blocks the worker

Every step is verifiable on a laptop with Docker and a fake LLM client. These are needed
only for production and for refreshing recorded fixtures.

1. **Account.** Create an OpenRouter account, verify the email.
2. **Data policy — before the first real message.** This is household financial data
   (ADR-0004 names this consequence).
   - **Settings → Privacy**: leave *"OpenRouter Use of Inputs/Outputs"* **off** (it offers a
     1% discount in exchange for training rights — decline it) and *"Private Input & Output
     Logging"* **off**.
   - Same page: set the model data policy to **disallow routing to providers that may train
     on your data**, for **both** the paid and the free settings — separate toggles.
   - Do **not** use `:free` variants: worst data policies, hard rate limits. The code rejects
     any configured id ending in `:free`.
   - Belt and braces, already in the code: every request sends
     `"provider": {"data_collection": "deny", "require_parameters": true}`.
3. **Credits and a hard cap.** Buy **$10**. Turn **auto-top-up off**. Create a **dedicated
   API key for finbot** and set a **per-key credit limit of $5** — a runaway loop then burns
   the key's limit, not the balance. Verify:
   ```bash
   curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY" | jq
   ```
   `limit`, `limit_remaining`, `limit_reset` must be populated.
4. **`.env`.** Add `OPENROUTER_API_KEY=…` and `MODEL_TEXT=…`. Never into the repository.
5. **Confirm the candidate ids exist and their endpoints support structured outputs:**
   ```bash
   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[].id' | grep -E 'flash-lite|luna|qwen3.7-flash|gemini-3.6-flash'
   ```
   For structured-output support look for a `supported_parameters` array containing
   `structured_outputs`; **the field name is unverified** — if absent, use the documented web
   filter `https://openrouter.ai/models?supported_parameters=structured_outputs`. Replace any
   dead id in `evals/run.py`'s candidate list and say so in the journal.
6. **Refresh the recorded fixtures** once the key works:
   ```bash
   python -m evals.run --models google/gemini-3.5-flash-lite --save-raw tests/fixtures/openrouter
   ```
   Commit them. They come from synthetic golden cases only.
7. **Still open from Stage 0:** the `pg_dump` copy lives on the same host as the database.
   Out of scope here.

---

## Decisions taken (do not re-open)

**The thirteen categories.** Slug is the stable identifier used in the prompt, the JSON-Schema
`enum`, `evals/golden/` and reports. The Ukrainian label is presentation and lives in the
adapter; the description steers the model and lives in the prompt.

| slug | emoji | Ukrainian label | covers |
|---|---|---|---|
| `groceries` | 🛒 | Продукти | супермаркет, ринок, вода, продукти додому |
| `dining_out` | 🍽 | Кафе і доставка | кава, ресторан, доставка їжі, бізнес-ланч |
| `transport` | 🚕 | Транспорт | таксі, метро, паливо, парковка, СТО, квитки |
| `housing` | 🏠 | Житло | оренда, комуналка, інтернет, ОСББ, ремонт |
| `health` | 💊 | Здоровʼя | аптека, лікар, аналізи, стоматолог, оптика |
| `household` | 🧴 | Дім і побут | побутова хімія, гігієна, посуд, меблі, інструменти |
| `clothing` | 👕 | Одяг | одяг, взуття, аксесуари, хімчистка, ремонт взуття |
| `entertainment` | 🎬 | Дозвілля | кіно, концерти, книги, ігри, спорт, хобі, подорожі |
| `subscriptions` | 📱 | Підписки | мобільний, стримінг, софт, хмара, абонементи |
| `gifts` | 🎁 | Подарунки і донати | подарунки, донати на ЗСУ, благодійність |
| `pets` | 🐾 | Тварини | корм, ветеринар, грумінг |
| `hookah` | 💨 | Кальян | кальянна, тютюн, вугілля, обслуговування |
| `other` | 🗂 | Інше | усе, що не підходить вище — **обовʼязковий fallback, не вигадувати нове** |

`hookah` is narrower than its neighbours on purpose: the owner asked for it by name, which
means the household spends on it often enough to want it visible in a report rather than
absorbed into `dining_out`. `other` stays last in catalog order — the schema `enum` follows
catalog order, and the fallback reading last is the one thing a reader should be able to
rely on.

Single source of truth: `src/finbot/core/categories/catalog.py`. The migration spells the
thirteen literally (migrations never import `finbot`), and
`tests/integration/test_categories_seed.py` asserts the seeded rows equal the catalog, so
the two cannot drift.

**Prompt layout and versioning.** `src/finbot/prompts/extract_text.v1.md`, loaded by
`prompts/__init__.py::load(name)`. Rendered with `string.Template.substitute` — **not**
`str.format`, because the prompt contains JSON braces. `prompt_version` is the literal
`"extract_text.v1"`, passed into every `extractions` row. A prompt change means a new file
and a new version string. The category list is injected at render time, so Stage 5's dynamic
list needs no new prompt version.

**Relative dates.** `today` is computed once per processing round as
`datetime.now(tz=settings.tz).date()`; `TIMEZONE=Europe/Kyiv`. It is **passed into `core` as
a parameter** — `core` never calls `datetime.now`, which is what makes date tests
deterministic. The prompt receives `$today` and `$weekday`. `occurred_at` is
`["string","null"]`; **`null` → `today`**. A future date is impossible by construction and is
clamped to `today` with a WARNING; past dates are accepted as given.

**Zero expenses (spec §7).** Not a technical failure: `extractions.status='ok'` with the
empty array (evals count it), `messages.status='done'`, **no retry**, and one reply:
`Не зрозумів, що саме витрачено. Напиши, будь ласка, що і скільки — наприклад: «хліб 50, таксі 200».`

**Eval candidates and the criterion.** Compare four, including one deliberately expensive
control so that "cheap is enough" is measured rather than assumed:
`qwen/qwen3.7-flash`, `openai/gpt-5.6-luna`, `google/gemini-3.5-flash-lite`,
`google/gemini-3.6-flash` (control).

The criterion, stated so it cannot be fudged after seeing the table:

1. **Hard gate:** `schema_ok` (valid on attempt 1) ≥ 10/11. A model that routinely needs the
   repair loop doubles both cost and latency.
2. Among models passing the gate, require `amount_exact == 11/11` and `count_exact == 11/11`.
3. **Choose the cheapest model whose `amount_exact` and `count_exact` are within one case of
   the best scorer**, tie-broken by mean `usage.cost`, then p95 latency.
4. **Do not choose a model that wins only on `category_exact`.** A wrong category is one tap
   to fix; a wrong amount corrupts a year of reports (`docs/vision.md`).

The chosen id goes to `.env` as `MODEL_TEXT`; the runner-up to `MODEL_FALLBACKS`. Both the
table and the choice go into the Step 4 journal entry.

---

## Steps

Four steps, each ending in a commit on `stage-1-text-to-expense`. TDD: failing test first,
minimal implementation, green, commit. **Gate for every step, output read:**

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

Run `ruff format .` before committing; the gate is `--check`.

---

### Step 1 — Schema, categories, money rules

**Deliverable:** the database is Stage-1-shaped; `Decimal` money round-trips through
Postgres; the thirteen categories are seeded and guarded; rule 2 is executable. Bot behaviour
unchanged.

**Create**

```
migrations/versions/0002_stage1_expenses.py
src/finbot/core/money.py
src/finbot/core/categories/__init__.py
src/finbot/core/categories/catalog.py
src/finbot/repo/categories.py
tests/unit/test_money.py
tests/unit/test_no_float_money.py
tests/integration/test_categories_seed.py
tests/integration/test_expenses_repo.py
```

**Modify**

```
pyproject.toml                                    (aiohttp declared)
src/finbot/core/models.py                         (MessageStatus, ExtractionStatus)
src/finbot/repo/models.py                         (4 new tables + 4 columns on messages)
src/finbot/repo/engine.py                         (json_serializer)
src/finbot/repo/messages.py                       (claim/complete/fail/reset)
tests/conftest.py                                 (truncate list)
tests/integration/test_schema_matches_models.py   (exclusion names)
```

**1.1 `src/finbot/core/money.py`** — the whole of rule 2, in one place.

```python
UAH = "UAH"
MAX_AMOUNT = Decimal("1000000")

def loads_decimal(text: str) -> Any:
    """json.loads that never produces a float. The only JSON entry point in finbot."""
    return json.loads(text, parse_float=Decimal)

def to_amount(value: Decimal) -> Decimal:
    """Quantize to 2dp, ROUND_HALF_UP. Raises ValueError outside (0, MAX_AMOUNT)."""
```

Quantize rather than reject on >2 decimals: a model returning `33.333` for a split is an
artefact, not a problem worth a repair call — rejecting would spend real money on it.

`tests/unit/test_money.py`: `loads_decimal('{"a": 1234567.89}')["a"] == Decimal("1234567.89")`
**and** is not a float — plain `json.loads` yields `1234567.8899999999`, which is the exact
failure rule 2 exists to prevent; `to_amount(Decimal("33.335")) == Decimal("33.34")`; `0`,
`-1`, `10**7` raise.

**1.2 `tests/unit/test_no_float_money.py`** — the Stage-1 lint rule, built like
`test_layering.py` (stdlib AST walk, no Docker, table-driven with control cases).

Walk every `*.py` under `src/finbot/`. For each `ast.Call` resolving to `json.loads` or
`loads`, assert a `parse_float` keyword is present. Allow-list exactly one file:
`src/finbot/core/money.py`. Cases: `json.loads(x)` → flagged;
`json.loads(x, parse_float=Decimal)` → not; `loads_decimal(x)` → not; `json.dumps(x)` → not.
Fail with file and line. Justification for a test rather than prose: ruff cannot express it,
and rule 2 has exactly one place it can be broken silently — the wire.

**1.3 `src/finbot/core/models.py`** — add, importing nothing outward:

```python
class MessageStatus(StrEnum):
    PENDING = "pending"; PROCESSING = "processing"
    DONE = "done"; FAILED = "failed"; SKIPPED = "skipped"

class ExtractionStatus(StrEnum):          # exactly spec §5's three values
    OK = "ok"; INVALID_JSON = "invalid_json"; FAILED = "failed"
```

**1.4 `src/finbot/core/categories/catalog.py`** — a frozen tuple, the single source:

```python
@dataclass(frozen=True)
class CategorySpec:
    slug: str; emoji: str; description: str

CATALOG: Final[tuple[CategorySpec, ...]] = (...)   # the thirteen above
SLUGS: Final[frozenset[str]] = frozenset(c.slug for c in CATALOG)
FALLBACK_SLUG: Final[str] = "other"
```

Assert at import time that `FALLBACK_SLUG in SLUGS` and slugs are unique.

**1.5 `src/finbot/repo/models.py`** — four new tables and four new columns.

`messages` gains:

| column | type | note |
|---|---|---|
| `status` | `Enum(MessageStatus, name="message_status", native_enum=False, create_constraint=True, length=10, values_callable=…)` | default `pending`, not null. **`values_callable` again** — without it SQLAlchemy stores the member *name* |
| `attempts` | `Integer` not null default `0` | processing rounds, not extraction attempts |
| `next_attempt_at` | `DateTime(timezone=True)` not null `server_default=func.now()` | backoff schedule |
| `last_error` | `Text` nullable | |

Index `ix_messages_status_next_attempt_at` on `(status, next_attempt_at)`. **Plain, not
partial, deliberately:** Alembic compares `postgresql_where` as text and produces spurious
diffs, and ADR-0012 names every addition to the exception list a liability. At two users'
volume the index shape is irrelevant; not weakening the guard is not.

`categories`: `id` BigInteger PK; `name` `String(64)` unique not null (the slug); `emoji`
`String(8)` not null; `is_system` `Boolean` not null; `status` `String(16)` not null default
`'active'`; `merged_into_id` BigInteger FK `categories.id` nullable; `created_by` BigInteger
FK `users.id` nullable; `created_at` timestamptz. `status` is a plain `String`, not an
`Enum` — Stage 5 owns the lifecycle, and a check constraint now would only grow the
drift-guard exception list for a column nothing enforces yet.

`extractions`: `id`; `message_id` FK not null; `model_id` `String(128)` not null;
`prompt_version` `String(64)` not null; `attempt` `SmallInteger` not null; `status`
`Enum(ExtractionStatus, name="extraction_status", native_enum=False, create_constraint=True, length=12, values_callable=…)`;
`raw_response` `JSONB` **not null**; `cost_usd` `Numeric(12, 8)` **nullable**; `latency_ms`
`Integer` not null; `created_at` timestamptz. Index on `message_id`.

- `cost_usd` is nullable because the API types `cost` as nullable and a failed transport call
  has no cost at all. **Populated from `usage.cost`, never estimated.**
- `raw_response` is not null: when there is no response, store the error object we built.
  Rule 6 says the raw response is recorded; "we had none" is itself the record.
- **No `UNIQUE(message_id, attempt)`** — a retried round produces `attempt = 1` again. Rounds
  are distinguished by `created_at` and by `messages.attempts`.

`expenses`: `id`; `message_id` FK not null; `user_id` FK not null; `category_id` FK not null;
`item` `Text` not null; `amount` `Numeric(12, 2)` not null; `currency` `CHAR(3)` not null
default `'UAH'`; `amount_uah` `Numeric(12, 2)` not null; `fx_rate` `Numeric(14, 6)` not null
default `1`; `fx_rate_date` `Date` nullable; `occurred_at` `Date` not null; `created_at`
timestamptz; `deleted_at` timestamptz nullable; `bot_message_id` BigInteger nullable. Indexes
on `occurred_at` and `message_id`.

`corrections`: `id`; `expense_id` FK not null; `before` `JSONB` not null; `after` `JSONB` not
null; `corrected_by` BigInteger FK `users.id` not null; `created_at` timestamptz.

**1.6 `src/finbot/repo/engine.py`** — add a JSON serializer:

```python
create_async_engine(
    database_url,
    pool_pre_ping=True,
    json_serializer=lambda value: json.dumps(value, default=str, ensure_ascii=False),
)
```

The response body is parsed once with `parse_float=Decimal`, and the same object goes into
`raw_response`. `default=str` renders those Decimals as JSON strings — lossless as text and
still queryable in `jsonb`. **One parse, one truth.** Parsing twice, once for money and once
for provenance, creates two copies and an unanswerable question about which is authoritative.

**1.7 `src/finbot/repo/messages.py`** — add, none of which commit:

- `claim_next(session, now) -> Message | None` —
  `UPDATE messages SET status='processing', attempts=attempts+1 WHERE id = (SELECT id FROM messages WHERE status='pending' AND next_attempt_at <= :now ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *`.
  One statement, so the claim is atomic and the transaction is short — **the LLM call never
  happens inside an open transaction.** `SKIP LOCKED` costs one clause and makes a second
  worker safe if one ever appears.
- `mark_done`, `mark_skipped`
- `schedule_retry(session, message_id, *, error, delay_seconds)` — sets `pending` and
  `next_attempt_at = now() + delay`, or `failed` once `attempts >= max_attempts`.
- `reset_processing(session) -> int` — `UPDATE messages SET status='pending' WHERE status='processing'`.
  Called once at startup. Correct because deployment is single node, single replica
  (ADR-0002); state that in the docstring so a future multi-replica deploy trips over it.
- `add_if_new` gains `initial_status: MessageStatus`. Rule: `kind == TEXT` and `raw_text` does
  not start with `/` → `PENDING`; everything else → `SKIPPED`. This is where the Stage-0
  plan's *"filtering commands out of extraction is Stage 1's job"* lands.

**1.8 `src/finbot/repo/categories.py`** — `all_active(session)`, `by_slug(session)` (slug →
id). Cached per pipeline run, not globally.

**1.9 `migrations/versions/0002_stage1_expenses.py`** — `revision = "0002"`,
`down_revision = "0001"`. Hand-written; **must not import `finbot`**. Creates `categories`,
alters `messages`, creates `extractions`, `expenses`, `corrections`, the indexes, and seeds
the thirteen categories with `op.bulk_insert` (`is_system=True`, `status='active'`,
`created_by=None`). Enum columns spelled literally, matching 0001's style. `downgrade()`
drops in reverse and removes the four `messages` columns.

**1.10 `tests/integration/test_schema_matches_models.py`** — extend the exclusion set to
`{"message_kind", "message_status", "extraction_status"}`. **Justification, which ADR-0012
requires for each addition:** these are the identical spurious diff — Alembic excludes
type-bound CHECK constraints on the metadata side but cannot on the reflected side, so every
`Enum(native_enum=False, create_constraint=True)` column reports a permanent
`remove_constraint`. Excluded **by name**, never by broadening to "all check constraints".

**1.11 `tests/conftest.py`** — truncate
`expenses, corrections, extractions, messages, users RESTART IDENTITY CASCADE`.
**`categories` is deliberately absent:** it is seeded by the migration, and truncating it
would break every FK and every later test. Add a comment saying so.

**1.12 `tests/integration/test_categories_seed.py`** — the seed drift guard: read
`categories` ordered by `name`, assert it equals `CATALOG` on slug, emoji, `is_system`,
`status`. A migration edited without the catalog (or the reverse) fails the gate.

**1.13 `tests/integration/test_expenses_repo.py`** — rule 2, executable:

- insert `Decimal("1234567.89")` into `amount`, read back, assert `isinstance(row.amount,
  Decimal)` and equality. The Stage-1 analogue of Stage 0's enum round-trip test.
- `currency` round-trips as exactly `"UAH"` — `CHAR(3)` pads, and a padded comparison is a
  classic silent failure.
- `status` round-trips as `"pending"`, not `"PENDING"`.

**Commit:** `feat: stage 1 schema, thirteen seeded categories, and executable numeric-money rules`

---

### Step 2 — OpenRouter client and text extraction, provable offline

**Deliverable:** *"хліб 50 і таксі 200"* becomes two `expenses` rows and two `extractions`
rows against a real Postgres and a fake LLM client, with no Telegram and no socket. The
repair loop, cost accounting and the strict schema are all proven.

**Create**

```
src/finbot/core/extraction/__init__.py
src/finbot/core/extraction/ports.py
src/finbot/core/extraction/schema.py
src/finbot/core/extraction/text.py
src/finbot/core/extraction/pipeline.py
src/finbot/llm/__init__.py
src/finbot/llm/openrouter.py
src/finbot/prompts/__init__.py
src/finbot/prompts/extract_text.v1.md
src/finbot/repo/extractions.py
src/finbot/repo/expenses.py
tests/fixtures/openrouter/{ok_two_items,ok_empty,ok_fenced,invalid_json,no_cost}.json
tests/fixtures/openrouter/README.md
tests/support/fake_llm.py
tests/unit/test_extraction_schema.py
tests/unit/test_extraction_text.py
tests/unit/test_prompt_render.py
tests/unit/test_openrouter_payload.py
tests/unit/test_llm_protocol.py
tests/integration/test_extraction_pipeline.py
```

**Modify:** `src/finbot/config.py`, `tests/unit/test_settings.py`, `.env.example`.

**2.1 `config.py`** — add, keeping Stage 0's idioms:

```python
openrouter_api_key: SecretStr
openrouter_base_url: str = "https://openrouter.ai/api/v1"
model_text: str
model_fallbacks: str = ""          # str + property: pydantic-settings JSON-decodes
llm_timeout_seconds: int = 60      # complex-typed fields straight from env, so
max_extraction_attempts: int = 2   # list[str] would fail on "a,b" before any validator
max_message_attempts: int = 5
```

Properties `model_candidates -> tuple[str, ...]` (`[model_text, *fallbacks]`, blanks
stripped) and `tz -> ZoneInfo`. Validators, all failing at startup rather than at 2 a.m.:
`ZoneInfo(self.timezone)` must resolve; **no candidate id may end in `:free`**;
`max_extraction_attempts` in 1..3. `MODEL_VOICE`/`MODEL_VISION` stay undeclared —
`extra="ignore"` covers them, and Stage 0's precedent is to declare only what is used.

`tests/unit/test_settings.py` gains: `"a, b ,"` → `("model_text", "a", "b")`; a `:free` id
raises; `TIMEZONE=Nowhere/Nope` raises. Existing tests need the two new kwargs.

**2.2 `prompts/__init__.py`**

```python
_DIR = Path(__file__).parent
PROMPT_VERSION_TEXT: Final[str] = "extract_text.v1"

def load(version: str) -> str:
    return (_DIR / f"{version}.md").read_text(encoding="utf-8")

def render_text_prompt(*, today: date, catalog: Sequence[CategorySpec]) -> str:
    return Template(load(PROMPT_VERSION_TEXT)).substitute(
        today=today.isoformat(), weekday=today.strftime("%A"),
        categories="\n".join(f"- {c.slug} {c.emoji} — {c.description}" for c in catalog),
    )
```

`Template.substitute` (not `safe_substitute`) so a missing placeholder raises loudly.
`str.format` is wrong here — the prompt contains JSON braces.

**2.3 `prompts/extract_text.v1.md`** — ship exactly this:

```markdown
You extract household expenses from a short message written by one of two people in a
family Telegram group. They write in Ukrainian, Russian, or a mix of both, usually in
shorthand: "хліб 50, таксі 200".

Today is $today ($weekday), timezone Europe/Kyiv.

Return one JSON document matching the provided schema. Nothing else.

## Rules

1. One entry per distinct thing bought. "хліб 50 і таксі 200" is two entries.
   "дві кави по 65" is one entry: amount 130, item "дві кави".
2. `amount` is the number of hryvnia spent, as a JSON number. Strip currency words and
   symbols. "1 250,50" is 1250.50. Never invent an amount that is not in the text.
3. If the message names no amount at all, or is not about spending money, return an empty
   `expenses` array. Do not invent an entry.
4. `item` is the shortest noun phrase naming what was bought, in the language the user
   wrote it. Do not translate. Do not add words.
5. `category` must be exactly one of the slugs listed below. Never invent a slug. When
   nothing fits, use `other`.
6. `occurred_at` is the date the money was spent, as YYYY-MM-DD. Resolve relative dates
   ("вчора", "минулої пʼятниці") against today's date above. If the message says nothing
   about when, return null.

## Categories

$categories
```

**2.4 `core/extraction/schema.py`** — the Pydantic DTOs and the strict schema derived from
them.

```python
class ExpenseDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: str
    amount: Decimal
    category: str
    occurred_at: date | None = None

class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    expenses: list[ExpenseDraft]
```

Validators on `ExpenseDraft`: `item` stripped, non-empty, truncated to 200 characters rather
than rejected (a long name must not cost a repair call); `amount` through `money.to_amount`;
`category` must be in `SLUGS`, otherwise coerced to `FALLBACK_SLUG` with a WARNING — the
`enum` in the schema already makes this near-impossible, and a repair call for a category is
worse value than filing it under `other`.

```python
def text_json_schema(slugs: Sequence[str]) -> dict[str, Any]:
    """Hand-built, strict-mode-ready. Pydantic's model_json_schema() is NOT usable
    as-is: it emits $defs/$ref, omits additionalProperties:false, and renders Decimal
    as anyOf[number,string]."""
```

Every object carries `"additionalProperties": false` and a `required` listing **all** its
properties; `amount` is `{"type": "number"}`; `category` is
`{"type": "string", "enum": [*slugs]}`; `occurred_at` is `{"type": ["string", "null"]}`.

`tests/unit/test_extraction_schema.py` — the derivation is tested, not trusted:

- recursively walk the emitted schema; **every** object node has `additionalProperties is
  False` and `sorted(required) == sorted(properties)`;
- no `$ref` or `$defs` anywhere;
- `category.enum` equals the thirteen catalog slugs, in catalog order;
- a valid instance parses into `ExtractionResult`; an instance with an extra key is rejected
  by `extra="forbid"`, so the model and the wire schema agree in both directions.

**2.5 `core/extraction/ports.py`**

```python
@dataclass(frozen=True)
class LlmRequest:
    models: tuple[str, ...]
    messages: tuple[Mapping[str, str], ...]
    json_schema: Mapping[str, Any]
    schema_name: str

@dataclass(frozen=True)
class LlmResponse:
    model_id: str                 # from the response body, never from config
    content: str
    cost_usd: Decimal | None
    latency_ms: int
    raw: Mapping[str, Any]

class LlmError(Exception):
    """Transport / provider failure. Carries `raw` for the extractions row."""

class LlmClient(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...
```

**2.6 `core/extraction/text.py`** — pure, no I/O, no clock:

- `build_request(*, raw_text, today, catalog, models) -> LlmRequest`
- `parse_content(content: str) -> ExtractionResult` — strips a leading/trailing ```json fence
  if present (some providers add one even under `response_format`), then `money.loads_decimal`,
  then `ExtractionResult.model_validate`. Raises `ExtractionInvalid(str)` whose message is
  short enough to paste into the repair prompt.
- `build_repair_request(previous, bad_content, error)` — appends
  `{"role": "assistant", "content": bad_content}` and
  `{"role": "user", "content": f"The previous reply did not match the schema: {error}\nReturn only a JSON document matching the schema. No prose, no code fences."}`.
- `resolve_dates(result, today)` — `None` → `today`; future → `today` with a WARNING; past
  untouched.

**2.7 `llm/openrouter.py`**

```python
class OpenRouterClient:
    def __init__(self, *, session: aiohttp.ClientSession, api_key: SecretStr,
                 base_url: str, timeout_seconds: int) -> None: ...
    async def complete(self, request: LlmRequest) -> LlmResponse: ...
```

Body:

```json
{
  "models": ["<primary>", "<fallback>"],
  "messages": [...],
  "response_format": {"type": "json_schema",
                      "json_schema": {"name": "<schema_name>", "strict": true, "schema": {...}}},
  "provider": {"data_collection": "deny", "require_parameters": true},
  "temperature": 0
}
```

Non-negotiable details, each verified:

- **No `usage: {include: true}`.** Deprecated and inert; usage is always returned.
- `provider.require_parameters: true` is **mandatory** — structured-output support is per
  *endpoint*, so without it a request can be routed to a provider that ignores
  `response_format` and returns prose.
- `models` (plural) carries the fallback list; there is no separate retry wrapper.
- `model_id = body["model"]` — **the model that actually served**. Recording config here
  would silently mislabel every fallback row in the evaluation dataset.
- `cost_usd = body["usage"].get("cost")`, already a `Decimal` because the whole body is
  parsed with `money.loads_decimal`. `None` stays `None`.
- `latency_ms` via `time.perf_counter()` around the request only.
- Non-2xx, timeout, connection error → `LlmError` carrying
  `{"error": …, "type": …, "status": …}` as `raw`.
- Headers: `Authorization: Bearer …`, `Content-Type: application/json`;
  `HTTP-Referer`/`X-Title` omitted (a private bot, not a leaderboard entry).

`tests/unit/test_openrouter_payload.py` builds the body through a `_build_body()` helper and
asserts, byte-exactly: `provider.data_collection == "deny"`, `provider.require_parameters is
True`, `json_schema.strict is True`, the **absence** of any `usage` key, and `models` being
the full candidate tuple. No network.

`tests/unit/test_llm_protocol.py` — three lines, checked by mypy strict, which is a gate:

```python
def _conforms(client: OpenRouterClient) -> LlmClient:
    return client
```

**2.8 `core/extraction/pipeline.py`**

```python
async def extract_and_store(*, session, message, llm: LlmClient, catalog, category_ids,
                            today, models, max_attempts) -> ExtractionOutcome
```

Loop, at most `max_attempts` (spec §4.3 says two):

1. `response = await llm.complete(request)` — **outside any open transaction**.
2. `LlmError` → `extractions` row `status='failed'`, `raw_response` = the error object,
   `cost_usd=None`, stop the loop (a provider outage is not repairable by rephrasing).
3. `parse_content` raises → row `status='invalid_json'` with the full body; build the repair
   request; next attempt.
4. Success → row `status='ok'`; resolve dates; write `expenses` in model order
   (`currency='UAH'`, `amount_uah=amount`, `fx_rate=1`, `fx_rate_date=occurred_at` — Stage
   1.5 changes values, not columns); `messages.status='done'`; commit **once**.
5. Empty `expenses` → still `status='ok'`, still `done`, zero expense rows, outcome flag
   `asked_for_clarification=True`.
6. Attempts exhausted → `schedule_retry` with exponential backoff (`30 * 2**(attempts-1)`
   seconds, capped at 30 minutes) or `failed` at `max_message_attempts`.

Returns `ExtractionOutcome(expense_ids, drafts, status, asked_for_clarification)`. It sends
nothing to Telegram — that is the adapter's job, which keeps this reusable by `evals/` and by
the Stage 6 HTTP adapter.

**2.9 `tests/support/fake_llm.py`** — the `FakeSession` pattern, applied to the model:

```python
class FakeLlmClient:
    """Returns recorded HTTP response bodies. Raises AssertionError on an
    unscripted call — 'the code called a model behind my back' must be a loud
    failure, not a bill."""
    def __init__(self, *responses: str | LlmError) -> None: ...
    async def complete(self, request: LlmRequest) -> LlmResponse: ...
    # records every LlmRequest it received, for assertions on the repair prompt
```

It parses recorded bodies through the **same** code path `OpenRouterClient` uses
(`money.loads_decimal`, `body["model"]`, `body["usage"]["cost"]`), so a change to cost or
model-id extraction cannot pass in tests while failing in production.

`tests/conftest.py` gains one session-scoped autouse fixture setting
`OPENROUTER_BASE_URL=http://127.0.0.1:9` (the discard port). If any code ever constructs a
real client during tests, it fails in milliseconds instead of spending money. A blanket
socket ban is **not** used — testcontainers talks to the Docker socket over HTTP.

**2.10 `tests/fixtures/openrouter/*.json`** — whole response bodies, not domain objects.
`ok_two_items` (`usage.cost` present, `model` **differing from the requested primary**, to
pin that `model_id` comes from the response), `ok_empty`, `ok_fenced` (content wrapped in
```json), `invalid_json` (content is prose), `no_cost` (`usage.cost: null`). `README.md`
states they are recorded bodies, that the initial versions are hand-written from the
documented schema, and how Step 4's `--save-raw` refreshes them from synthetic golden cases
only (ADR-0009).

**2.11 `tests/integration/test_extraction_pipeline.py`** — real Postgres, fake client, no
Telegram:

- two items → 2 `expenses` in model order, 1 `extractions` row `status='ok'`,
  `cost_usd == Decimal("0.000123")` exactly, `model_id` equal to the fixture's `model` and
  **not** to the requested primary, `messages.status='done'`;
- `invalid_json` then `ok_two_items` → **2** rows, `attempt` 1 and 2, statuses
  `invalid_json` then `ok`, and the second recorded `LlmRequest` contains the assistant turn
  plus the validation error;
- two consecutive `invalid_json` → 2 rows, no expenses, `messages.status='pending'` with
  `attempts == 1` and `next_attempt_at` in the future;
- `LlmError` → 1 row `status='failed'`, `cost_usd is None`, `raw_response` non-null;
- `no_cost` → row written, `cost_usd is None`, expenses still written;
- `ok_empty` → 0 expenses, `status='ok'`, `messages.status='done'`,
  `asked_for_clarification is True`;
- `occurred_at` null → today; future → today.

**Commit:** `feat: openrouter client, versioned prompt and text extraction with a repair loop`

---

### Step 3 — Telegram: delivery guarantee, buttons that work, confirmations, reports

**Deliverable:** the bot works end to end. A tap on ✏️ or 🗑 reaches a handler and changes
the database. An update is never acknowledged before it is stored. Several expenses in one
message produce one numbered confirmation.

**Create**

```
src/finbot/adapters/telegram/{callbacks,keyboards,render,polling,runner,errors}.py
src/finbot/core/reporting/{__init__,periods}.py
src/finbot/repo/{reports,corrections}.py
tests/unit/{test_sender_of,test_periods,test_polling_offset,test_render}.py
tests/integration/{test_callback_flow,test_confirmation_flow,test_reports,test_persistence_error_withholds_offset}.py
```

**Modify:** `mapping.py`, `middlewares.py`, `handlers.py`, `main.py`,
`tests/support/fake_session.py`, `tests/support/updates.py`, `tests/unit/test_main.py`,
`tests/integration/test_telegram_flow.py`, `infra/Dockerfile`, `infra/docker-compose.yml`,
`.env.example`.

**3.1 `mapping.py`** — add the resolver both middlewares need:

```python
def sender_of(update: Update) -> User | None:
    """Who this update is from, for the allowlist.

    For a callback query this is `callback_query.from_user` — the person who
    TAPPED. `callback_query.message.from_user` is the BOT, and checking it
    would reject both household members. Reads the Update directly rather than
    data["event_from_user"], keeping Stage 0's rule that nothing depends on
    aiogram's internal middleware registration order.
    """
    if update.message is not None:
        return update.message.from_user
    if update.callback_query is not None:
        return update.callback_query.from_user
    return None
```

`tests/unit/test_sender_of.py` is table-driven over every shape, in the spirit of
`test_layering.py`: text message → the sender; callback query → the tapper, **explicitly
asserting it is not the bot user embedded in `callback_query.message.from_user`**; callback
query whose `message` is an `InaccessibleMessage` → still the tapper; an update carrying
neither → `None`; a message with no `from_user` → `None`.

**3.2 `middlewares.py`** — three changes, each closing the reviewer's finding:

- `AllowlistMiddleware` uses `sender_of(update)`; `None` or not in the allowlist → `return
  None`, silently (spec §7). On success it sets `data["sender"] = user`.
- `PersistMessageMiddleware`: **`if update.message is None: return await handler(event, data)`**
  — pass through, do not drop. This one line is what makes every ✏️/🗑 tap reach a handler.
  The docstring must say so and name the Stage-0 bug it fixes.
- The same middleware wraps its DB work in
  `try: ... except Exception as exc: raise PersistenceError(...) from exc`.
  `PersistenceError` (`errors.py`) is **the only exception that withholds the polling
  offset**, and it can only originate here.

`DbSessionMiddleware` is unchanged; its ADR-0011 log line becomes belt-and-braces rather than
the last resort.

**Callbacks are not written to `messages`.** `messages` is "what arrived to be turned into
expenses" (ADR-0006), and the production eval set is built from `messages` + `expenses` +
`corrections` (ADR-0009) — a tap there would be noise in the dataset, and there is no `kind`
value for it. The record of a tap is the `corrections` row. Redelivery of a callback is
harmless because both actions are idempotent: deleting an already-deleted expense and setting
a category to its current value are both no-ops that still answer the query.

**3.3 `callbacks.py`** — `CallbackData` factories, well inside the 64-byte limit:

```python
class ExpenseAction(CallbackData, prefix="exp"):   # "exp:edit:1234"
    action: Literal["edit", "del"]
    expense_id: int

class SetCategory(CallbackData, prefix="cat"):     # "cat:1234:7"
    expense_id: int
    category_id: int
```

**3.4 `render.py`** — Telegram-facing text, `parse_mode=None` throughout.

`CATEGORY_LABELS: dict[str, str]` maps slug → Ukrainian label. A unit test asserts
`set(CATEGORY_LABELS) == SLUGS`, so a fourteenth category cannot ship label-less.

Confirmation, one message per incoming message (ADR-0007):

```
✅ Записав 2:
1. 🛒 хліб — 50.00 ₴
2. 🚕 таксі — 200.00 ₴
Разом: 250.00 ₴
```

Single expense: `✅ 🛒 хліб — 50.00 ₴`, no total line. An `occurred_at` other than today gets
a ` (09.08)` suffix on that line. **No `parse_mode`** — item text originates from a model
reading user input, and with no parse mode there is nothing to escape and no
formatting-injection surface. Say so in the docstring.

**3.5 `keyboards.py`** — `confirmation_keyboard(expenses)` builds one row per expense,
`✏️ 1` / `🗑 1`, capped at 12 rows (beyond that the list is shown without buttons; only Stage
4 receipts can reach it). `category_keyboard(expense_id, categories)` builds the picker, 3
per row, plus `← Назад`.

**3.6 `handlers.py`** — `build_router()` registers:

| filter | behaviour |
|---|---|
| `Command("ping")` | `pong` (unchanged) |
| `Command("day"/"week"/"month")` | period → `repo.reports` → `render` → reply |
| `F.voice \| F.photo` | `Поки що я розумію лише текст. Голос і фото — скоро.` — never silence |
| `ExpenseAction.filter(F.action == "del")` | soft-delete + `corrections` row + re-render + `answer("Видалив")` |
| `ExpenseAction.filter(F.action == "edit")` | `edit_reply_markup` to the category picker + `answer()` |
| `SetCategory.filter()` | update `category_id` + `corrections` row + re-render + `answer("Готово")` |

Plain expense text has **no handler** — the inbox middleware owns it and the drain replies.
aiogram logging it as "not handled" is correct and expected.

Rules for every callback handler:

- **Always** send `AnswerCallbackQuery`, including on the failure path, or the client spins
  for 30 seconds. Wrap the body in `try/except Exception` that logs and answers
  `Не вдалося, спробуй ще` — this is the "handler failure must not block the queue" lane, and
  a repeat tap costs the user nothing.
- Group siblings by **`expenses.message_id`**, not by `bot_message_id` — one rule, no
  fallback branch, correct even if the process died before `bot_message_id` was written.
- Edit via `bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, ...)`.
  **Never narrow `cq.message` to `Message`** — it may be an `InaccessibleMessage`, and both
  carry `chat` and `message_id`.
- Deleted expenses render as `~ 🚕 таксі — 200.00 ₴ (видалено)` and lose their buttons.
- `corrections.before`/`after` are `jsonb` snapshots of the changed fields only.

**3.7 `core/reporting/periods.py`** — pure, no SQL, no clock:

```python
def resolve(period: Literal["day", "week", "month"], today: date) -> tuple[date, date]
```

`day` → `(today, today)`; `week` → Monday of the current ISO week → today; `month` → the 1st
→ today. Tests cover a Monday, a Sunday, and the 1st of a month.

**3.8 `repo/reports.py`** — one `SELECT`, no model anywhere near it (rule 5):

```sql
SELECT c.name, SUM(e.amount_uah) AS total, COUNT(*) AS n
  FROM expenses e JOIN categories c ON c.id = e.category_id
 WHERE e.deleted_at IS NULL AND e.occurred_at BETWEEN :d_from AND :d_to
 GROUP BY c.name ORDER BY total DESC
```

Returns `Report(period, date_from, date_to, lines, total)` from `core/reporting`. Empty →
`Нічого не записано за цей період.`

**3.9 `polling.py`** — the mechanism that closes ADR-0011:

```python
ALLOWED_UPDATES: Final[list[str]] = ["callback_query", "message"]

async def run_polling(*, bot: Bot, feed: Callable[[Update], Awaitable[None]],
                      stop: asyncio.Event, poll_timeout: int = 25) -> None:
    offset: int | None = None
    backoff = Backoff(BackoffConfig(min_delay=1.0, max_delay=60.0, factor=2.0, jitter=0.1))
    while not stop.is_set():
        try:
            updates = await bot(GetUpdates(offset=offset, timeout=poll_timeout,
                                           allowed_updates=ALLOWED_UPDATES),
                                request_timeout=poll_timeout + 15)
        except Exception:
            logger.exception("getUpdates failed"); await backoff.asleep(); continue
        backoff.reset()
        try:
            for update in updates:
                try:
                    await feed(update)
                except PersistenceError:
                    raise                       # abort the batch; offset unchanged
                except Exception:
                    logger.exception("handler failed for update_id=%s", update.update_id)
        except PersistenceError:
            logger.exception("withholding offset; telegram will redeliver")
            await backoff.asleep(); continue    # offset NOT advanced
        if updates:
            offset = updates[-1].update_id + 1  # acknowledged only now
```

`feed` is a parameter, defaulting in `main()` to `partial(dp.feed_update, bot)`. That is what
makes the guarantee unit-testable without a database, and it documents the contract: the loop
distinguishes exactly two exception classes and nothing else.
`aiogram.utils.backoff.Backoff` is reused rather than reinvented.

**No `dp.errors` handler is registered anywhere.** `ErrorsMiddleware` sits outermost and
re-raises only while no handler exists; registering one would swallow `PersistenceError` and
silently void the whole design.

**3.10 `runner.py`** — the drain:

```python
async def drain_loop(*, bot, sessionmaker, llm, settings, stop, idle_seconds=2.0) -> None
```

Loop: `claim_next` (short transaction, committed); if none, sleep and continue; else
`extract_and_store` (no transaction held across the network call); then render and send the
confirmation (or the clarification question, or the failure notice); then one
`UPDATE expenses SET bot_message_id = :id WHERE id IN (...)` and commit.

Order is ADR-0007's, literally: **write, then reply.** A crash after the write loses nothing;
a crash before `bot_message_id` is set loses only provenance, because the buttons carry
`expense_id` and siblings are grouped by `message_id`.

**3.11 `main.py`**

```python
async def main() -> None:
    settings = Settings()
    sessionmaker = create_sessionmaker(settings.database_url)
    async with sessionmaker() as session:
        n = await messages.reset_processing(session); await session.commit()
    async with aiohttp.ClientSession() as http:
        llm = OpenRouterClient(session=http, api_key=settings.openrouter_api_key, ...)
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        dp = build_dispatcher(sessionmaker, settings.allowed_user_ids)
        stop = asyncio.Event()
        # SIGTERM/SIGINT -> stop.set(); docker stop then drains the current message
        async with asyncio.TaskGroup() as tg:
            tg.create_task(run_polling(bot=bot, feed=partial(dp.feed_update, bot), stop=stop))
            tg.create_task(drain_loop(bot=bot, sessionmaker=sessionmaker, llm=llm,
                                      settings=settings, stop=stop))
```

`start_polling` is gone. `build_dispatcher` is unchanged in shape, so tests and `main()` still
share one factory.

**3.12 `tests/support/fake_session.py`** — grow branches, exactly as ADR-0012 anticipated:

- `SendMessage` → a canned `Message` with an **incrementing** `message_id`, so
  `bot_message_id` linkage is assertable;
- `AnswerCallbackQuery` → `True`;
- `EditMessageText` / `EditMessageReplyMarkup` → a canned `Message`;
- `GetUpdates` → pops a scripted queue and **records the `offset` it was called with**;
- anything else still raises `AssertionError`.

`tests/support/updates.py` gains `callback_update(update_id, data, *, user_id, bot_message_id)`
producing a real `callback_query` payload, with the bot as `message.from.id` — so the "which
user id" trap is present in the fixture, not just in the prose.

**3.13 The three tests that make this stage's claims mechanical**

`tests/unit/test_main.py`:

```python
def test_allowed_updates_matches_registered_handlers() -> None:
    """ALLOWED_UPDATES is what we ask Telegram for; resolve_used_update_types() is
    what the router can actually handle. Stage 0 shipped ["message"] while Stage 1
    needs callback_query — every ✏️ tap would have been dropped by Telegram before
    reaching us, with no log and no reply. This equality is what makes that class
    of bug unmergeable. If they ever diverge legitimately, change this test
    deliberately and say why in the journal."""
    dp = build_dispatcher(create_sessionmaker("postgresql+asyncpg://u:p@localhost/db"),
                          frozenset({1}))
    assert sorted(ALLOWED_UPDATES) == dp.resolve_used_update_types()

def test_no_global_error_handler_is_registered() -> None:
    """A dp.errors handler would make aiogram's outermost ErrorsMiddleware swallow
    PersistenceError instead of re-raising it, silently voiding ADR-0013's
    delivery guarantee. Handle failures inside handlers instead."""
    assert build_dispatcher(...).errors.handlers == []
```

`tests/unit/test_polling_offset.py` — no Docker, no DB, no dispatcher:

- three scripted updates, `feed` always succeeds → the second `GetUpdates` carries
  `offset == last.update_id + 1`;
- `feed` raises `PersistenceError` on the second → the next `GetUpdates` carries the
  **unchanged** offset, and the third update is never fed;
- `feed` raises `ValueError` on the second → the third **is** fed and the offset **does**
  advance past the whole batch, because a handler bug must not wedge the household's bot.

`tests/integration/test_persistence_error_withholds_offset.py` — proves the other half, that
the middleware really raises `PersistenceError`: build a dispatcher over
`create_sessionmaker("postgresql+asyncpg://x:x@127.0.0.1:1/none")` (connection refused, fast,
deterministic, no Docker) and assert `PersistenceError` propagates out of `feed_raw_update`.
Together these two prove the guarantee end to end without a network.

**3.14 The remaining behavioural tests**

`tests/integration/test_callback_flow.py` — the reviewer's finding, made permanent:

- a 🗑 callback through the **real dispatcher**: exactly one `AnswerCallbackQuery`, one
  `EditMessageText`, `expenses.deleted_at` set, one `corrections` row with `corrected_by`
  equal to the **tapper**;
- a ✏️ callback: one `EditMessageReplyMarkup` carrying thirteen category buttons;
- a `SetCategory` callback: `category_id` changed, one `corrections` row;
- **a callback from a stranger: zero API calls and zero DB changes**;
- the same 🗑 callback fed twice: still one `corrections` row, still deleted, and the second
  is still answered (idempotent redelivery).

`tests/integration/test_confirmation_flow.py` — the roadmap bullet, mechanically: one text
update, fixture yielding **three** expenses, one drain tick → **exactly one** `SendMessage`
whose text contains `1.`, `2.`, `3.` and `Разом`, whose `reply_markup` has three rows of two
buttons, and three `expenses` rows sharing one `bot_message_id`. Plus the `ok_empty` path:
zero expenses, one `SendMessage` containing `Не зрозумів`, no keyboard.

`tests/integration/test_reports.py` — seed expenses across two categories and three dates;
`/day`, `/week`, `/month` return the right totals; a soft-deleted row is excluded; an empty
period returns the empty-state text.

`tests/integration/test_telegram_flow.py` — extend: `/ping` and `/day` land in `messages` with
`status='skipped'` (persisted per ADR-0006, never sent to the model); plain text lands with
`status='pending'`.

**3.15 `infra/`** — add `tzdata` next to `ffmpeg` in the `apt-get install` line. `zoneinfo`
silently falls back or raises without it, and discovering that in production is exactly the
half-day of guessing the staged start exists to prevent. Add `OPENROUTER_API_KEY`,
`MODEL_TEXT`, `MODEL_FALLBACKS` to `.env.example`.

**Additional verification for Step 3** — two container checks needing no bot token and no API
key:

```bash
docker compose --env-file .env -f infra/docker-compose.yml build bot
docker compose -f infra/docker-compose.yml run --rm --no-deps --entrypoint python bot -c \
  "from zoneinfo import ZoneInfo; print(ZoneInfo('Europe/Kyiv'))"
docker compose -f infra/docker-compose.yml run --rm --no-deps --entrypoint python bot -c \
  "from finbot import prompts; print(len(prompts.load('extract_text.v1')))"
```

The second must print a non-zero length: it proves the `.md` prompt survived `pip install`
into the wheel. A prompt file that exists in git and not in the image is a production-only
failure, and this is the cheap way to find it now.

**Commit:** `feat: inbox delivery, working inline buttons, numbered confirmations and SQL reports`

---

### Step 4 — Evals, the record, and the choice of model

**Deliverable:** `python -m evals.run --models a,b,c` prints a model × accuracy × cost ×
latency table over eleven hand-written cases, and the criterion above turns it into a
decision.

**Create**

```
evals/__init__.py
evals/run.py
evals/scoring.py
evals/golden/text_v1.jsonl
```

**Modify:** `README.md`, `evals/README.md`, `docs/roadmap.md`, `docs/journal.md`.

**4.1 `evals/golden/text_v1.jsonl`** — eleven synthetic cases, deliberately awkward
(ADR-0009: hand-written only, never production data). Dates are expressed as
`occurred_offset_days` relative to the run date, **never as absolute dates** — an absolute
date in a committed fixture is a clock bomb that turns green into red next August.

| id | input | expects |
|---|---|---|
| `single-01` | `хліб 50` | 1 · groceries · 50 |
| `multi-02` | `хліб 50 і таксі 200` | 2 · groceries, transport |
| `multi-03` | `кава 65, аптека 340, метро 16` | 3 · dining_out, health, transport |
| `relative-date-04` | `вчора таксі 200` | 1 · offset −1 |
| `words-05` | `двісті гривень за таксі` | 1 · 200 |
| `russian-06` | `купил молоко 45 и хлеб 30` | 2 · groceries |
| `donation-07` | `донат на дрон 500` | 1 · gifts |
| `no-amount-08` | `купив хліб` | 0 |
| `not-expense-09` | `яка сьогодні погода` | 0 |
| `separators-10` | `заправка 1 250,50` | 1 · 1250.50 · transport |
| `quantity-11` | `дві кави по 65` | 1 · 130 · dining_out |

**4.2 `evals/run.py`** — `--models a,b,c`, `--cases`, `--repeats`, `--save-raw DIR`.

It imports `finbot.core.extraction.text` and `finbot.llm.openrouter` and calls the
**production code path**. An eval with its own prompt or its own parser measures nothing. It
fails fast with a clear message when `OPENROUTER_API_KEY` is absent, rather than hanging on a
401.

**4.3 `evals/scoring.py`** — deterministic metrics only: `schema_ok`, `amount_exact`,
`count_exact`, `category_exact`, `date_exact`, `cost_per_message` (mean `usage.cost`),
`latency_p50`, `latency_p95`. **`item_similar` is deliberately absent** — it needs a judge
model, and that is Stage 3. Never call a judge where `==` exists (spec §8). Output is a
Markdown table, raw counts, no percentages.

**4.4 The split, stated in `evals/README.md` and `README.md` in these words:**

> **`pytest` proves the plumbing.** Given this exact recorded response body, the code parses
> it, keeps money as `Decimal`, writes N rows and sends one confirmation. It never calls a
> model, costs nothing, and is deterministic — which is why it can be a gate on a branch that
> merges itself.
>
> **`evals/` measures the model.** It calls real models, costs real money, and its results
> vary between runs. It is not part of `pytest` and is not a gate before Stage 3.
>
> Extraction *correctness* is not asserted in `pytest`. Asserting it there would either
> require a network call in the gate or freeze one model's output as "the truth" — both
> destroy the gate's meaning.

**4.5 `docs/roadmap.md`** — Stage 1 → ✅ **only if** a `MODEL_TEXT` has actually been chosen
by running the evals; otherwise leave it 🚧 with one line naming exactly what remains, as
Stage 0's plan did. The roadmap's done-criterion is *"a week of recording expenses by text"*,
which no gate can prove — say so plainly rather than marking it done.

**4.6 `docs/journal.md`** — one entry at the **top**, English, five lines maximum, plus a
`## Learning notes` block covering, in three or four sentences: why the ack is withheld for
the persistence step only and not for handlers; why `model_id` is read from the response
rather than from configuration; why `json.loads(parse_float=Decimal)` is the entire distance
between `CLAUDE.md` rule 2 and a float in the ledger; and why `provider.require_parameters:
true` matters when structured-output support is per endpoint rather than per model.

**Verification for Step 4:** the three gates, plus — with a key present, which is an owner
prerequisite and therefore not a gate — one eval run whose table is pasted into the journal
entry alongside the chosen model and the criterion that chose it.

**Commit:** `feat: golden eval set and a runner for choosing the text model`

**Then:** merge `stage-1-text-to-expense` to `main` once all three gates are green and review
is clean.

**Hand-off to `doc-curator`:** **ADR-0013** (supersedes ADR-0011) and **ADR-0014**, plus the
amendment to ADR-0012, must be written before the stage counts as documented.

---

## Status: Done
