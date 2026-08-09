# ADR-0003 — Single-step extraction, not an agent loop

**Date:** 2026-08-09 · **Status:** accepted

## Context

The model could be given tools (`save_expense`, `get_report`) and allowed to decide when
to call them — the standard agent pattern. Alternatively it can be treated as a pure
function: input in, structured document out, with application code performing all side
effects.

## Decision

A single call with a fixed output schema. **The model holds no tools, runs no loop, and
keeps no state.** Application code writes to the database.

## Rationale

- Tool definitions cost tokens on every request.
- An agent may call a tool twice, not at all, or with nonsense arguments. The number of
  calls becomes unpredictable, and therefore so does the cost.
- The task genuinely has a fixed shape: one message in, a list of expenses out. Nothing
  about it requires the model to plan.
- A model with no tools cannot do anything worse than fill in the form incorrectly —
  which is the strongest available defence against injected instructions in a receipt
  photo.

Most production systems with an LLM inside contain no agent at all; they contain
transformations like this one, and are more reliable for it.

## Consequences

- Adding capability means writing code, not adding a tool. Accepted at this size.
- Off-topic input needs no instruction to refuse: the schema has no field for free text,
  so anything unrelated can only produce `expenses: []`.

## Rejected

**Tool-using agent** — extends naturally and would be useful experience, but costs more,
runs slower, and has undefined cost per message. Revisit at Stage 7, where free-form
questions genuinely require variable steps.
