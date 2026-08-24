# ADR-0019 — A bank-feed eval's labels are as private as its pixels

**Date:** 2026-08-24 · **Status:** accepted
**Amends:** [ADR-0009](0009-public-repo-private-eval-data.md) — its rule that `evals/golden/`
holds committed synthetic cases does not survive contact with a modality whose *labels* are the
private data. For this modality the whole case file leaves the repository.
**Extends:** [ADR-0016](0016-narrow-exception-for-owner-named-voice-samples.md) — its guard, until
now enforced on `pull_voice_samples.py`'s `--out`, now also covers `--cases` and `--images-dir`,
as **one** implementation rather than two copies.
**Related:** [ADR-0012](0012-stage-0-verification-strategy.md) (its Stage-1 amendment permits
fixture refresh from synthetic cases only), [ADR-0014](0014-structured-output-and-the-evals-split.md)
§7 (`pytest` proves the plumbing, `evals/` measures the model),
[ADR-0017](0017-only-expense-rows-are-written.md) (the failure mode `no_false_expense` measures),
[ADR-0018](0018-model-reads-the-header-code-owns-the-calendar.md) (the resolver `date_exact` runs
through). Plan: `docs/plans/stage-2_5-bank-screenshots.md`, Approach F.
Doc section: `evals/README.md` and `evals/golden/bank/README.md`.

## Context

Stage 2 set a pattern that looked reusable: `evals/golden/voice_v1.jsonl` is committed, the audio
behind it is not, and the split is enforced by `.gitignore` plus ADR-0016's guard on the puller.
Applying that to bank screenshots — commit `bank_v1.jsonl`, ignore the images — is the obvious
move, and it is disqualified.

**The voice set's committed labels were safe for a reason that does not transfer.** Its `expected`
values came from an agreed script the owner deliberately spoke: synthetic content, private audio.
The label was invented first and the recording made to match it, so publishing the label publishes
nothing.

A bank feed inverts that. Its labels are real amounts, real merchants and a third party's name,
transcribed off a real household's account. The pixels being git-ignored does not help at all —
the labels are the same data in another container, and a `.jsonl` of amounts is easier to read than
a screenshot. `CLAUDE.md` rule 4 forbids "screenshots with real amounts"; a transcription of one is
not different, and ADR-0009's own Rejected section already named the hazard class ("one
`.gitignore` mistake away from being public").

There is a second hole, in the opposite direction. ADR-0012's Stage-1 amendment says
`python -m evals.run --save-raw DIR` refreshes `tests/fixtures/openrouter/` **from synthetic golden
cases only**. For this modality there are no synthetic cases, so that flag would write real bank
response bodies — rows, merchants, amounts — into a committed fixture directory.

## Decision

### 1. The whole case file lives outside the repository

Not the labels inside the tree with the images outside it. Both, out.

`--cases` and `--images-dir` are **required for `--modality bank`, have no defaults**, and are
refused when they resolve inside the repository. `load_bank_golden_cases` runs both through
`evals.paths.ensure_outside_repo` before it opens anything, so the refusal belongs to the loader
rather than to the CLI: any future caller inherits it.

No default is as load-bearing as the guard. A default is a path a hurried invocation falls back to
silently; F4's point is that a fresh clone has no bank golden set to fall back *to*.

### 2. One implementation of ADR-0016's guard, not two

The check itself is ADR-0016's, unchanged in substance: resolve the given path — expanding `~`,
collapsing `..`, following symlinks — and compare it against the resolved repository root, never
trusting the caller's `.gitignore` discipline or a string prefix. That single comparison refuses the
repository root, any subdirectory of it, a relative path that resolves inside it, and a symlink
whose *target* is inside it.

What changes is where it lives. It was a private `_REPO_ROOT`/`_validate_out_dir` pair inside
`pull_voice_samples.py`; it is now `evals/paths.py` (`REPO_ROOT`, `ensure_outside_repo`,
`RepoPathError`), and the puller imports it. ADR-0016's Consequences already required that any
future variant of that tool keep both guards, and named the guard — not the script's flags — as the
invariant to preserve. Two copies of an invariant is one copy that can drift. `flag` is a parameter
so the error message names `--out`, `--cases` or `--images-dir` precisely.

