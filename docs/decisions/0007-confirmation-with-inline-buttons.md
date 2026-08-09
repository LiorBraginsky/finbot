# ADR-0007 — Confirm after writing, correct with inline buttons

**Date:** 2026-08-09 · **Status:** accepted

## Context

Speech recognition will occasionally hear "250" for "two hundred". Three options: write
silently and fix later; write and immediately show a correctable confirmation; or ask for
confirmation before writing anything.

## Decision

Write first, then reply with a summary and inline ✏️ / 🗑 buttons. Several expenses in
one message produce one numbered confirmation, not one message each.

## Rationale

- Confirming before every write becomes unbearable at dozens of entries a day.
- Writing silently accumulates errors that surface a month later, when nobody remembers
  what the taxi actually cost.
- Writing before replying means a crash after the write loses nothing.
- The correction happens two seconds after the event, while the memory is fresh — which
  is precisely when a human correction is reliable.

## Consequences

- `expenses.bot_message_id` is required so a button knows which rows it controls. One
  bot message maps to many expenses; no join table is needed.
- Deletion is soft. A year of household spending is history, not cache.
- The correction interface doubles as a labelling pipeline (see ADR-0006).

## Rejected

**Silent write** — cheapest to build, needs a correction UI much earlier, and quietly
corrupts reports in the meantime.
**Confirm before write** — safest per entry, unusable at this frequency.
