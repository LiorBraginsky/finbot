# ADR-0001 — Postgres, not a spreadsheet

**Date:** 2026-08-09 · **Status:** accepted

## Context

The ledger could live in a Google Sheet, in SQLite, or in Postgres. A spreadsheet is the
lowest-effort option and would be readable by both users directly.

## Decision

Postgres, running in the same `docker compose` stack as the bot.

## Rationale

- Dozens of writes a day from two clients hit Sheets API rate limits and races, and
  Sheets has no transactions.
- Every report beyond a plain sum becomes manual parsing in application code instead of
  one `GROUP BY`.
- Learning a small amount of backend engineering is an explicit goal of the project. A
  spreadsheet teaches none of it.

SQLite would be sufficient for two users and simpler to back up, but it exercises
nothing new and would have to be migrated later anyway.

## Consequences

- Backups are now the operator's responsibility: `pg_dump` on cron with an off-site copy,
  from day one.
- Neither user can look at the raw data without a tool. Acceptable while the confirmation
  buttons cover corrections; Stage 6 adds a UI if they do not.

## Rejected

**Google Sheets** — rate limits, no transactions, no useful querying.
**SQLite** — adequate, but teaches nothing and defers the same migration.
**Supabase** — adds a network hop and an external dependency for auth, realtime and
storage features this project does not use.
