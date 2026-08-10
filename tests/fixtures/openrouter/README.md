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

- `ok_two_items.json` — two expenses, `usage.cost` present, and `model`
  deliberately **different** from the requested primary model, to prove
  `model_id` is read from the response and never from config.
- `ok_empty.json` — `expenses: []` (spec §7's "message names no amount").
- `ok_fenced.json` — the assistant content wrapped in a ` ```json ` fence,
  which some providers add even under a strict `response_format`.
- `invalid_json.json` — the content is prose, not JSON at all.
- `no_cost.json` — `usage.cost: null`, which the OpenRouter response schema
  types as nullable; `extractions.cost_usd` must tolerate it.

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
like any other fixture.
