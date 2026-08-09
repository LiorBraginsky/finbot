# ADR-0005 — Controlled category taxonomy with a human gate

**Date:** 2026-08-09 · **Status:** accepted

## Context

The desired behaviour was: let the model recognise categories and add a new one whenever
nothing fits. The alternative is a fixed list the model must choose from.

## Decision

A fixed list of roughly twelve categories, supplied in the prompt, from which the model
**must** choose. When nothing fits, it returns `proposed_category` with a reason instead
of creating anything. The confirmation message then offers *Create "Pets"?* or *Put in
"Other"*, after a duplicate check against existing categories.

Rejected proposals are stored with `status = 'rejected'` so the same one is not offered
again.

## Rationale

A model has no memory of the labels it created yesterday — it sees only the current
request. Given freedom, it produces *Food*, *Groceries*, *Meals* and *Supermarket* as
four distinct categories within weeks, and every chart built on them is noise.

The taxonomy still evolves, but through the two humans who will read the reports. This
is the same human-in-the-loop pattern already chosen for amounts, applied one level up.

## Consequences

- One extra decision occasionally surfaces in the confirmation flow.
- Category proposals are deferred to Stage 5 on purpose: real data first shows which
  categories are actually missing. Discovering that twelve were enough is the best
  outcome.

## Rejected

**Free-form categories with monthly consolidation** — an agent proposing merges every
month. Zero friction at capture time, but a month of dirty data, and merging
retroactively invalidates reports already shown.
