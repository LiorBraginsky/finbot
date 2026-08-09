# ADR-0010 — An append-only journal instead of a state file

**Date:** 2026-08-09 · **Status:** accepted

## Context

The project is expected to pause for weeks and resume without losing the thread. The
common answer is a `STATE.md` describing where work stopped, updated at the end of each
session — optionally enforced by a stop hook.

## Decision

No state file. Instead:

- **`docs/journal.md`** — append-only, newest entry at the top, English, at most five
  lines per entry, written by whoever finished a unit of work.
- **`docs/roadmap.md`** — stage statuses, changed once per stage.

Current state is *derived* from those two, never stored separately.

## Rationale

A state file duplicates information and therefore drifts. It goes stale the moment
someone kills a session without updating it — and sessions do get killed. A stale state
file is worse than none, because it is believed.

An append-only log cannot lie: every entry was true when written. Its cost is that a
reader reconstructs the present from the last few entries, which is cheap when entries
are short and dated.

The journal also records what git cannot: intent, hypotheses, and things that were tried
and abandoned. *"Gemini handles mixed Ukrainian/Russian speech worse than a dedicated STT
model, reverted"* exists in no diff.

## Consequences

- The format must be enforced, or the file becomes unreadable within months. Hence the
  fixed four-line template and the ban on restating diffs.
- Resuming means reading the roadmap, then the top of the journal, then the ADRs.

## Rejected

**`STATE.md` plus a stop hook** — accurate only while the ritual holds, and the ritual
depends on sessions ending cleanly.
**Git log alone** — records what changed, never why it was attempted or why it was
dropped.
