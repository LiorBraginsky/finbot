# ADR-0008 — Convert currency at write time and store the rate

**Date:** 2026-08-09 · **Status:** accepted

## Context

Expenses occur in UAH, USD and EUR. Conversion could happen at read time, using today's
rate, or at write time, using the rate for the date of the expense.

## Decision

Convert on write. Store `amount`, `currency`, `amount_uah`, `fx_rate` and `fx_rate_date`
on every row. Rates come from the NBU public API, which needs no key and serves
historical dates, cached daily in `fx_rates`.

An amount with no stated currency defaults to UAH.

## Rationale

A March report recalculated at today's rate reports a number that was never true. Storing
the converted value with the rate that produced it makes every historical report stable
and auditable.

## Consequences

- Rates must be fetched for the expense date, not only today, when a user records
  something retroactively.
- If the rate API is unavailable, the last known rate is used and the confirmation says
  so. **Recording an expense is never blocked by an exchange rate.**
- Amounts are `numeric(12,2)`, never `float` — binary floating point loses cents, and the
  discrepancy is unexplainable later.

## Rejected

**Convert at read time** — one less column, and makes every historical report a moving
target.
**Store only the original currency** — pushes the same problem into every query.
