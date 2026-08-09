# Vision

## The problem

Two people spend money dozens of times a day. Every tool for tracking that asks them to
stop, open an app, pick a category from a dropdown and type a number. So nobody does it,
and at the end of the month nobody knows where the money went.

The cheapest possible act of recording an expense is saying it out loud, the way you
would tell your partner: *"bread 50, taxi 200"*. Everything else is the software's
problem.

## What finbot is

A Telegram group with a bot in it. You write or speak into the group, or photograph a
receipt. The bot files it and replies with what it understood. If it got something
wrong, one tap fixes it.

That is the entire product.

## Who it is for

Two people — a household. Not a team, not a company, not other users. This constraint
is load-bearing: it removes authentication, permissions, onboarding, billing, and
multi-tenancy from the design entirely.

## Principles

**Capture must cost nothing.** If recording an expense takes more effort than not
recording it, the ledger becomes incomplete and therefore worthless. Every design
decision is measured against this first.

**Wrong data is worse than missing data.** A model that quietly writes 250 instead of
200 corrupts a year of reports. Hence the confirmation step: the correction happens
while the memory is two seconds old, not at the end of the month.

**The model transforms; the code decides.** The language model turns messy human input
into a structured document and does nothing else. It holds no tools, takes no actions,
and cannot reach the database. Every side effect belongs to application code.

**Never guess where you can compute.** Aggregation, totals and reports are SQL. A model
is used only where the input is genuinely unstructured.

**Corrections are data.** Every tap on ✏️ is a labelled example of the model being
wrong, with the right answer attached. The system is built so that using it produces
its own evaluation dataset.

## Deliberately out of scope

- Other users, sharing, or accounts beyond the two people in the group
- Budgets, limits, alerts, and financial advice
- Bank or card integrations
- Income, savings, investments, debt tracking
- Anything resembling accounting

If it is not "record what was spent, then show me what was spent", it does not belong
here.

## The second purpose

This project is also a deliberate exercise in applied LLM engineering. The interesting
work is not the CRUD — it is model routing across modalities, structured output under a
schema, prompt versioning, an evaluation harness on real data, cost accounting per
unit of work, and failure handling that distinguishes a dead provider from a
hallucinated number.

Where the two purposes conflict, the household wins: this is a tool two people rely on,
not a laboratory.

## What success looks like

A month in which neither person opens a spreadsheet, and the month-end report is
believed without checking.
