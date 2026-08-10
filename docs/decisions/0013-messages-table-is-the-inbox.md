# ADR-0013 — The `messages` table is the inbox: acknowledge on durable write, process from the table

**Date:** 2026-08-10 · **Status:** accepted
**Supersedes:** [ADR-0011](0011-at-least-once-delivery-is-not-free.md) — the gap it named is closed
here, and the choice it deferred to Stage 1 is made.
**Related:** [ADR-0006](0006-separate-provenance-tables.md) (`messages` is what arrived),
[ADR-0002](0002-vps-with-docker-compose.md) (single node, single replica),
[ADR-0007](0007-confirmation-with-inline-buttons.md) (write, then reply),
[ADR-0012](0012-stage-0-verification-strategy.md) (the harness these claims are proven in).
Closes requirement R1 of `docs/plans/stage-1-text-to-expense.md`.

## Context

ADR-0011 withdrew the spec's claim that the queue is built into the protocol: aiogram advances
the `getUpdates` offset as soon as updates are fetched and dispatches handlers as background
tasks, so by the time a handler raises, the update is already acknowledged. Stage 0 accepted
that gap because it wrote nothing anyone depends on. Stage 1 writes expenses, so a dropped
message is now a dropped expense — and the loss is silent, which is the failure mode
`docs/vision.md` names as the one to avoid above all.

ADR-0011 required the mechanism to be chosen before the first expense is written, named two
candidates, and required the choice to be recorded in its own ADR. This is that record. Both
candidates replace `dp.start_polling` — no keyword argument changes the offset behaviour. What
separates them is what the acknowledgement waits for.

## Decision

### 1. The acknowledgement waits for the durable write, not for the unit of work

ADR-0011's first candidate — a polling loop that advances the offset only once the whole unit of
work commits — makes *processing* the unit of acknowledgement. It is about thirty lines and needs
no schema change, and it fails where it matters:

- An OpenRouter outage means the offset never advances, so **the queue head blocks every later
  message**. A `/day` report typed after the stuck message waits behind a provider the household
  does not control.
- A poison update — one that fails every time it is processed — blocks forever, and nothing short
  of manual intervention clears it.
- It does not even deliver what it appears to: a crash between acknowledgement and processing
  still loses the update.

The second candidate wins: persist the raw update first, acknowledge, and process from the table
with retries. Retries then belong to one message and are backed off, so a dead provider delays
that message rather than the queue, and a poison message ends at `status='failed'`, loudly.

### 2. `messages` *is* that table; no second one is created

ADR-0011 called the second option an outbox. The rows are inbound updates, so it is an inbox — and
Stage 0 already built it. `messages` already stores what arrived (ADR-0006), already carries
`UNIQUE(telegram_update_id)`, and is already committed by `PersistMessageMiddleware` before any
handler runs. A new table beside it would hold the same rows under a second name and create two
answers to "what arrived" — precisely what ADR-0006 exists to prevent, and the answer the
evaluation dataset is queried from (ADR-0009). The mechanism is four columns (`status`,
`attempts`, `next_attempt_at`, `last_error`), one index on `(status, next_attempt_at)`, and a
drain task that claims one row at a time.

Adding state to a table that already had rows has a cost, paid once. The `status` column's server
default is `pending`, so every message Stage 0 had persisted would have become claimable on the
first `alembic upgrade head` — real messages sent days earlier, re-billed to a model and written
as expenses dated today. Migration `0002` backfills pre-existing rows to `skipped`, and
`tests/integration/test_migration_backfill.py` proves it from a pre-`0002` database rather than
from the already-migrated session fixture.

### 3. The guarantee, in one sentence

> An update is acknowledged only once its durable record is committed; everything after that —
> replies, reports, button handling, extraction — is best-effort and recoverable from `messages`.

### 4. `PersistenceError` is the only exception that withholds the offset

`run_polling` distinguishes exactly two exception classes and nothing else. `PersistenceError`
aborts the batch and leaves the offset untouched, so Telegram redelivers; every other exception is
logged at ERROR and the loop moves to the next update.

`PersistenceError` can originate in exactly one place: the narrow `try` in
`PersistMessageMiddleware` around `users.get_or_create`, `messages.add_if_new` and the commit.
Nothing else may raise it, and no handler may gain that power. The alternative is not merely worse
but fatal: if a handler failure withheld the offset, one failing `sendMessage` would wedge the
household's bot forever, redelivering the same update into the same failure at every poll.

One detail only a real outage surfaces: the loop resets its backoff after a batch is fully
acknowledged, never right after a successful `getUpdates`. Resetting earlier meant a repeated
withhold — Postgres down — slept the minimum delay every time: roughly 3600 iterations an hour,
each logging a full serialised update onto the same disk Postgres needs in order to recover.

### 5. Five statuses, one atomic claim, and a reset at startup

`pending` → `processing` → `done` | `failed`, plus `skipped` for what never reaches a model.
`_initial_status` decides on the way in: plain text not starting with `/` is `pending`; commands,
voice and photos are `skipped` — persisted (ADR-0006), never claimed. Callback taps are not
written to `messages` at all; the record of a tap is a `corrections` row.

`claim_next` is one statement — `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)
RETURNING *` — so the claim is atomic and the transaction is short: the model call never happens
inside an open transaction. A round that ends without a stored `ok` result calls `schedule_retry`,
which either returns the row to `pending` with `next_attempt_at = now() + 30 · 2^(rounds−1)`
seconds (capped at thirty minutes) or, once `attempts` reaches `max_message_attempts`, sets
`failed` with `last_error`.

