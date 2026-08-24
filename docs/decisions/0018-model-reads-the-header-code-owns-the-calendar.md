# ADR-0018 — The model reads the header; the code owns the calendar; the weekday is the checksum

**Date:** 2026-08-24 · **Status:** accepted
**Related:** [ADR-0003](0003-single-step-extraction-not-agent.md) (the model transforms and does
nothing else), [ADR-0013](0013-messages-table-is-the-inbox.md) §5 (the retry backoff that makes
drain time an unusable reference point), [ADR-0012](0012-stage-0-verification-strategy.md) (a
unique index *is* the guarantee; that is why the tests run against a real Postgres),
[ADR-0014](0014-structured-output-and-the-evals-split.md) §7 (what `pytest` is allowed to prove),
[ADR-0017](0017-only-expense-rows-are-written.md) (the other half of the same per-row write
decision). Plan: `docs/plans/stage-2_5-bank-screenshots.md`, Approaches B and C.
Doc section: `docs/vision.md` → *Never guess where you can compute*.

## Context

Text and voice hand the model a date problem it can actually solve: the person said "yesterday",
the prompt says today is `$today`, and `occurred_at` comes back resolved.

A bank feed does not print dates on rows at all. It prints **group headers** above them, in two
forms, neither of which carries a year:

- relative — `Сьогодні`, `Вчора` (and their Russian equivalents);
- absolute without a year — `Сб, 22 серпня`.

A header can also have zero rows visible under it, at the bottom edge of a screenshot.

Asking the model for `occurred_at` here means asking it to do arithmetic on an unstated year from
an unstated reference point, and to do it identically for every row under one header. Getting it
wrong is not a visible failure: a transaction lands a day or a year off, in a table whose whole
purpose is to be believed without checking. And there is no way to tell afterwards which it did,
because the header it read is gone.

This is also the stage's dedup problem, because a bank row's identity begins with its date. A key
built on a mis-resolved date deduplicates nothing.

## Decision

### 1. The model returns the header verbatim, and is never told today's date

`BankRow.date_header` is a string, transcribed exactly as printed. `render_bank_prompt` takes
`catalog` and **nothing else** — there is no `$today` and no `$weekday` in `extract_bank.v1.md`,
and `tests/unit/test_prompt_render.py` asserts the template contains neither.

That is not a stylistic preference. A model that does not know today's date **cannot** resolve
`Сьогодні`, so there is no path by which it silently does so and returns a plausible answer. The
capability is removed rather than discouraged, which is the same move ADR-0014 §3 makes with the
category enum and ADR-0014 §1 makes with `require_parameters`.

### 2. `bank_dates.resolve` owns the calendar, in pure code

`core/extraction/bank_dates.py` is a pure function of `(header, anchor)`: relative words map to the
anchor or the anchor minus a day; an absolute header parses a day and a genitive month name from
Ukrainian or Russian tables; the year is inferred as the **most recent occurrence at or before the
anchor**, because a screenshot shows the past and never the future. Anything unrecognised — an
empty header, an unseen layout, a date that exists in no year such as `31 лютого` — returns `None`.

It lives in its own module for the reason `currency.py` is not inside `text.py`: a calendar
function with an exhaustive table-driven test has nothing to do with request building. Its whole
test suite — both relative forms in both languages, a header crossing a year boundary, an
impossible date, the controls that must resolve — runs with **no model, no network and no Docker**,
and it is the largest single block of verification this stage brought.

### 3. The weekday is redundant information, and that makes it a checksum

A bank app prints `Сб, 22 серпня` — the weekday *and* the day and month. The weekday adds no
information a reader needs, which is exactly what makes it useful to a machine: it is a second,
independent encoding of the same fact.

So `resolve` infers the year, then verifies the computed date's own `weekday()` against the
weekday the header printed. A mismatch means something upstream is wrong — an OCR slip on the day,
a misread month, a year inference defeated by a screenshot older than it looks — and returns
`None`. The row is then not written: counted in `unresolved_date` and reported, never guessed
(ADR-0017 §5).

This is the same shape as Stage 4's planned `sum(expenses) ≈ receipt_total`: take a quantity the
input states twice, compute it once, and compare. An absolute header printed with no weekday at
all resolves without a checksum, which is safe — "most recent occurrence at or before the anchor"
is only wrong for a feed more than a year old.

