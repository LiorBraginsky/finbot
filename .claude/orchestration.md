# Orchestration

The substrate this project's agents read before doing anything. Written in the format
the agents in `.claude/agents/` expect: they look for `## Truth` and `## Gates` by name.

## Truth

Authoritative sources, in order. Where they disagree, the earlier one wins. Where a
claim contradicts them, they win.

1. **`CLAUDE.md`** — the six non-negotiable rules, layout, gates, git and journal policy.
2. **`docs/vision.md`** — why this exists, its principles, what is deliberately excluded.
3. **`docs/decisions/`** — ADRs. Accepted decisions are not reopened without a
   superseding ADR.
4. **`docs/specs/`** — frozen design documents. The current one is
   `2026-08-09-expense-capture-design.md`.
5. **`docs/roadmap.md`** — stages and their status. Defines what is in scope right now.
6. **`docs/journal.md`** — recent entries: where work stopped, what was tried, what was
   abandoned.

**There is no `lint-rules/` directory in this project.** The executable technical
standard is the `ruff` and `mypy` configuration in `pyproject.toml`, **plus the
rule-enforcing tests under `tests/`** — `tests/unit/test_layering.py` enforces
`CLAUDE.md` rule 3, and `tests/integration/test_schema_matches_models.py` enforces
migration/model agreement. Neither rule is expressible in ruff or mypy. Treat all of it
as binding, not advisory.

## Gates

A stage is not finished until all of these pass **and the output has been read**:

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

**`pytest` requires a running Docker daemon** — the integration suite starts a real
Postgres, and those tests **fail rather than skip** when Docker is absent. That is
deliberate (ADR-0012): a skipped database test on a branch that merges unattended is a
green gate proving nothing. If they fail for want of Docker, start Docker. **Do not add
`skipif`, and do not add a rerun-until-green plugin** — an intermittent gate trains the
habit that destroys it.

From Stage 3 onward, one more gate: any change to a prompt or a model requires
`python -m evals.run` before and after, with both results recorded in the journal entry.

"Should work" is not a result. "Type-checks" is not "works".

## Unit of work

**One stage from `docs/roadmap.md`.** Not one commit, not one file. Running the full
chain on trivial changes is overhead; running it per stage gives both discipline and a
clean point to stop for a month and come back.

**The roadmap is the decomposition.** Stages are already scoped, ordered and given
done-criteria. Do not re-decompose them; there is no parallelism to exploit here, and a
second breakdown would only drift from the first.

## How much process

Not every stage deserves the same ceremony.

| Scope | Flow |
|---|---|
| Smaller than a stage — typo, one-file fix, docs | Directly. No chain. |
| Mechanical stage with no open decisions (0, 1.5) | `worker` straight from the roadmap, then gates and a journal entry. No architect, no reviewer. |
| Stage with real design content (1, 2, 3, 4, 5) | Full chain. |

Two standing exceptions to the full chain:

- **`doc-curator` runs on demand, not by default.** Invoke it only when the architect
  flagged `## ADR worthy: yes`. When a stage produced no decision, its output is two
  lines — roadmap status and a journal entry — and the worker writes them.
- **`reviewer` is not skipped.** It is the cheapest feedback channel in the project, and
  it catches the "works, but not idiomatic" class that gates do not.

## The chain

| Role | Does | Must not |
|---|---|---|
| **architect** | Reads Truth, produces a stepwise plan for one stage into `docs/plans/`, with every design decision already made | Write product code |
| **worker** | Executes the plan. Runs the gates. Commits. | Guess at a decision that meets the BLOCK bar — stop instead |
| **reviewer** | Reviews the stage diff against `CLAUDE.md` and the relevant ADRs | Fix things silently; findings go back to worker |
| **doc-curator** | Updates roadmap status, writes or amends ADRs for decisions taken during the stage | Touch product code |

Every role appends one journal entry when done: five lines maximum, English, newest at
the top of `docs/journal.md`.

## Ownership / STOP

**This project runs autonomously.** The design is settled and ratified; the owner does
not want a checkpoint at every plan and every merge. Run the stage end to end.

