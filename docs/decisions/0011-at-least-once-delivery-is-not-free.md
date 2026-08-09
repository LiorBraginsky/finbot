# ADR-0011 — At-least-once delivery is not free; the spec's claim is withdrawn

**Date:** 2026-08-09 · **Status:** accepted
**Supersedes:** the redelivery claim in `docs/specs/2026-08-09-expense-capture-design.md`
§4 and §7.

## Context

The design spec asserts:

> *"Offsets advance only after successful processing. If Postgres is down, the update is
> not acknowledged and Telegram redelivers it. The queue is built into the protocol."*

This is false for aiogram's long-polling loop, and the error was found while planning
Stage 0. aiogram advances the `getUpdates` offset as soon as updates are fetched, and
dispatches handlers as background tasks; a handler exception is logged and the offset
moves on regardless. Telegram's 24-hour retention exists, but nothing in the default
polling loop withholds acknowledgement to make use of it.

The guarantee has to be built. It is not inherited from the protocol.

## Decision

**Stage 0: accept the gap, make it loud.** `DbSessionMiddleware` catches, rolls back, and
logs the fully serialised `Update` at ERROR before re-raising. Once the offset has moved,
that log line is the only remaining copy. Stage 0's done-criterion is unaffected, and no
table or interface changes.

**Stage 1: close it, before any expense is written.** Decide between:

- a custom polling loop that advances the offset only after the unit of work commits, or
- an outbox: persist the raw update first, acknowledge, and process from the table with
  retries.

The second is more work and strictly more robust — a crash between commit and processing
is recoverable, which the first does not give. Whichever is chosen gets its own ADR
recording the mechanism.

## Rationale

A dropped message is a dropped expense, and the loss is silent: nobody notices that the
taxi they dictated on a bad-network evening never arrived. That is worse than an error
reply, because the ledger looks complete while being wrong — the failure mode
`docs/vision.md` names as the one to avoid above all.

Deferring the mechanism to Stage 1 rather than building it now is deliberate: Stage 0
writes nothing anyone depends on, and the right mechanism depends on how the extraction
step ends up structured.

## Consequences

- The spec is Truth but is not infallible. Where a later finding contradicts it, the
  finding wins **through an ADR** — the spec is not edited in place. This is the first
  instance and sets the precedent.
- Until Stage 1 closes this, a failed write is recoverable only from container logs on
  the VPS.
- The Stage 1 plan must carry this as an explicit requirement, not a nice-to-have.

## Rejected

**Silently keeping the spec's wording** — leaves a false guarantee in the authoritative
document, and someone (human or agent) would eventually build on it.
**Fixing it inside Stage 0** — the correct mechanism depends on Stage 1's structure, and
Stage 0 has nothing to lose yet.
