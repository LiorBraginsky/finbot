# ADR-0016 — A narrow, owner-invoked exception to "nothing binary is archived": named voice messages pulled to a path outside the repository

**Date:** 2026-08-10 · **Status:** accepted
**Amends:** [ADR-0009](0009-public-repo-private-eval-data.md), which states nothing binary
is archived, that voice evaluation "relies on a small number of samples recorded
deliberately for the purpose, not on production traffic," and rejects — in its own
Rejected section — "generating `evals/golden/private/` files from production" as "a
second copy of data that is already in Postgres, and one `.gitignore` mistake away from
being public."

## Context

Stage 2's `evals/golden/voice_v1.jsonl` needs real recordings behind it. The owner
already has some: voice notes sent to the bot before this eval set existed. The first
version of `evals/pull_voice_samples.py` downloaded every `kind='voice'` row from
`messages` into `evals/golden/voice/` — a directory inside this repository's own tree,
kept out of git only by a `.gitignore` entry.

That is exactly the hazard ADR-0009's Rejected section already named, not a new one: a
script that bulk-populates a repo-tracked directory with real household audio is one
accidental `git add -f`, one edited `.gitignore` line, or one contributor unaware of the
ignore rule away from publishing it. That the directory happens to be ignored today is a
property of the current `.gitignore`, not of the script — nothing about the script itself
depended on that being true, or would have noticed if it stopped being true.

## Decision

A narrow exception, not a repeal. `evals/pull_voice_samples.py` may download **only
messages the owner names explicitly**, and **only to a directory outside this
repository**:

- `--message-ids` is required, with no default and no "every voice row" mode: a
  comma-separated list of specific `messages.id` values, chosen by the owner because they
  know what is in them.
- `--out` is required, with no default, and refused — by comparing the resolved path
  against the resolved repository root, not by trusting `.gitignore` — if it names this
  repository or anything under it.

Everything else ADR-0009 states remains in force: nothing binary is committed, the
private evaluation set is still a query against Postgres/`expenses`/`corrections`, never
files, and this script is run by hand, by the owner, one invocation at a time — never on
a schedule, never from CI.

## Rationale

The convenience the script exists for — turning voice notes already sent into
`evals/golden/voice_v1.jsonl` cases without re-recording them — needs neither bulk export
nor a repo-adjacent destination. It only needs some way to get specific bytes out of
Postgres and into a file the owner can point a media player at. Removing the two things
ADR-0009 actually warned about (an unbounded sweep, a path inside the tree) removes the
hazard while keeping the entire reason the script exists.

Requiring explicit ids is a legibility property as much as a safety one. A week from now,
this script cannot be run out of habit and quietly pull in a new real conversation nobody
meant to export: every invocation names, in the command line itself, exactly what it
downloaded.

## Consequences

- The owner looks up message ids before running this — e.g. `select id, created_at from
  messages where kind = 'voice' order by id desc` — one extra step, in exchange for
  making an accidental bulk export structurally impossible rather than merely
  discouraged.
- `evals/golden/voice/README.md` documents the two-step flow: find ids, run the script
  with `--out` pointed outside the repository, then move or rename the wanted files in by
  hand to match a case in `voice_v1.jsonl`.
- Any future variant of this tool — a "list candidate voice messages" companion command,
  for instance — must keep both guards: refuse a path inside the repository, and require
  explicit selection. This ADR's guard is the invariant to preserve, not a detail of the
  current script's flags.

## Rejected

**Keep the bulk `kind='voice'` sweep, but default `--out` to somewhere outside the
repository** — leaves the sweep itself in place. Any later call with `--message-ids`
omitted (there was no such flag before) or `--out` overridden back to the repo
reintroduces exactly the hazard ADR-0009 named. The two guards are load-bearing together;
neither alone is enough.

**Delete the script** — the convenience is real (it avoids re-recording notes already
sent), and the amendment above removes the actual hazard without losing it.
