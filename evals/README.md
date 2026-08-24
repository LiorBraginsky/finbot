# Evaluation

Choosing a model by intuition is guessing. This directory turns "which model is better"
into a table.

**This is not a gate.** `pytest` proves the plumbing: given an exact recorded response
body, the code parses it, keeps money as `Decimal`, and produces the right rows — it never
calls a model, costs nothing, and is deterministic, which is why it can sit on a branch
that merges itself. `evals/` measures the model: it calls real models, costs real money,
and its results vary between runs. It is not part of `pytest` and is not a gate before
Stage 3 (ADR-0009, ADR-0014). Extraction *correctness* is asserted here, never in
`pytest` — asserting it there would either need a network call in the gate or freeze one
model's output as "the truth", and either one destroys the gate's meaning.

## Two sources of cases

**`golden/` — synthetic, committed.** Hand-written cases, deliberately awkward: several
expenses in one sentence, relative dates (*"taxi yesterday"*), mixed Ukrainian/Russian
speech, an amount spelled out in words, a separator style a parser could misread. These
are files because they must run on a fresh clone with no database.

**Production — queried, never files.** The real dataset already exists in Postgres:
`messages` holds the input, `expenses` holds the truth after corrections, and
`corrections` marks exactly which rows the model got wrong. The runner reads it directly
when executed on the server. Nothing real is ever written into this repository — see
[ADR-0009](../docs/decisions/0009-public-repo-private-eval-data.md). This source arrives
with Stage 3; Stage 1 runs on `golden/` alone.

Every ✏️ tap in Telegram therefore adds a labelled example at no cost, once Stage 3 wires
the production source up.

## Case format

One JSON object per line. `evals/golden/text_v1.jsonl` is the text-modality set the `v1`
prompt is evaluated against; a future voice or photo set gets its own file, named the same
way. Dates are `occurred_offset_days`, relative to the day the runner executes, **never an
absolute date** — a literal date in a committed fixture is a clock bomb that turns green
into red once the calendar moves past it. Amounts are JSON strings, never bare numbers,
for the same reason `finbot.core.money.loads_decimal` exists: a bare JSON number is parsed
through a `float` first, even in a test fixture.

```json
{
  "id": "multi-02",
  "input": "хліб 50 і таксі 200",
  "expected": [
    {"item": "хліб", "amount": "50.00", "category": "groceries", "occurred_offset_days": 0},
    {"item": "таксі", "amount": "200.00", "category": "transport", "occurred_offset_days": 0}
  ]
}
```

`category` is one of the thirteen slugs in `finbot.core.categories.catalog.CATALOG`.
`item` is carried along for a human reading the case table; it is never scored — comparing
it needs a judge model (`item_similar`, below), which is Stage 3's addition, not Stage 1's.

## Metrics

Deterministic wherever a deterministic check exists — a sum is a number, it compares with
`==`. A judge model is slower, costs money and is wrong sometimes; use it only where the
comparison is genuinely fuzzy. Never call a judge where an exact check exists (spec §8).

| Metric | What it catches |
|---|---|
| `schema_ok` | valid structured output on the first attempt, no repair call needed |
| `amount_exact` | the most expensive kind of error |
| `count_exact` | an expense missed, or one invented |
| `category_exact` | filing errors that corrupt reports |
| `date_exact` | relative dates resolved wrongly |
| `cost_per_message` | mean `usage.cost` per call, per model |
| `latency p50 / p95` | how long the user waits |

**`item_similar` — naming, e.g. *bread* vs *a loaf* — is deliberately absent from Stage
1's runner.** It is the one metric that needs a judge model, and Stage 3 is where the
judge harness arrives; until then, the four exact metrics above already gate the decision
(see `docs/plans/stage-1-text-to-expense.md` → Decisions taken → *the criterion*).

The table itself prints raw counts (`9/11`), never percentages — a count names exactly how
many cases failed; a percentage hides it.

## Running

```bash
python -m evals.run --models mistralai/mistral-nemo,openai/gpt-oss-20b,google/gemini-3.6-flash
```

Also: `--modality text|voice` (default `text`), `--cases PATH` (default
`evals/golden/text_v1.jsonl`, or `evals/golden/voice_v1.jsonl` under `--modality voice`),
`--repeats N` (default 1), `--save-raw DIR` (writes every raw response body to `DIR`, for
refreshing `tests/fixtures/openrouter/`), `--today YYYY-MM-DD` (overrides the date
`occurred_offset_days` resolves against; default: today in `Europe/Kyiv`).

Requires `OPENROUTER_API_KEY` — in `.env` or exported — and fails fast with a clear
message if it is absent, rather than reaching OpenRouter and hanging on a 401.

Any change to a prompt or a model is compared before and after, with both results
recorded in `docs/journal.md`.

## Voice (docs/roadmap.md Stage 2)

```bash
python -m evals.run --modality voice --models google/gemini-2.5-flash
```

Same production code path, one layer over: `finbot.core.extraction.voice` builds the
request and parses the response, `finbot.llm.openrouter` performs the call — no repair
loop here either, for the same reason. The same discipline extends to input preparation:
`evals/scoring.py`'s `load_voice_golden_cases` converts each case's audio to mp3 with
`finbot.adapters.telegram.audio.convert_to_mp3`, the exact function the bot itself calls
before extraction (ADR-0015) — imported directly, never reimplemented — so this eval
scores the same request production actually sends, not raw `.oga` bytes mislabelled as
mp3 (ADR-0014 §7: an eval with its own input preparation measures the harness, not the
model).

`evals/golden/voice_v1.jsonl` adds two things to the text case shape: `audio` (a filename
under `evals/golden/voice/`, read relative to `--audio-dir`, default that same directory)
and `expected_transcript_contains` — a list of substrings that must all appear in the
model's own `transcript`, case-insensitively. It is a cheap deterministic proxy for "did
it hear the words" that needs no judge model (never call a judge where an exact check
exists, ADR-0014 §7).

**The audio files themselves are not in this repository.** `evals/golden/voice/` is
git-ignored except for its own `README.md`, which explains how to produce them — they are
the owner's own recordings, and ADR-0009 keeps household audio out of a public repo.
Running this modality on a fresh clone fails on a missing file until some are recorded —
and, the same way, on a missing or failing `ffmpeg` (`--ffmpeg` points at a specific
binary if the one on `PATH` is not the right one; default: resolved from `PATH`, same as
the bot).

`transcript_ok` sits alongside the four exact metrics in the printed table; everything
else — raw counts, never percentages, `mean cost`/`p50`/`p95` — is identical to the text
table.
