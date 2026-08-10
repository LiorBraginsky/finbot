# ADR-0014 — Structured output under a schema derived from the domain model; the model is measured in `evals/`, not in `pytest`

**Date:** 2026-08-10 · **Status:** accepted
**Related:** [ADR-0003](0003-single-step-extraction-not-agent.md) (one call, fixed output schema),
[ADR-0004](0004-openrouter-and-model-routing.md) (the gateway and its data policy),
[ADR-0005](0005-controlled-category-taxonomy.md) (the taxonomy this enforces),
[ADR-0006](0006-separate-provenance-tables.md) (`extractions` is the dataset, not logging),
[ADR-0009](0009-public-repo-private-eval-data.md) (synthetic cases are files, real ones are a
query), [ADR-0012](0012-stage-0-verification-strategy.md) (the gate this must not weaken).
Doc section: `evals/README.md`, which states the split in the same words.

## Context

Stage 1's extraction is one call with a fixed output schema (`CLAUDE.md` rule 1, ADR-0003), every
call is a row in `extractions` (rule 6), and money crosses a JSON boundary on the way in (rule 2).
Three questions had to be answered together: how the model is made to return the right shape, how
that shape stays in agreement with the Pydantic model the rest of the code uses, and **where the
quality of an extraction gets asserted**.

The third question decides what the gate means. This repository merges to `main` unattended on
green gates, so anything asserted in `pytest` has to be deterministic and free; anything that
needs a real model is neither.

## Decision

### 1. `response_format` strict, and `provider.require_parameters: true`

Every request carries:

```json
"response_format": {"type": "json_schema",
                    "json_schema": {"name": "extraction_result", "strict": true, "schema": {…}}},
"provider": {"data_collection": "deny", "require_parameters": true}
```

`require_parameters` is not belt and braces. **Structured-output support is a property of the
endpoint serving a model, not of the model**: the same id can be served by several providers, and
only some of them implement `response_format`. Without `require_parameters: true` a request can be
routed to one that ignores it and returns prose — which lands downstream as `invalid_json` and
reads as a bad model when it was bad routing. The evaluation dataset would then record the wrong
conclusion, and the fix would be to change models rather than to constrain the route. With it,
OpenRouter only considers endpoints that honour the parameter, and a model with no such endpoint
has no eligible route at all — which is how `qwen/qwen3.7-flash`, the cheapest candidate, was ruled
out against the live catalogue before it cost anything.

`data_collection: "deny"` is ADR-0004's consequence made per-request rather than trusted to an
account setting.

### 2. The strict schema is hand-derived from the domain model, and the derivation is tested

`ExpenseDraft` / `ExtractionResult` are the domain shape; `text_json_schema(slugs)` is the wire
shape. The wire shape is written by hand because Pydantic's emitter cannot produce it: it emits
`$defs`/`$ref` for the nested model, omits `additionalProperties: false`, and renders `Decimal` as
`anyOf[{"type": "number"}, {"type": "string"}]`. Strict mode accepts none of the three, and it
requires `additionalProperties: false` plus a `required` naming every property on **every** object
node, not only the root.

Two hand-maintained shapes that must agree is the drift ADR-0012 built a guard for, so this pair
is guarded too. `tests/unit/test_extraction_schema.py` walks the emitted schema recursively —
every object node carries `additionalProperties: false` and `sorted(required) == sorted(properties)`,
no `$ref` or `$defs` anywhere, at least two object nodes so the walk cannot pass vacuously — and
round-trips a valid instance through `ExtractionResult`, whose `extra="forbid"` closes the other
direction. The derivation is tested, not trusted: a schema change that reintroduced the exact bug
this exists to catch would fail on shape, not on a remembered list of today's fields.

### 3. The category `enum` is what makes ADR-0005 mechanical