`tests/unit/test_evals_paths.py` tests the guard itself, table-driven over every path shape it must
refuse; `tests/unit/test_pull_voice_samples.py` staying green across the refactor is the evidence
that the guard was moved rather than weakened.

### 3. `--modality bank` refuses `--save-raw`

Refused outright, with one clear line, **before** `load_eval_settings` and before any socket is
considered — so the refusal does not depend on having an API key, and cannot be reached only by a
run that was going to fail anyway.

The reason is ADR-0012's, not a new one: fixture refresh is permitted from synthetic golden cases
only, and **there is no such thing as a synthetic bank-feed screenshot**. Every case in this
modality is a real household screenshot, so `--save-raw` here is not a convenience with a caveat;
it is a pipe from private data into a committed directory.

The three committed bank fixtures — `bank_feed_ok.json`, `bank_multi_day.json`,
`bank_not_a_feed.json` — are therefore **hand-written from the documented response schema with
invented merchants and amounts**, exactly as ADR-0012's amendment describes the original text
fixtures. They are contracts with a provider's envelope shape, not samples of a household's month.

### 4. `anchor_date` is absolute and per case

This is the one place this modality must diverge from the committed sets' run-date-relative rule,
and the divergence is forced by the medium. A screenshot's absolute headers — `Сб, 22 серпня` — are
baked into the pixels and will still say that next year. An offset relative to the *run* date would
drift a day every day, so the eval would start failing on its own calendar rather than on the
model. An offset relative to a **per-case absolute anchor** is stable forever and exercises exactly
what production does, since `anchor_date` is precisely what `runner.py` computes from
`message.created_at` (ADR-0018 §4).

Consequently `--today` does not apply to `--modality bank`, and `run_bank_case` takes no `today`
parameter at all. `date_exact` resolves each row's header through `bank_dates.resolve` against the
case's own anchor — the production resolver itself, never a second copy (ADR-0014 §7).

### 5. `no_false_expense`, and why it is asymmetric

The metric the model choice turns on:

> Every amount `bank.plan_writes` would actually **write** must appear, with multiplicity, among
> the amounts this case labelled `expense` and fully visible. `Counter.__le__` is multiset
> inclusion, so `written <= allowed`.

Two properties make it the gate rather than one column among ten.

**It counts one direction only** — money recorded that was never spent. A model that *misses* an
expense scores a pass here: `written` is then a strict subset. That is deliberate, and it is
ADR-0017's asymmetry restated as arithmetic. A missed expense is retyped in five seconds; a savings
jar written as spending is a wrong number in a report the household is supposed to believe without
checking, and nothing later surfaces it. A mislabelled category never costs that much.

**It stays scoreable when the model miscounts rows.** Every positional metric — `kind_exact`,
`category_exact`, `date_exact`, `dropped_exact`, `amount_exact` — is gated on `count_exact`,
because zipping mismatched lists compares unrelated rows. So they all go blind at exactly the
moment something went wrong. `no_false_expense` never zips: it compares two multisets of amounts,
and it is computed through `plan_writes` itself, so it measures the code's actual write decision
rather than a restatement of its rules.

Hence Gate 1 of the pre-registered model choice: `no_false_expense` must be **perfect**, and a
model that fails it is disqualified at any price.

### 6. The two recurring rules are tests, not prose

Both belong in the gate, following ADR-0012 §5's rule about rules — and its corollary, that a
check which only ever sees passing input is not known to work:

- **A golden-set loader for a private modality refuses a path inside the repository.**
  `tests/unit/test_evals_bank.py` drives `load_bank_golden_cases` with `--cases` inside the
  repository and with `--images-dir` inside it, each refused, on top of the guard's own
  table-driven test in `test_evals_paths.py`; plus an identity pin that the loader's data-URL
  builder *is* `finbot.adapters.telegram.images.to_data_url`, the same shape as the existing pin on
  `convert_to_mp3`.
