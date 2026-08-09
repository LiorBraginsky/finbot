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
standard is the `ruff` and `mypy` configuration in `pyproject.toml`. Treat it as binding
in the same way — not advisory.

## Gates

A stage is not finished until all of these pass **and the output has been read**:

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

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
| **worker** | Executes the plan. Runs the gates. Commits. | Make a decision the plan left open — escalate instead |
| **reviewer** | Reviews the stage diff against `CLAUDE.md` and the relevant ADRs | Fix things silently; findings go back to worker |
| **doc-curator** | Updates roadmap status, writes or amends ADRs for decisions taken during the stage | Touch product code |

Every role appends one journal entry when done: five lines maximum, English, newest at
the top of `docs/journal.md`.

## Ownership / STOP

- **Branch per stage:** `stage-N-<slug>`. `main` stays green.
- **The worker is explicitly authorized to commit** on a stage branch.
- **Merging is not the chain's to do.** Stop at "PR ready, gates green, review clean"
  and hand back to the owner. Never merge, never push to `main` directly.
- Never `--force`, never `--no-verify`, never amend a pushed commit.
- The `gitleaks` pre-commit hook stays enabled; if it fires, fix the content rather than
  bypassing it. Enable hooks once per clone: `git config core.hooksPath .githooks`.
- Message style: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

## Process state

- **Plans: `docs/plans/stage-N-<slug>.md`, committed.** Not gitignored, unlike the
  orchestrator's default. A plan is part of the record that makes a three-month pause
  survivable, and it is the artefact the owner reviews before any code is written.
- Journal entries go to `docs/journal.md`; stage status to `docs/roadmap.md`. There is no
  separate handoff or todo file — see ADR-0010.

## Escalation

Stop and ask rather than guess when:

- the plan is silent on a decision that changes the data model or an interface;
- a gate fails for a reason that looks like a design problem rather than a bug;
- a change would violate a rule in `CLAUDE.md` or contradict an ADR.

Contradicting an ADR is allowed — but it requires a superseding ADR, not a silent
deviation.

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
