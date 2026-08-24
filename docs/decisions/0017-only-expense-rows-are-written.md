# ADR-0017 — Only `expense` rows are written; the other four kinds are shown, not stored

**Date:** 2026-08-24 · **Status:** accepted
**Related:** [ADR-0006](0006-separate-provenance-tables.md) (`extractions` already holds what the
model made of an input, verbatim, per attempt),
[ADR-0013](0013-messages-table-is-the-inbox.md) §2 (a second table holding the same rows creates
two answers to one question), [ADR-0005](0005-controlled-category-taxonomy.md) (the taxonomy a
written row lands in), [ADR-0014](0014-structured-output-and-the-evals-split.md) §3 (the strict
`enum` that makes a classification decodable instead of merely requested),
[ADR-0018](0018-model-reads-the-header-code-owns-the-calendar.md) (the other half of the same
row-by-row write decision), [ADR-0019](0019-bank-eval-labels-are-as-private-as-its-pixels.md)
(the eval whose primary metric is this decision's failure mode).
Plan: `docs/plans/stage-2_5-bank-screenshots.md`, Approach A.
Doc section: `docs/vision.md` → *Deliberately out of scope*.

## Context

Text and voice are self-selecting inputs. A person says *"bread 50, taxi 200"* because they spent
that money; every utterance is an expense by construction, and the model's whole job is parsing.

A bank-app screenshot is not that. It is a machine-generated list of everything that touched an
account, and the finding that shaped this stage is that **most negative rows in a real Privat24
feed are not expenses.** A savings jar fires several times a day for kopecks. `Округлення
залишку` does the same. A transfer to one's own card and a transfer to someone else's card are
both outflows, and neither is money spent. The interesting rows — a shop, a taxi, a subscription
— are a minority of the list.

So the problem this modality poses is **classification, not extraction**. Reading `320.50` off a
screenshot is the easy part; deciding whether that number belongs in the ledger at all is the
part that can corrupt a year of reports. Every design question below follows from that, including
the one the model choice turns on (ADR-0019 §5).

The stage plan's own reality check names the tension this had to resolve: `docs/vision.md`
excludes "bank or card integrations" and "income, savings, investments". A screenshot is not an
integration — no API, no token, no account link, no sync; it is a capture modality for the same
manual act. The second exclusion is the binding one, and it is what makes this decision the only
reading of Truth available.

## Decision

### 1. Five wire kinds, one write path

`BankRowKind` names `expense`, `income`, `savings`, `own_transfer`, `transfer_out`, and
`bank_json_schema`'s `kind` enum is exactly those five — ADR-0014 §3's mechanism reused, so a
sixth classification cannot be decoded rather than being politely discouraged in a prompt.

`bank.plan_writes` turns **only** `expense` rows into `ExpenseDraft`s. The other four are counted
individually in `BankPlan.skipped_by_kind` and named in the reply. Nothing new is stored for them,
anywhere.

### 2. No fourth table, because the rows are already recorded

The obvious alternative is a `bank_rows` table holding every row the model read, so that "how much
went into the jar this month" is direct SQL. It is rejected on the ground ADR-0006 and ADR-0013 §2
both already stand on.

`extractions.raw_response` stores the model's whole document verbatim, one row per attempt
(`CLAUDE.md` rule 6). Every skipped row — its kind, its amount, its merchant string, its header —
is therefore already in Postgres, in the one table ADR-0006 exists to make trustworthy. A
`bank_rows` table would be a fourth copy of data already recorded, with a second write path, a
second migration and a second thing to truncate, and it would create two answers to "what did the
model see" in a project whose evaluation dataset is exactly that question.

It would also start tracking income and savings, which `docs/vision.md` excludes **by name**. A
table nothing reads is cheap to add and expensive to keep honest; a table nothing reads that also
contradicts the vision document is not a trade at all.

### 3. No `kind` column on `expenses`

The other obvious alternative — write every row to `expenses` with a `kind` column and exclude the
non-expense kinds from reports — is the one that fails catastrophically rather than merely
awkwardly. Every report and every future query would have to remember the predicate, forever, and
one forgotten `WHERE` counts a five-figure transfer as spending. That is `docs/vision.md`'s "wrong data
is worse than missing data" with an amplifier attached: the error is silent, plausible, and
appears in a total the household is supposed to believe without checking.

`expenses` therefore keeps meaning exactly what it meant before this stage. Every row in it is
money spent. No predicate, no exception, no column to remember.

### 4. A sixth, domain-only kind: `UNCLASSIFIED`

`BankRowKind` has one value the wire enum cannot produce. `BankRow`'s `mode="before"` validator
coerces any unrecognised `kind` to `UNCLASSIFIED` and logs a WARNING, rather than raising.

Strict mode makes an out-of-enum value near-impossible, so this exists for the residue: a repaired
response, a hand-edited one, a future looser schema. In that residue, filing one row as
unclassified — counted, reported, written nowhere — beats spending a repair call and risking a
whole screenshot. It is the same shape and the same reasoning as `ExpenseDraft`'s coercion of an
unknown category slug to `other` (ADR-0014 §3), twenty lines away in the same file.

`UNCLASSIFIED` gets its own counter, separate from `skipped_by_kind`. The four skipped kinds are
choices the model is entitled to make; an unclassified row is a failure of the contract, and
lumping the two together would hide it.

### 5. Visibility is a *reply* requirement, not a storage one

Because nothing is stored, the reply carries the whole burden of the household knowing what
happened. `render_bank_note` therefore names every skip reason with **its own count** —
`Пропустив: скарбничка 2, переказ собі 1, …`, `Обрізано на краю`, `Вже було`, `Не зрозумів дату`,
`Не розібрав суму`, `Не визначив тип` — and omits only the zero-valued lines. A row is attributed
to exactly one reason, because `plan_writes` checks in a fixed order and `continue`s at the first
one that excludes it.

The note is a separate message from the confirmation, and nothing ever edits it (Approach D2). A
summary inside the button-bearing message would be destroyed by `_rerender_group` on the first
✏️/🗑 tap, which is what makes "shown, not stored" survivable at all.

## Rationale

Provenance is already satisfied. ADR-0006's three tables answer "what arrived", "what the model
made of it" and "what is true", and a bank feed's non-expense rows are fully answered by the
second. Adding a fourth table would not add information; it would add a second, staler place to
look for information that is already recorded — which is the property ADR-0006 exists to protect,
not a gap in it.

What is genuinely new in this modality is not storage but a decision per row, and the asymmetry of
getting it wrong. A missed expense is a nuisance the household notices on the next `/day` and
retypes in five seconds. A savings jar written as spending is a wrong number in a believed report,
and nothing later surfaces it. Every choice above pushes the cost of uncertainty onto the side
that under-counts and reports.

## Consequences

- **"How much went into the jar this month" is a JSONB query over `extractions.raw_response`, not
  a report.** Answerable, deliberately not convenient, and consistent with a product whose vision
  document says savings are out of scope.
- **`expenses` needs no predicate anywhere.** Existing reports, `/day`, and every future query
  keep working unchanged, and a new one cannot get this wrong by omission.
- **The reply is the only place a skipped row is ever visible**, so a regression in
  `render_bank_note` is a data-visibility bug, not a cosmetic one. `tests/unit/test_render_bank.py`
  covers every skip reason at once, the zero-written case, and the >5-collision cap.
- **The four skipped kinds are only as good as the prompt's rules.** A cash withdrawal is
  `own_transfer` ("the money is not spent yet") and a refund is `income`; neither appears in any
  observed sample, so both are reasoned rather than measured — and both err toward *not* writing.
- **The expensive direction is measured, not assumed.** ADR-0019's `no_false_expense` is exactly
  this ADR's failure mode turned into the gate the vision model is chosen on.
- **If income or savings tracking is ever wanted, that is a new ADR superseding this one and an
  edit to `docs/vision.md`** — not a column added quietly to `expenses`.
- **Stage 4 inherits the shape.** A receipt is also a list of rows the code must decide about; the
  `is_transaction_feed` boolean that guards this prompt against a photographed receipt is the seam
  that stage grows from.

## Rejected

**A `bank_rows` table holding every row** (Approach A2) — direct SQL over transfers, at the price
of a fourth copy of data already in `extractions.raw_response`, a second answer to "what did the
model see", and a schema that starts tracking the two things `docs/vision.md` excludes by name.
The migration and the backup live forever; nothing reads the table.

**A `kind` column on `expenses`, filtered in reports** (Approach A3) — one table, and every
report's correctness becomes a thing to remember. One forgotten `WHERE` counts a transfer as
spending, silently, in a total presented as complete.

**Raising on an unrecognised `kind`** — the strict-mode-purist reading, and it trades one
unclassifiable row for a repair call and, past `max_extraction_attempts`, a whole screenshot. The
coercion keeps the other rows and makes the anomaly loud in the log and in the reply.

**Reporting one lump "skipped: 5"** — cheaper to render and useless to act on. A savings jar
skipped and a row whose date could not be resolved call for completely different responses from
the household, and only the second is a reason to retype anything.
