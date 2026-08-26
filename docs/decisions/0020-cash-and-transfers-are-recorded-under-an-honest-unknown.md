# ADR-0020 — Cash withdrawals and outgoing transfers are recorded under an honest "we don't know"

**Date:** 2026-08-26 · **Status:** accepted
**Supersedes:** [ADR-0017](0017-only-expense-rows-are-written.md), which decided that only
`expense` rows are ever written. Two of its four skipped kinds are now written.

## Context

ADR-0017 wrote only `expense` rows and reported the other four kinds — `income`,
`savings`, `own_transfer`, `transfer_out` — without storing them. The reasoning was
double-counting: money that only *moved* is not money spent, and recording the move as
well as the eventual purchase counts it twice.

Two months of real screenshots showed the reasoning is right for two of those kinds and
wrong for the other two. The distinction is not "did the money move" but **will any later
row ever account for it**:

| kind | where the money went | will a later feed row show it? |
|---|---|---|
| `income` | in, not out | n/a |
| `savings` | the household's own jar | yes — when withdrawn and spent |
| `own_transfer` | the household's own other card | yes — that card's own feed |
| `transfer_out` | someone else's account | **no** |
| cash withdrawal | out of the banking system | **no** |

For the bottom two, "reported but not stored" means the money is silently absent from
every report. The owner's own framing: *«там нема інформації, хто зна куди воно
пішло»* — the absence of information is the reason to record it, not a reason to drop it.

A cash withdrawal was classified `own_transfer` under ADR-0017 ("the money is not spent
yet, only moved"). That reading fails as soon as the cash is spent, because no feed row
ever appears for a cash purchase — which is precisely the case that motivated this
record: a single purchase paid partly by card and partly with cash withdrawn minutes
earlier appeared in the ledger at only the card half of its true amount.

## Decision

**A sixth wire kind, `cash_withdrawal`, split out of `own_transfer`.** `Зняття готівки в
банкоматі` and its siblings are fixed bank labels, not free text, which is what makes
this a distinction the model can be asked to draw reliably.

**`cash_withdrawal` and `transfer_out` are written; `income`, `savings` and
`own_transfer` are still not.** `bank.SKIPPED_KINDS` and `bank.FORCED_CATEGORY` carry
each kind's reason in a comment, and a module-level assertion requires every
`BankRowKind` to be on exactly one side.

**Their category is assigned by the code, from the kind alone — never by the model.**
Two new categories, `cash` (💵 Готівка) and `transfers` (↔️ Перекази), live in
`catalog.DERIVED_CATALOG`: real `categories` rows, seeded by migration 0005, but
deliberately **absent from `CATALOG`** and therefore from the prompt and from the
schema's `category` enum. `catalog.MODEL_SLUGS` is what `BankRow.category` validates
against; `catalog.SLUGS` — the wider set — is what `ExpenseDraft.category` validates
against, because a draft is not always the model's own choice.

**Correction is the mechanism, not classification.** A cash row is filed under an honest
"we don't know", and one ✏️ tap moves it where it belongs once the owner remembers. The
category is wrong on purpose until then; the *amount and the date* are not.

## Consequences

- The reports total is now complete for money that leaves the account. It was previously
  understated by every cash withdrawal and every outgoing transfer.
- **A cash purchase logged by hand is now a genuine double-count risk**: the withdrawal
  is recorded, and typing «фокстрот 2472» records it again.
  `expenses_repo.manual_duplicate_candidates` already warns on exactly this shape
  (`⚠️ Можливий дубль`, ADR-0007's R7) — a guard that existed before it had anything to
  guard. It warns; it does not merge or suppress, and that stays the right behaviour:
  only the owner knows whether two rows of the same amount on the same day are one
  purchase or two.
- **`own_transfer` keeps a known hole.** Privat's `На картку` does not say whose card.
  A transfer to a household member's card is really a `transfer_out` and the model cannot
  see the difference. It stays skipped, and the reply's `Пропустив: переказ собі 1` line
  is what makes the omission visible rather than silent. Closing it needs an account
  model, not a better prompt.
- The prompt changed, so `extract_bank.v1` is frozen on disk and
  `PROMPT_VERSION_BANK` is `extract_bank.v2` — `extractions.prompt_version` keeps every
  pre-existing row traceable to the prompt that produced it.
- The prompt and the schema both changed, which invalidates the Stage 2.5 model
  measurement: `MODEL_VISION` must be re-measured on the private bank set before this
  ships, with `no_false_expense` re-defined — a `cash_withdrawal` row written as `cash`
  is now correct, and the metric must not count it as a false expense.

## Rejected

**Keep skipping both** — the ADR-0017 status quo. Leaves the report understated by
exactly the money whose destination is unknown, which is the money most worth seeing.

**Skip the withdrawal and rely on the owner logging cash purchases by hand** — no
double-count risk at all, and the cleanest model on paper. Rejected because it fails in
the direction that cannot be noticed: a forgotten cash purchase leaves no trace anywhere,
while a miscategorised row is visible in the next report. A wrong category lies about
*where*; a missing row lies about *how much*, and the amount is what this project exists
to get right.

**A `cash` vs `card` account model** — withdrawal as a transfer between two accounts,
cash purchases debiting the wallet, balances that must reconcile. Correct
double-entry bookkeeping, no double-count possible by construction, and it would show how
much cash should be in a pocket. Rejected as out of proportion: it requires *every* cash
purchase to be logged or the wallet balance drifts into noise, and this household does
not log reliably enough for a balance to mean anything. Revisit only if cash becomes a
large share of spending.

**Let the model choose `cash`/`transfers` as ordinary categories** — one fewer concept,
no `DERIVED_CATALOG` split. Rejected: a model free to file a supermarket purchase as
"cash" would corrupt the one signal these categories carry, which is *"the feed does not
say where this went"* — a property of the row's kind, never of its merchant.
