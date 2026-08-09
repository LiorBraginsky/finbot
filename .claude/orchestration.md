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

Anything smaller than a stage — a typo, a one-file fix, a doc edit — is done directly,
with no chain.

## The chain

| Role | Does | Must not |
|---|---|---|
| **architect** | Reads Truth, produces a stepwise plan for one stage into `docs/plans/`, with every design decision already made | Write product code |
| **worker** | Executes the plan. Runs the gates. Commits. | Make a decision the plan left open — escalate instead |
| **reviewer** | Reviews the stage diff against `CLAUDE.md` and the relevant ADRs | Fix things silently; findings go back to worker |
| **doc-curator** | Updates roadmap status, writes or amends ADRs for decisions taken during the stage | Touch product code |

Every role appends one journal entry when done: five lines maximum, English, newest at
the top of `docs/journal.md`.

## Commits

**The worker is explicitly authorized to commit in this repository.** Pushing is the
owner's call unless stated otherwise for a given stage.

Never `--force`, never `--no-verify`, never amend a pushed commit. The `gitleaks`
pre-commit hook stays enabled; if it fires, fix the content rather than bypassing it.
Enable hooks once per clone: `git config core.hooksPath .githooks`.

Message style: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

## Escalation

Stop and ask rather than guess when:

- the plan is silent on a decision that changes the data model or an interface;
- a gate fails for a reason that looks like a design problem rather than a bug;
- a change would violate a rule in `CLAUDE.md` or contradict an ADR.

Contradicting an ADR is allowed — but it requires a superseding ADR, not a silent
deviation.

## Agents

`.claude/agents/` holds four project-local agent definitions: `architect`, `worker`,
`reviewer`, `doc-curator`. They are generic and project-agnostic — everything specific
to this project is in this file and in `## Truth`.

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