### 4. The anchor is `message.created_at`, not `now()` at drain time

`adapters/telegram/runner.py` computes `anchor_date = message.created_at.astimezone(settings.tz)
.date()` and passes it in; `core` never touches a clock for this.

Arrival, not processing, for two reasons. A whole feed then inherits **one** anchor, so every row
in one screenshot resolves against the same reference point regardless of when each was written.
And drain time is not a stable quantity: ADR-0013 §5's backoff runs from 30 s to thirty minutes,
and a message can sit `pending` far longer than that. A drain that crossed midnight would misdate
*every* relative row in the feed by a day — the failure mode that is both the easiest to hit and
the hardest to notice.

### 5. The known wrong case, stated plainly

A screenshot **taken** yesterday and **sent** today resolves `Сьогодні` to today. Every row under
that header lands a day late.

This is unavoidable without asking the user, and asking costs the one thing `docs/vision.md`
protects above all: capture must cost nothing. So it is not fixed; it is made visible. The note's
**first line** states the anchor date — `🧾 Скріншот за 24.08 — дати рахував від цього дня.` — so a
wrong assumption is on screen next to the rows it produced rather than buried in a column nobody
reads. Until Stage 6 owns date and amount editing, the remedy is 🗑 and retyping the affected rows
by hand; the roadmap records that deferral as this stage's one known-wrong case.

### 6. The dedup key is `date | time | amount`, and merchant is deliberately excluded

`expenses.bank_txn_key` is `f"{occurred_at.isoformat()}|{_normalize_time(time)}|{amount:.2f}"`,
unique per `(user_id, bank_txn_key)`, inserted with `ON CONFLICT DO NOTHING … RETURNING id`. A
`None` return *is* the "already recorded" counter the reply needs — never a `SELECT`-then-filter,
for ADR-0012's reason: this project runs its tests against a real Postgres precisely so a unique
index can be relied on as the guarantee instead of re-derived in application code.

**`time` is normalised before it enters the key, not passed through verbatim.** `"9:05"` and
`"09:05"` name the same clock time, but `MODEL_FALLBACKS` is shared across modalities, so a retry
served by the fallback is a *different model reading the same pixels* — exactly where formatting
like zero-padding drifts. Without normalising, two reads of one transaction would mint two keys and
double-count money, the same failure mode merchant's exclusion above already exists to prevent.
`bank_txn_key` therefore normalises `time` through a `^(\d{1,2}):(\d{2})$` match first: a match
zero-pads the hour, anything else (empty, unrecognised, or too long to be a clock time at all)
collapses to `""`, the same "no information" value `time=None` already produces. This also closes a
second hole for free: `bank_txn_key` is `String(64)`, and an unbounded `time` reaching the key
verbatim could overflow it and raise `StringDataRightTruncation` out of `create_bank_row` — the
normalised form is always five characters or empty.

**Merchant is excluded on purpose, and this is the load-bearing part of the key.** Merchant strings
are the noisiest field on the wire — a trailing reference number, a truncated name, a different
transliteration. Two reads of the *same pixels* can differ there, and a key containing that
variance would fail to match, insert a second row, and **double-count money**: the worst outcome
this stage can produce, and precisely the outcome dedup exists to prevent.

The trade is a rare false collision — two transactions of the same amount in the same minute on the
same day — which **under-counts and is reported** as `Вже було`. Under-counting loudly beats
over-counting silently, the same asymmetry ADR-0017 is built on. `tests/unit/test_bank_key.py`
pins it: two rows differing only by merchant produce the *same* key, so a future "improvement" that
adds merchant back fails there and has to argue with this record.

`user_id` sits in the **index**, not in the string. Uniqueness is therefore per person: the two
people in the household cannot collide with each other, and only one person's own two accounts
share a key space. Two banks are two ledgers.

The unique constraint is **not** conditioned on `deleted_at`. A bank row the household deleted with
🗑 is therefore not resurrected by re-sending the same screenshot — a deliberate choice, pinned by
`tests/integration/test_bank_dedup.py`, because a deletion is a decision and re-sending a
screenshot is not a decision to reverse it.

## Rationale

