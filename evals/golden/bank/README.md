# Bank-feed golden set

This directory holds only this README. The case file (`bank_v1.jsonl`) and the
screenshots it labels are **never committed here or anywhere else in this
repository** — see [ADR-0009](../../../docs/decisions/0009-public-repo-private-eval-data.md)
and [ADR-0016](../../../docs/decisions/0016-narrow-exception-for-owner-named-voice-samples.md).

Voice's golden audio is git-ignored but the *labels* (`voice_v1.jsonl`) are
committed, because they describe an agreed script the owner deliberately
spoke — synthetic content. A bank screenshot's labels are the opposite: real
amounts, real merchant names, a third party's name in a transfer line. The
labels are exactly as private as the pixels, so **both** live outside this
repository (docs/plans/stage-2_5-bank-screenshots.md, Reality check #3,
Approach F).

`python -m evals.run --modality bank` therefore takes two required flags with
no default, both checked to resolve outside this repository before anything
is read from them (`evals.paths.ensure_outside_repo`, the same ADR-0016
invariant `evals/pull_voice_samples.py`'s `--out` enforces):

```bash
python -m evals.run --modality bank --models google/gemini-3.5-flash-lite \
  --cases ~/finbot-vision-samples/bank_v1.jsonl \
  --images-dir ~/finbot-vision-samples
```

A fresh clone cannot run this eval at all until a case set exists — the
correct, honest failure, not a reason to commit one.

## Producing a set

The three screenshots the Stage 2.5 spike used already exist locally
(`~/finbot-vision-samples/photo_*.jpeg`, filenames containing spaces —
rename them to case ids on the way in, e.g. `privat-day-01.jpeg`). There is
no pulling tool for this modality — unlike voice notes, these were never
sent to the bot, so there is nothing in Postgres to resolve a `file_id` from;
copying local files by hand is the whole process:

1. Copy each screenshot into a directory outside this repository, renamed to
   a case id (`privat-day-01.jpeg`, `privat-day-02.jpeg`, ...).
2. Hand-label **every visible row** in each screenshot into a `bank_v1.jsonl`
   in that same directory, one JSON object per line, in the format below.
3. Optional: crop account balances out of future samples before adding them.

## Case format

```json
{"id": "privat-day-01", "image": "privat-day-01.jpeg", "anchor_date": "2026-08-24",
 "is_transaction_feed": true,
 "rows": [
   {"kind": "savings",      "amount": "6.35",   "partially_visible": false},
   {"kind": "own_transfer", "amount": "123.60", "partially_visible": false},
   {"kind": "expense",      "amount": "193.65", "category": "groceries",     "occurred_offset_days": 0,  "partially_visible": false},
   {"kind": "expense",      "amount": "43.19",  "category": "subscriptions", "occurred_offset_days": 0,  "partially_visible": true}
 ]}
```

- **`id`** — matches the image filename's stem.
- **`image`** — the filename in `--images-dir` (jpeg, png or webp — the same
  three formats `adapters.telegram.images.sniff_mime` accepts).
- **`anchor_date`** — **absolute**, ISO-8601, one per case — the one place
  this stage's case format diverges from `text_v1.jsonl`/`voice_v1.jsonl`'s
  run-date-relative rule. A screenshot's date headers are baked into its
  pixels: an offset relative to the day the runner happens to execute would
  drift by a day every day, while an offset relative to a fixed per-case
  anchor is stable and exercises exactly what production does — `message.
  created_at` in the household's timezone (Approach B). `--today` has no
  effect on `--modality bank`.
- **`is_transaction_feed`** — `false` for a case that is deliberately *not* a
  bank-feed screenshot (a receipt, an unrelated photo) — `feed_ok` scores
  whether the model agrees.
- **`rows`** — **every visible row in the screenshot, in feed order** (top to
  bottom), not just the ones that end up written. Amounts are JSON
  **strings**, never bare numbers, for the reason `evals/README.md`'s Case
  format section states.
  - **`kind`** — one of the five wire values: `expense`, `income`,
    `savings`, `own_transfer`, `transfer_out`.
  - **`amount`** — present on every row, regardless of kind: `no_false_
    expense` needs the amount of every non-expense row too, to know what a
    model must *never* write as spending.
  - **`category`** and **`occurred_offset_days`** — only ever present on a
    `kind: "expense"` row; omit both for every other kind. `category` is one
    of the thirteen slugs in `finbot.core.categories.catalog.CATALOG`.
    `occurred_offset_days` is relative to this case's own `anchor_date`
    (`0` same day, `-1` the day before).
  - **`partially_visible`** — `true` for a row cut off at the edge of the
    screenshot or otherwise unreadable. A `partially_visible: true` expense
    row must never be written (R4) — `dropped_exact` scores whether the
    model correctly left it unwritten instead of guessing.

## Metrics

`python -m evals.run --modality bank` prints `schema_ok`, `feed_ok`,
`count_exact`, `kind_exact`, `dropped_exact`, `expense_count_exact`,
`amount_exact`, `category_exact`, `date_exact` (the last two computed only
for rows this case actually labelled `expense`, and only when `count_exact`
holds — an off-by-one row count makes positional comparison meaningless),
mean cost and latency p50/p95, alongside:

> **`no_false_expense` — set-based, and deliberately asymmetric.** Every
> amount the model's output would actually cause to be *written* to
> `expenses` must appear, with multiplicity, among this case's own
> `expense`-kind, fully-visible row amounts. It counts one direction only —
> money recorded that was never spent — and stays scoreable even when the
> model miscounts rows entirely, which is exactly when every positional
> metric above goes blind. This is the metric `MODEL_VISION`'s choice turns
> on: a savings jar or a transfer written as spending destroys a month of
> reports; a missed expense is a nuisance the household will notice and
> retype.

See `docs/plans/stage-2_5-bank-screenshots.md`'s Owner prerequisite 3 for the
pre-registered gate this table is read against, and `docs/journal.md` for the
run once it happens.
