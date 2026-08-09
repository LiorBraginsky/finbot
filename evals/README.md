# Evaluation

Choosing a model by intuition is guessing. This directory turns "which model is better"
into a table.

## Two sources of cases

**`golden/` — synthetic, committed.** Hand-written cases, deliberately awkward: several
expenses in one sentence, relative dates (*"taxi yesterday"*), foreign currency, mixed
Ukrainian/Russian speech, an amount spelled out in words. These are files because they
must run on a fresh clone with no database.

**Production — queried, never files.** The real dataset already exists in Postgres:
`messages` holds the input, `expenses` holds the truth after corrections, and
`corrections` marks exactly which rows the model got wrong. The runner reads it directly
when executed on the server. Nothing real is ever written into this repository — see
[ADR-0009](../docs/decisions/0009-public-repo-private-eval-data.md).

Every ✏️ tap in Telegram therefore adds a labelled example at no cost.

## Case format

```json
{
  "id": "multi-item-01",
  "kind": "text",
  "input": "хліб 50 і таксі 200",
  "expected": [
    {"item": "хліб", "amount": "50.00", "currency": "UAH", "category": "food"},
    {"item": "таксі", "amount": "200.00", "currency": "UAH", "category": "transport"}
  ]
}
```

## Metrics

Deterministic wherever a deterministic check exists — a sum is a number, it compares with
`==`. A judge model is slower, costs money and is wrong sometimes; use it only where the
comparison is genuinely fuzzy.

| Metric | What it catches |
|---|---|
| `amount_exact` | the most expensive kind of error |
| `count_exact` | an expense missed, or one invented |
| `category_exact` | filing errors that corrupt reports |
| `date_exact` | relative dates resolved wrongly |
| `item_similar` | naming — *bread* vs *a loaf*. The only judged metric |
| `cost_per_item` | what one recorded expense costs, per model |
| `latency p50 / p95` | how long the user waits |

## Running

```bash
python -m evals.run --models a,b,c
```

Arrives with Stage 3. Any change to a prompt or a model is compared before and after,
with both results recorded in `docs/journal.md`.