`docs/vision.md` states two principles that decide this together: *the model transforms; the code
decides*, and *never guess where you can compute*. A header is genuinely unstructured input and
belongs to the model. A year, a weekday and an offset from an anchor are arithmetic and belong to
code. Splitting them at that exact line moves the whole date question from "a thing we hope the
model got right" into a pure function with a table-driven test — free to run, deterministic, and
allowed to gate a branch that merges itself (ADR-0014 §7).

The checksum is what turns the split from tidy into valuable. Keeping the header verbatim is not
merely faithful transcription; it preserves the redundancy the bank app printed, and redundancy is
the only thing that lets code detect an error it cannot otherwise see. Without it, "the model
returns a header instead of a date" would be a stylistic preference. With it, a misread day is
caught before it becomes a row.

The dedup key then rests on that. Every one of its three components is either computed by code
(the date) or a short printed literal (time, amount at two decimals); the one field with real OCR
variance is the one field left out.

## Consequences

- **The date logic is verified for free, exhaustively, and forever.**
  `tests/unit/test_bank_dates.py` needs no model, no network and no Docker, and covers the
  weekday mismatch, the year boundary and the impossible date as first-class cases.
- **An unseen layout degrades to unresolved rows, never to wrong rows.** Monobank is unobserved;
  a header shape the tables do not know returns `None`, and the reply says `Не зрозумів дату: N`.
- **A screenshot sent a day after it was taken is silently a day late until the household notices
  it in the note.** Stated, not hidden; Stage 6 is the real fix.
- **R6 ("re-sending the same screenshot is a no-op") holds *per arrival day*, not forever**, for a
  relative header. `Сьогодні`/`Вчора` resolve against the anchor (§4), so the exact same screenshot
  re-sent tomorrow resolves to a different date and genuinely writes again — this is not a bug in
  the dedup key, it is what "the model is never told today's date" (§1) implies once the date the
  household means has itself moved on. The note printing the anchor (§5) is what makes this
  visible rather than a silent surprise.
- **The relative-word and month tables are the thing to extend when a new bank appears** — one
  dictionary each, plus rows in the existing test table, no new code path.
- **`time=None` and `time=""` collapse to the same key**, so a feed that prints a time for a row
  once and not the next time does not double-write it.
- **Two same-amount transactions in the same minute deduplicate falsely.** The second is counted
  as `Вже було` and named in the note. Under-counted and visible, by design.
- **The key is `NULL` for every text and voice expense**, and NULLs are distinct in Postgres, so
  the unique constraint is silent for two of the three modalities and migration `0004` needed no
  backfill — deliberately the mirror image of migration `0002`'s lesson (ADR-0013 §2), where a
  server default changed the meaning of rows that already existed.
- **A screenshot↔manual collision is a separate problem the key cannot solve**, since a typed
  expense has no key at all. `manual_duplicate_candidates` probes the exact `(occurred_at, amount)`
  pairs actually written and names them in the note; both rows keep existing and the human resolves
  it with 🗑. A rounded manual entry will not match, and that is stated rather than papered over.
- **The eval scores dates through this same resolver** against each case's own `anchor_date`
  (ADR-0019 §4), so `date_exact` measures the production calendar, not a second copy of it.

## Rejected

**The model returns `occurred_at`, as text and voice do** (Approach B1) — zero new code, and it
turns year inference into an unverifiable guess while destroying the only redundancy the input
offered. `Сьогодні` would have no checksum at all, and a wrong year would be indistinguishable
from a right one after the fact.

**Both: the model resolves, the code resolves, and the two are compared** (Approach B3) — two
signals sound strictly better until the disagreement case needs a rule, and there is no good one.
It is also a second thing to keep in sync, for a guarantee the checksum already provides on its
own.

**`now()` at drain time as the anchor** — the default, and wrong by a whole day for any feed whose
message was retried across midnight. ADR-0013's backoff exists precisely so a message can be
processed much later than it arrived.

**Including merchant in the dedup key** — the intuitive key, and the one that double-counts money
the first time two reads of the same pixels disagree on a merchant string. Pinned against by a
test, on purpose.

**`SELECT` existing keys, filter in Python, then `INSERT`** (Approach C1) — puts the guarantee in
application code, in a project that starts a real Postgres in its test suite specifically so it
does not have to.

**A caption as a date override** — the obvious escape hatch for the known-wrong case, and a second
input channel steering a money-writing prompt. A caption is persisted by `to_incoming` and ignored
by extraction; "caption as a date override" is a named non-goal.