`category` is `{"type": "string", "enum": [<the thirteen catalog slugs, in catalog order>]}`.
ADR-0005's controlled taxonomy stops being a request in a prompt and becomes a property of
decoding: a slug outside the catalogue cannot be produced. The prompt still lists the categories,
because the model has to pick the *right* one, but nothing depends on politeness being obeyed.
`ExpenseDraft` still coerces an unknown slug to `other` with a WARNING — for a repaired response
or a future looser schema — because filing under `other` is worth more than a repair call.

### 4. `model_id` and `cost_usd` come from the response body

`model_id = body["model"]` — the model that actually served the request — and
`cost_usd = body["usage"].get("cost")`, already a `Decimal` because the whole body is parsed
through `loads_decimal`. The fallback list travels as `models` (plural) in one request rather than
in a retry wrapper, so recording configured ids would silently mislabel **every fallback row** in
the evaluation dataset: a fallback's answers, cost and latency attributed to the primary, in the
one table ADR-0006 exists to make trustworthy. `cost_usd` is nullable in the API and therefore
nullable in the column; it is read, never estimated.

The review round forced one exception, and it is deliberately not a plausible value. An `LlmError`
means no response body ever arrived, so there is no served model to record; the row stores the
sentinel `"no-response"` (`pipeline.NO_RESPONSE_MODEL_ID`). Writing `models[0]` there would read
as a real value in exactly the place a "which model errors on us" query looks.

### 5. `json.loads(parse_float=Decimal)`, enforced by an AST test rather than a convention

`core.money.loads_decimal` is the only JSON entry point in the codebase. Plain `json.loads` parses
`1234567.89` through the C float parser before `Decimal` ever sees it, and a `Decimal` built from
that float is already lossy — `parse_float=Decimal` is the entire distance between rule 2 and a
float in the ledger, and the JSON wire is the one place rule 2 can be broken silently.

A convention cannot hold that, because `json.loads(body)` is the obvious thing to write and it
looks correct. `tests/unit/test_no_float_money.py` walks `src/finbot/` and `evals/` with `ast`,
resolves calls to json's `loads` **by binding** rather than by name — module attribute or bare
name, aliased either way — and requires a `parse_float` keyword on each; `core/money.py` is the
single allow-listed file. Like `test_layering.py` it is table-driven over the grammar, with control
cases that must not be flagged, including the substring trap `loads_decimal`. In a project with no
`lint-rules/` directory this *is* the lint rule (`.claude/orchestration.md` → `## Truth`). The
review round widened it to `evals/` after finding a `json.loads` there with no `parse_float`.

### 6. HTTP 200 is not a promise about the envelope

Transport failure and schema violation were the two failure classes the design started with. There
is a third: a 200 whose body is not the shape the code assumed — `{"error": {…}}` with no
`choices`, `"choices": []` from a dead route, `message.content: null` from a refusal or a
reasoning-only generation. Each of those raised `KeyError`/`IndexError`/`TypeError` straight out of
the parser, and `extract_and_store` catches neither: the call was billed and **no `extractions`
row was ever written**. That breaks rule 6 silently, and it does so exactly where the interesting
failures are — a billed call invisible to the dataset rule 6 exists to build.

`parse_response_body` therefore validates the envelope and raises `LlmError` with the body itself
as `raw`, so "the response made no sense" becomes a recorded row like any other. Three fixtures
pin the class: `error_envelope_200.json`, `no_choices.json`, `null_content.json`.

### 7. The split: `pytest` proves the plumbing, `evals/` measures the model

> **`pytest` proves the plumbing.** Given this exact recorded response body, the code parses it,
> keeps money as `Decimal`, writes N rows and sends one confirmation. It never calls a model, costs
> nothing, and is deterministic — which is why it can gate a branch that merges itself.
>
> **`evals/` measures the model.** It calls real models, costs real money, and its results vary
> between runs. It is not part of `pytest` and is not a gate before Stage 3.
>
> Extraction *correctness* is asserted in `evals/`, never in `pytest`. Asserting it there would
> need either a network call in the gate or one model's output frozen as truth, and both destroy
> the gate's meaning.

