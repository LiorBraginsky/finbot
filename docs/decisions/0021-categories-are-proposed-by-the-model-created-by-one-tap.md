# ADR-0021 — Categories are proposed by the model and created by one tap; the model's enum never grows

**Date:** 2026-08-26 · **Status:** accepted
**Amends:** [ADR-0005](0005-controlled-category-taxonomy.md), whose human gate stands —
this record only reduces it from a code change to a button.

## Context

Thirteen categories were chosen before any real data existed. Real data disagrees: on
the private bank set, `category_exact` sits at 6/15, and every miss is a merchant with no
honest slug among the thirteen — an education service, a payment processor, a marketplace.
All of them land in `other`, which makes `other` the largest line in a report and the
report itself useless for exactly the spending the household would want to see.

ADR-0005 chose a controlled taxonomy with a human gate, and that reasoning holds: a model
free to invent category names produces five spellings of the same idea within a month.
But the gate as implemented was *a change to `catalog.py`, a migration and a deploy*,
which in practice means the taxonomy never changes at all.

## Decision

**The model proposes; it never chooses.** A new nullable wire field,
`suggested_category`, carries a Ukrainian label — «Освіта» — and only when the row was
already filed under `other`. The `category` enum the model picks from is unchanged and
**never grows**.

That last point is the load-bearing one, and it is what distinguishes this from the
obvious design. A prompt whose category list is read from the database at request time
would make the prompt, the strict schema's `enum`, and therefore every measurement, a
function of database state. `evals/` would stop being reproducible — a run today and a run
next month would not be comparing the same thing — and the enum would grow without bound.
Instead:

- The prompt and the schema enum are built from `catalog.CATALOG`, a constant.
- The proposal is free text, resolved **in code** by `slugify_category` (a deterministic
  transliteration) against the `categories` table.
- Reuse is therefore automatic and needs no prompt change: the second charge of a kind
  the owner has already approved is filed under it directly, because the same label
  slugifies to the same existing slug.

**One tap creates it.** A proposal inserts a `categories` row with `status='suggested'`,
invisible to `by_slug`, to the picker and to reports. The expense stays under `other` and
records `expenses.suggested_category_id`. The ✏️ picker then shows one extra row,
`➕ Створити «Освіта»`, packing the **same `SetCategory` callback** as every other
category button; the handler flips a `suggested` category to `active` on the way through.
Approving and filing are one tap, one transaction, one code path — no fourth
`CallbackData` type, and no way to end up with a created category whose expense is still
under `other`.

**Category presentation becomes data.** `categories.label` is a new column, and the
label/emoji travel on `ExpenseView`, `ReportLine` and `CategoryView`.
`render.CATEGORY_LABELS` and `render._EMOJI_BY_SLUG` are deleted: a constant map keyed on
slug cannot name a category that exists only in the database, and every lookup through it
was a latent `KeyError` on the first owner-created row.

**The voice prompt is deliberately left out.** See Consequences.

## Consequences

- `other` stops being a dead end. The gate that ADR-0005 wanted is still there — nothing
  is ever filed under a category nobody approved — but it costs a tap instead of a deploy.
- **A proposal on a row the model already categorised is ignored**, in code, not merely
  forbidden by the prompt: honouring it would let the model quietly reclassify a row it
  had already filed with confidence.
- **A proposal that slugifies onto a seeded slug creates nothing.** It is a rewording of a
  category the model could have picked outright.
- The picker now lists every *active* category, so it grows as the taxonomy does — and
  already shows fifteen rather than thirteen, since ADR-0020's two code-assigned
  categories are active rows like any other.
- `slugify_category` must stay deterministic forever. It is the identity of a category
  across proposals; a change to its transliteration table would orphan every category
  created before the change. That is why it has its own test file asserting determinism
  and totality rather than only pretty output.
- Prompt and schema changes on two modalities, so `extract_text.v2` and
  `extract_bank.v3` — and both channels re-measured. Text: 22/22 on every metric,
  unchanged, at $0.000381 per message against $0.000276 before (a longer prompt costs
  38% more on a message this small — $0.0001). Bank: `no_false_write` 15/15,
  `written_count_exact` 15/15, unchanged.
- **Voice keeps `extract_voice.v1`.** Adding the rule there cost `transcript_ok` 10/10 →
  8/10 at n=10, with the model rendering a Russian word in Ukrainian, while all four
  extraction metrics stayed 10/10. Reordering the rule to last and restating the
  transcript rule after it recovered 9/10, not 10/10. The wire schema still carries the
  field on all three modalities — strict mode requires every property in `required` — so
  the model simply returns `null` for voice. A voice note therefore proposes no
  categories, which is an accepted loss: voice is the least-used channel and the `other`
  pile accumulates from screenshots.
- `tests/conftest.py` had to delete owner-created categories before deleting users. Its
  own comment said the value-based FK check was safe "since Stage 1 never sets
  `created_by` (Stage 5 does)" — this decision sets it, the assumption expired, and the
  fixture failed loudly. A comment that states its own precondition is why that took a
  minute to diagnose instead of an hour.

## Rejected

**Read the prompt's category list from the database.** The obvious reading of "categories
on the fly", and the reason it is wrong is not obvious: it makes the prompt, the schema
enum and therefore every eval number a function of database state, so no measurement is
comparable to any other, and the enum grows without bound as proposals accumulate.
Resolving proposals in code gets the same behaviour — including automatic reuse — with a
frozen prompt.

**Let the model return a slug as well as a label.** Two strings that must agree,
produced by one call, which invites them to disagree; and the slug is an identifier no
human ever reads. Transliterating in code is deterministic and testable.

**Create the category immediately, with no tap.** Simpler, and it deletes ADR-0005's gate
rather than reducing it: one hallucinated proposal becomes a permanent category, and
nothing distinguishes "the household decided this" from "the model had a thought".

**A separate `ApproveCategory` callback.** Explicit, and it splits one user intention into
two operations that can half-fail — a created category whose expense is still `other`, or
the reverse. Reusing `SetCategory` makes the two atomic by construction.

**Keep the labels in `render.CATEGORY_LABELS` with a `.get(slug, slug)` fallback.** A
smaller diff, and it would work: an owner-created category would render as `osvita`
instead of «Освіта». Rejected because the fallback would be the *normal* path for exactly
the categories this decision adds, and the ugly identifier would show up in every report.