- **Branch per stage:** `stage-N-<slug>`.
- **The worker is authorized to commit** on the stage branch.
- **Auto-merge to `main` when — and only when — every gate is green and review is
  clean.** The branch exists to keep the stage diff readable after the fact, not to
  block the owner.
- **The plan is not a gate.** It is written, committed, and execution starts. The owner
  reads it if and when they want to.
- Never `--force`, never `--no-verify`, never amend a pushed commit.
- The `gitleaks` pre-commit hook stays enabled; if it fires, fix the content rather than
  bypassing it. Enable hooks once per clone: `git config core.hooksPath .githooks`.
- Message style: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

**The gates carry the whole weight of this arrangement.** With auto-merge, they are the
only thing standing between an agent and a database two people depend on. Therefore:

> **A stage that cannot be verified mechanically must bring its own verification.**
> If a stage adds behaviour no gate can check, adding that check is part of the stage,
> not a follow-up. A green gate that proves nothing is worse than no gate, because
> autonomy is granted against it.

## Process state

- **Plans: `docs/plans/stage-N-<slug>.md`, committed.** Not gitignored, unlike the
  orchestrator's default — a plan is part of the record that makes a three-month pause
  survivable.
- Journal entries go to `docs/journal.md`; stage status to `docs/roadmap.md`. There is no
  separate handoff or todo file — see ADR-0010.

## Escalation — two channels

Interrupting the owner and informing the owner are different acts. Do not confuse them.

### BLOCK — stop and wait

Only for things that genuinely cannot proceed:

- an external prerequisite is missing: a bot token, VPS access, a paid account, a
  credential;
- a decision is required that is covered by neither the plan nor any ADR, and picking
  wrong would change the data model or a public interface;
- a gate fails for a reason that looks like a design problem rather than a bug;
- the work would contradict `CLAUDE.md` or an ADR. Contradicting an ADR is allowed, but
  it needs a superseding ADR — never a silent deviation.

Anything already answered by the plan, the spec or an ADR is **not** a blocker. Decide
and move.

### TEACH — inform without stopping

The owner is learning Python and applied LLM engineering through this project, and wants
to understand the code rather than merely own it. When a choice was made that is worth
understanding — an async pattern picked over another, a library idiom, a retry or
batching strategy, a schema-design trade-off — append a `## Learning notes` block to the
stage's journal entry. Three or four sentences: what was chosen, what it was chosen over,
and why.

**This never pauses the work.** It is read asynchronously.

## Team

Generic roles, no project-scoped names. `.claude/agents/` holds four project-local
definitions: `architect`, `worker`, `reviewer`, `doc-curator`. They are project-agnostic
— everything specific to this project is in this file and in `## Truth`.

There is no project-specific orchestrator skill here. The generic `/orchestrate` skill is
the entry point, subject to `## How much process` above.

Two things to know:

- **They are copies.** They were vendored from a user-level `~/.claude/agents/` so that
  a fresh clone works without any personal setup. Project-level definitions take
  precedence over user-level ones, so inside this repository these copies are what runs.
  If the originals improve, re-copy them.
- **They reference Superpowers skills** (`superpowers:writing-plans`,
  `test-driven-development`, `verification-before-completion`). Without that plugin
  installed the roles still work; the skill invocations are simply skipped.

## Working without subagents

If your tooling has no subagent mechanism — or you are a human — the chain is four
**phases**, not four programs. One actor runs them in sequence, and the value is in not
mixing them:

1. **Plan.** Read Truth. Write the stage plan to `docs/plans/`. Make every decision now.
   Stop. Have it read by someone before continuing.
2. **Implement.** Follow the plan exactly. Escalate instead of deciding.
3. **Review.** Read the diff cold, against `CLAUDE.md` and the ADRs — not against your
   own intentions five minutes ago.
4. **Document.** Update the roadmap status, write any ADR the stage produced, append the
   journal entry.

The gates and the journal matter more than the delegation.

## Pausing and resuming

The repository is the memory. Resuming after weeks means reading, in order:

1. `docs/roadmap.md` — which stage is 🚧
2. the top entries of `docs/journal.md` — where it stopped and why
3. `docs/decisions/` — why anything looks the way it does
4. `git log`

Nothing required to resume lives outside the repository. That is the point.