- **`--save-raw` is only ever pointed at synthetic cases.**
  `tests/unit/test_evals_run.py` asserts `--modality bank --save-raw` exits non-zero and opens no
  socket, with no API key configured.

## Rationale

ADR-0009's rule was written around a container, and this modality shows the rule was really about
content. "Nothing binary is archived" and "`evals/golden/` holds synthetic cases" happened to
coincide for text and voice, because in both the file held something invented and the private part
was the medium. Here the private part *is* the file. Keeping the letter of ADR-0009 while
committing a list of a household's real amounts would satisfy the rule and defeat its purpose.

The guard is mechanical for the same reason ADR-0014 §5 makes `parse_float=Decimal` an AST test and
ADR-0012 §5 makes the layering rule an AST walk. "Remember not to point `--cases` at the repo" is
not a rule; it is a hope with a command line attached. A resolved-path comparison inside the loader
is a rule, and it is the same one ADR-0016 already argued for — which is why extracting it was the
right shape of change and adding a second copy would not have been.

`no_false_expense` exists because a model choice made on an average of ten metrics is a model choice
made on nothing in particular. This stage has one failure mode that corrupts a believed report, and
the gate is pre-registered against it, before any candidate was run.

## Consequences

- **A fresh clone cannot run this eval at all.** One step beyond voice, where the cases are at
  least visible and only the audio is missing. Anyone reproducing the model choice must build their
  own case set; `evals/golden/bank/README.md` documents the format, and is the only thing committed
  under that directory.
- **The published numbers are reproducible in shape, not in value.** The journal records counts,
  costs and latencies only — never a merchant name and never an amount.
- **The bank fixtures cannot be refreshed mechanically, and will not be.** ADR-0014's consequence
  that "the recorded fixtures are the contract with a provider that can change under us" still
  holds; for this modality the refresh path is a human hand-writing the new envelope shape.
- **`--today` is meaningless for `--modality bank`**, and a case file missing `anchor_date` fails at
  load time, eagerly, before the first billed call — the same discipline as a missing image or an
  unsniffable format.
- **`evals/paths.py` is now shared, so a change to it changes two tools.** That is the point, and
  it is why both `tests/unit/test_evals_paths.py` and `tests/unit/test_pull_voice_samples.py` have
  to stay green.
- **Stage 4 inherits all of this.** A photographed receipt is the same class of data as a bank
  feed, so its eval starts from `--cases`/`--images-dir` outside the repository, hand-written
  fixtures, and no `--save-raw`.
- **ADR-0009 now has two amendments and no repeal.** The private evaluation set is still a query
  against Postgres, nothing binary is still committed, and the exceptions are still narrow,
  owner-invoked and enforced by a resolved-path check rather than by `.gitignore`.

## Rejected

**Commit `bank_v1.jsonl`, git-ignore only the images** (Approach F1, the voice arrangement) —
disqualified, not merely worse: it publishes real amounts, real merchants and a named third party
into a public git history, which is the exact outcome ADR-0009 exists to prevent. The pixels being
ignored is irrelevant when the labels carry the same facts in plain text.

**Commit anonymised labels** (F2) — the amounts *are* the private data, and `amount_exact` needs
the real numbers to mean anything. An anonymised label set measures a different screenshot.

**Hand-draw synthetic screenshots and commit both** (F3) — it would measure a synthetic layout,
when the entire value of the spike was that these are real feeds with real chrome, real truncation
and real savings-jar noise. Worth doing later as a public smoke case; it is not this eval.

**Keep `--save-raw` for bank and rely on the owner not using it** — the flag exists, the directory
it writes to is committed, and nothing would fail. Refusing it costs one branch and removes the
possibility.

**A second private copy of the repo-path guard inside `evals/scoring.py`** — the smaller diff, and
it creates two implementations of one invariant that must never drift apart. ADR-0016 already
named the guard as the thing to preserve, not the script's flags.

**Run-date-relative offsets for bank cases, for consistency with the committed sets** — consistent
and wrong: a screenshot's absolute headers do not move, so the expectations would drift a day every
day and the eval would report a model regression that is really a calendar.