Both sides run the same production code path. `evals/run.py` imports `core.extraction.text` and
`llm.openrouter` and has neither its own prompt nor its own parser — an eval with either measures
the harness, not the model. `tests/support/fake_llm.py` feeds recorded whole bodies through
`parse_response_body`, the same function `OpenRouterClient` calls, so a change to cost or model-id
extraction cannot pass in tests while failing in production. The one deliberate divergence is the
repair loop: `run_case` makes a single call, because `schema_ok` means valid on the first attempt
and repairing would turn a real miss into a hidden pass.

## Rationale

Everything above is one idea applied at three boundaries. At the routing boundary, a guarantee that
depends on which provider answered is not a guarantee, so `require_parameters` makes it a
precondition of the route. At the JSON boundary, a rule that depends on remembering to pass a
keyword is not a rule, so an AST test enforces it. At the gate boundary, a green run that depended
on a model's mood would not be evidence, so model quality is measured somewhere that is allowed to
cost money and to vary, and the gate keeps only what is deterministic.

The dataset is the reason the small details are not small. `extractions` is not logging — it is the
thing every later model choice, prompt version and regression check is decided from. A mislabelled
`model_id`, a missing row after a billed call, or a float where a `Decimal` belongs corrupts that
record quietly, and unlike a crash, nothing surfaces it later.

## Consequences

- **`strict: true` plus `require_parameters: true` narrows the eligible endpoints**, and can leave
  a model with none. That is the intended behaviour, and it is a prerequisite check before a
  candidate enters the eval list, not a runtime surprise.
- **`extractions.cost_usd` is nullable and sometimes null**, so cost reporting is a mean over the
  calls that reported one, never a total presented as complete.
- **A model regression cannot fail CI.** No gate before Stage 3 runs a model. The discipline in the
  meantime is procedural: any prompt or model change is compared with `python -m evals.run` before
  and after, with both tables in the journal entry (`.claude/orchestration.md` → `## Gates`).
- **Two hand-maintained shapes stay in one file, twenty lines apart.** The tests prove strictness
  and agreement on instances, not field-by-field equivalence: a field added to `ExpenseDraft` with
  a default and forgotten in `text_json_schema` would pass both. Proximity is the guard; if the two
  ever separate, that guard is gone.
- **The recorded fixtures are the contract with a provider that can change under us.** They are
  hand-written from the documented response schema until an owner refreshes them with
  `python -m evals.run --save-raw`, from synthetic golden cases only (ADR-0009). A provider that
  starts returning a new envelope shape shows up as a new fixture and an `LlmError` path, not as a
  crash in production.
- **Adding a category is a schema change.** The `enum` is generated from the catalog, so Stage 5's
  dynamic list changes the wire schema per request — which is why the schema is a function of
  `slugs` and not a constant.

## Rejected

**Taking `model_json_schema()` as-is** — the obvious move, and it produces a schema strict mode
rejects on three separate counts. Post-processing Pydantic's output was considered and is worse
than deriving: the post-processor is the same amount of hand-written code, plus a dependency on the
emitter's shape staying what it is today.

**Asserting extraction correctness in `pytest`** — it would need either a network call inside the
gate, which makes the gate cost money, flake and depend on a provider, or one model's output frozen
as the expected value, which asserts that today's model still behaves like the day it was recorded
and says nothing about whether the answer is right. Either way a green gate stops meaning what
ADR-0012 needs it to mean.

**A judge metric (`item_similar`) in Stage 1** — naming ("хліб" vs "a loaf") is the one comparison
`==` cannot do, and it needs a judge model, a judge prompt and a way to evaluate the judge. Deferred
to Stage 3 with the rest of the judge harness. Never call a judge where an exact check exists
(spec §8): the four exact metrics already decide Stage 1's model choice, and a fuzzy metric added
next to them would only dilute a criterion written to be unfudgeable.