`processing` is a claim with no owner and no expiry, so a process that dies holding one leaves a
row nothing will claim again. `reset_processing` runs once at startup, before the drain loop
claims anything, and returns every `processing` row to `pending`. It is correct only because
deployment is single node, single replica (ADR-0002); its docstring says so, so a future second
replica trips over it rather than silently stealing live claims.

**A caught exception is not a crash, and the drain must also release a claim it still holds.** The
review round found the hole: `drain_loop` catches everything around `_process_claimed` so that one
bad message cannot stop the loop — which means the process stays alive, `reset_processing` will
not run again until the next restart, and the claimed row would sit in `processing` indefinitely.

The fix needs a guard the finding did not spell out. `extract_and_store` commits the message's
final status — `done`, or `pending`/`failed` through its own `schedule_retry` call — **before**
`_process_claimed` ever calls Telegram. Releasing unconditionally would therefore resurrect an
already-`done` row and reprocess it: a second model call billed and a second, duplicate set of
`expenses` for one message. The release acts only on a row still `processing`, and it runs in a
session of its own, because whatever failed may have left the original session in a bad
transactional state.

### 6. Redelivery is a no-op, which is what makes withholding safe

`messages.add_if_new` is `INSERT … ON CONFLICT DO NOTHING` on `UNIQUE(telegram_update_id)`,
returning `None` for an update already stored. A redelivered update is therefore persisted once,
claimed once and extracted once: at-least-once delivery from Telegram becomes effectively-once
processing here. The cost of an unnecessary redelivery is one no-op insert, which is why
withholding the offset is the cheap side of the trade.

### 7. No global `dp.errors` handler is registered, and none ever should be

aiogram registers `ErrorsMiddleware` first — outermost — on `dp.update`, and it re-raises an
exception **only while no error handler is registered**. A global `dp.errors` handler would make
it swallow `PersistenceError` instead, so `run_polling` would never see it and would acknowledge
an update whose write had failed. Everything above would be silently void, with a green test
suite and no log line saying otherwise. `tests/unit/test_main.py::
test_no_global_error_handler_is_registered` asserts `dp.errors.handlers == []`.

The two halves of the guarantee are proven separately, neither needing a network:
`tests/unit/test_polling_offset.py` drives `run_polling` with a scripted `feed` — which is why
`feed` is a parameter rather than `dp.feed_update` baked in — and
`tests/integration/test_persistence_error_withholds_offset.py` points a real dispatcher at a
refused connection and asserts `PersistenceError` propagates out of `feed_raw_update`.

## Rationale

The acknowledgement is placed at the narrowest point that can carry it: one commit of one row.
Everything downstream — extraction, the reply, a button, a report — is work that can be retried
from what was stored, so none of it needs to be inside the acknowledgement, and none of it may be
able to stop the queue. That split is what buys both properties at once: nothing is acknowledged
before it is safe, and nothing merely slow or broken downstream blocks a household of two from
recording the next expense.

The parts of this record that look like paranoia — one exception class and one origin, a
conditional release, a forbidden error handler — are the same observation restated. This guarantee
is invisible when it works and silent when it breaks, so every way of accidentally voiding it has
to be closed mechanically rather than remembered. That is the same reasoning ADR-0012 applies to
the gates, applied to the thing the gates are protecting.

## Consequences

- **A crash can still lose the reply, never the expense.** `extract_and_store` commits the
  expenses and the `done` status before the confirmation is sent, and `done` is terminal: nothing
  reclaims the message, so nothing re-sends the reply. The household sees no confirmation for a
  message whose expenses are recorded — visible on the next `/day`, and the right way round.
- **A shutdown mid-poll discards the in-flight fetch without acknowledging it.** SIGTERM races the
  long poll rather than waiting it out, so `docker stop`'s ten-second grace period is enough;
  Telegram redelivers whatever was in flight and the unique index absorbs the overlap.
- **A text message is now answered by the drain loop, not by the handler that received it** — up
  to `idle_seconds` (2 s) later. Commands, reports and button taps stay in the fast lane and are
  answered inline.
- **If the release itself fails** — Postgres is the thing that is down — the row stays
  `processing` until the next restart runs `reset_processing`. Logged at ERROR; the drain loop
  survives and claims other messages.
- **Every later modality inherits the lane.** Voice (Stage 2) and photos (Stage 4) become
  `pending` rows and reuse the drain, the retry schedule and the guarantee unchanged.
- **ADR-0011's ERROR log line in `DbSessionMiddleware` is now belt-and-braces**, not the last
  remaining copy of a lost update. It stays, because it costs nothing and covers the case where
  the log is all there is.
- **`reset_processing` is a single-replica assumption written into the runtime.** It is the first
  thing to revisit if the deployment ever gains a second bot process.

## Rejected

**A polling loop withholding the offset until the unit of work commits** (ADR-0011's first
candidate) — the head of the queue then blocks on the least reliable dependency in the system, a
poison update blocks it forever, and a crash between ack and processing still loses the update.
Cheaper to build and strictly weaker.

**A separate `inbox`/`outbox` table** (ADR-0011's second candidate, taken literally) — the same
rows under a second name, a second thing to migrate and truncate, and two answers to "what
arrived" in a project whose evaluation dataset is exactly that question.

**A lease column (`claimed_until`) instead of a startup reset** — the textbook fix for a claim
with no owner: it expires by itself and any worker may take it. It costs a migration, a renewal
loop for as long as a claim is held, and a rule for a renewal that fails mid-call. For two users
on a single-replica deployment, a startup reset plus the conditional release above covers the same
failure modes with no moving parts. Deferred as a **Stage 1.5 candidate**, and the first thing to
build if a second replica ever appears — at which point `reset_processing` becomes actively wrong.

**Registering a `dp.errors` handler for observability** — the single change that voids this ADR
while leaving every test green and every log quiet. Failures are handled inside handlers instead,
and a test forbids the handler.
