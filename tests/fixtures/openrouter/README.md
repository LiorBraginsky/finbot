# OpenRouter fixtures

Whole HTTP response bodies from `POST /api/v1/chat/completions`, never
hand-written `ExpenseDraft` objects — the unit under test is the parser
(`finbot.llm.openrouter.parse_response_body`, and beyond it
`core.extraction.text.parse_content`), so the input has to be what the wire
actually carries.

`tests/support/fake_llm.py`'s `FakeLlmClient` reads these files verbatim and
parses them through the exact same function `OpenRouterClient` calls in
production, so a change to cost or model-id extraction cannot pass in tests
while failing against the real API.

## Files

### The envelope is what you expect

- `ok_two_items.json` — two expenses, `usage.cost` present, and `model`
  deliberately **different** from the requested primary model, to prove
  `model_id` is read from the response and never from config.
- `ok_empty.json` — `expenses: []` (spec §7's "message names no amount").
- `ok_fenced.json` — the assistant content wrapped in a ` ```json ` fence,
  which some providers add even under a strict `response_format`.
- `invalid_json.json` — the content is prose, not JSON at all. The one case
  the repair loop is for: the envelope is fine, the payload is not.
- `no_cost.json` — `usage.cost: null`, which the response schema types as
  nullable; `extractions.cost_usd` must tolerate it.

### HTTP 200 with an envelope that is not what you expect

These seven are the third failure class, beside transport failure and schema
violation (ADR-0014 §6). Every one of them was a live escape at some point in
Stage 1's review: the exception reached the drain instead of becoming an
`LlmError`, so a **billed call left no `extractions` row** — breaking
`CLAUDE.md` rule 6, which exists to make the evaluation dataset complete.

- `error_envelope_200.json` — a provider error object returned under HTTP 200,
  with no `model` key at all.
- `no_choices.json` — `choices: []`. Carries a real cost: the call was billed.
- `null_content.json` — `content: null`, as a refusal or a reasoning-only
  generation produces. Also billed.
- `not_json.json` — the body is not JSON. An empty response, a proxy's HTML
  error page, or a misconfigured `OPENROUTER_BASE_URL`. This is why the parse
  guard starts at `loads_decimal`, not at the first line that indexes.
- `null_model.json` — `"model": null`, which would otherwise reach a
  `String(128) NOT NULL` column and raise at flush, far from the cause.
- `usage_not_a_mapping.json` — `"usage": "n/a"`. Neither the presence of a key
  nor its type can be assumed.
- `cost_not_a_number.json` — `usage.cost` as a string, which would reach a
  `Numeric(12,8)` bind as text.

When adding a fixture here, derive the case from the **envelope's failure
space**, not from the shapes already listed. That habit is the difference
between a suite that pins the parser and one that documents the bugs already
found.

### Voice (docs/roadmap.md Stage 2)

Same envelope shape as the text fixtures above; only `content` differs, since
`core.extraction.voice.parse_content` validates against
`VoiceExtractionResult` (`transcript` plus `expenses`) instead of
`ExtractionResult`. `invalid_json.json` above is reused as-is for voice too —
prose fails to parse as JSON regardless of which schema it would have been
validated against.

- `ok_voice_two_items.json` — a transcript plus two expenses, the voice
  analogue of `ok_two_items.json`.
- `ok_voice_empty.json` — a transcript but `expenses: []` — the model heard
  something, but nothing that named an amount.
- `ok_voice_foreign_currency.json` — a transcript naming a foreign currency
  ("доларів"), for the guard that runs on the transcript *after* extraction
  (docs/roadmap.md Stage 2's decision 4), unlike text's, which runs first.

### Bank-feed screenshots (docs/plans/stage-2_5-bank-screenshots.md)

Same envelope shape again; `content` validates against
`BankExtractionResult` (`is_transaction_feed` plus `rows`). Unlike the text
and voice fixtures above, these are **not** eligible for
`--save-raw` refreshing from a real screenshot: a bank feed's real amounts,
merchants and dates are exactly the private household data ADR-0009 keeps
out of the repository, and every merchant/amount here is invented, never
pulled from a real screenshot. See the plan's Reality check (finding 4) and
ADR-worthy note 2 for the refusal this forces on `evals/run.py`.

- `bank_feed_ok.json` — one `expense` row (Silpo, groceries) plus one
  `savings` row (a jar) and one `own_transfer` row (a same-owner card),
  proving Approach A1: only the expense is ever written, and the other two
  are reported and stored nowhere.
- `bank_multi_day.json` — two `expense` rows under two different verbatim
  headers (`Сьогодні`, `Вчора`), for the "a multi-day result produces drafts
  across two dates in feed order" case.
- `bank_not_a_feed.json` — `is_transaction_feed: false` with one row that
  *looks* like a valid expense — Approach E's guard: false means zero drafts
  regardless of what `rows` contains, closing the hole a photographed
  receipt would otherwise open before Stage 4 exists.

## Provenance and refreshing

These initial versions are **hand-written from the documented response
schema** (verified 2026-08-10 — see the plan's Reality check), not recorded
from a live call: Stage 1 Step 2 has no OpenRouter key yet, and a
prerequisite must never gate a step (`docs/plans/stage-1-text-to-expense.md`
§C).

Step 4 ships `python -m evals.run --save-raw DIR`, which refreshes these
files with real response bodies from the **synthetic golden cases** in
`evals/golden/` — never from real household messages (ADR-0009). Refreshing
is an owner prerequisite, run once a key exists, and the result is committed
like any other fixture. The three bank fixtures above are the one exception:
`evals/golden/bank/` holds no synthetic cases (there is no such thing as a
synthetic bank-feed screenshot), so `--save-raw` must refuse a `--modality
bank` run mechanically rather than silently writing a real screenshot's
response body here (Stage 2.5's own verification, Step 4).

- `bank_cash_and_transfer.json` — one `cash_withdrawal` row and one
  `transfer_out` row, plus an `own_transfer` row on the same feed. The
  ADR-0020 case: the first two are *written*, each under the category the
  code assigns from its kind (`cash`/`transfers`) rather than the
  `category` this fixture deliberately fills with something else
  (`other`, `gifts`) — so a regression that trusted the model's own
  category would fail visibly. The third stays written nowhere, which is
  what keeps "written" and "skipped" distinguishable in one body.
