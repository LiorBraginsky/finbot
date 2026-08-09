# ADR-0006 — Keep input, extraction and truth in separate tables

**Date:** 2026-08-09 · **Status:** accepted

## Context

The simplest schema stores expenses only. The model's raw output, the model used and the
original message could all be discarded once a valid row exists.

## Decision

Three tables with three lifecycles:

- **`messages`** — what arrived: raw text or transcript, `file_id`, modality, sender.
- **`extractions`** — what the model made of it: `model_id`, `prompt_version`, `attempt`,
  `status`, raw JSON response, `cost_usd`, `latency_ms`. One row per attempt, including
  failed ones.
- **`expenses`** — the truth after corrections.

Plus **`corrections`**, holding the before/after snapshot of every manual fix.

## Rationale

- Changing a model or a prompt is otherwise an unmeasurable act. With the raw inputs
  kept, old messages can be replayed through a new model and compared against the known
  correct answer — a regression test on real data, at no labelling cost.
- Every ✏️ produces a labelled example of the model being wrong with the right answer
  attached. Without `corrections`, that signal is destroyed on write.
- Per-call cost and latency recorded next to the business result answers "what does one
  recorded receipt cost" precisely, per model.

## Consequences

- More storage and more write paths. Irrelevant at this volume.
- Recording failed attempts is deliberate: how often a model needs a repair loop is a
  quality metric, not noise.

## Rejected

**Expenses only** — smaller, and blinds the project to the one thing it is supposed to
measure.
**A `confidence` field from the model** — self-reported confidence is poorly calibrated;
models are confidently wrong. The honest proxy is the share of rows corrected by hand.
