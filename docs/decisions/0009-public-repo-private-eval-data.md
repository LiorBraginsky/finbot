# ADR-0009 — Public repository, private data stays in the database

**Date:** 2026-08-09 · **Status:** accepted (a narrow exception carved out by
[ADR-0016](0016-narrow-exception-for-owner-named-voice-samples.md); amended a second
time by [ADR-0019](0019-bank-eval-labels-are-as-private-as-its-pixels.md), which finds
that "`evals/golden/` holds committed synthetic cases" does not survive a modality whose
*labels* are the private data, and moves a bank-feed golden set — cases and images alike
— out of the repository entirely)

## Context

The repository is public — it doubles as a portfolio. The ledger is a real household's
finances. Secrets live in `.env`, data lives in Postgres on the VPS, so ordinary code
carries nothing sensitive.

One design idea threatened that separation: growing the evaluation dataset from real
corrections, written into files inside the repository. That would have put *"pharmacy
340"* and *"loan to Serhii 5000"* into a public git history.

## Decision

**The private evaluation set is a query, not a directory.** Input and correct answer are
already stored — `messages` holds what arrived, `expenses` holds the truth after
corrections, `corrections` marks which rows the model got wrong. The runner reads them
from Postgres when it runs on the VPS.

`evals/golden/` holds only hand-written synthetic cases, which are committed. They must
run on a fresh clone with no database, which is exactly why they are files.

**Nothing binary is archived.** Voice notes and photos are referenced by Telegram
`file_id` and a stored transcript, and are not kept on disk. Consequently the private
evaluation set covers text only.

Also excluded from the repository: database dumps, screenshots with real amounts, and
Telegram user IDs.

## Rationale

The database is already the single source of truth; copying subsets of it into files
creates a second, staler copy with a worse privacy profile and no benefit.

Not archiving media is a deliberate trade. Telegram `file_id` is not durable — retention
is not guaranteed and the identifier is tied to the bot token — so replaying old voice
notes against a new model will not be possible. Storing a household's voice recordings on
a rented server to enable that is a poor exchange while text carries most of the value.
Revisit at Stage 3 if voice accuracy turns out to be the weak point.

A public repository is scanned by bots within minutes of a push, which is also why the
`gitleaks` pre-commit hook is mandatory rather than optional: `.env` protects one file,
the hook protects every other one from a key pasted "just to test" into code, a test, a
README or a compose file.

## Consequences

- The public dataset must be written by hand and kept genuinely awkward, or the published
  numbers mean nothing.
- Voice and photo evaluation relies on a small number of samples recorded deliberately
  for the purpose, not on production traffic.
- Anyone cloning the repository creates their own bot, database and keys. That is the
  intended level of reuse.

## Rejected

**Generating `evals/golden/private/` files from production** — a second copy of data that
is already in Postgres, and one `.gitignore` mistake away from being public. See
[ADR-0016](0016-narrow-exception-for-owner-named-voice-samples.md) for the one narrow,
owner-invoked exception carved out of this: explicitly named messages, pulled to a path
outside the repository, never a bulk export and never inside the tree.
**Private repository** — removes the whole concern, forfeits the portfolio value.
**Excluding orchestration files from the push** — would hide exactly the process
documentation needed to resume after a long pause.
