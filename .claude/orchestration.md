# Orchestration

How work moves through this repository. Read together with `CLAUDE.md`.

## Unit of work

**One stage from `docs/roadmap.md`.** Not one commit, not one file. Running the full
chain on trivial changes is overhead; running it per stage gives both discipline and a
clean point to stop for a month and come back.

Anything smaller than a stage — a typo, a one-file fix, a doc edit — is done directly,
no chain.

## The chain

| Role | Does | Must not |
|---|---|---|
| **architect** | Reads vision, roadmap, spec, ADRs and recent journal. Produces a stepwise plan for one stage, with the decisions already made. | Write product code |
| **worker** | Executes the plan. TDD where it fits. Runs the gates. Commits. | Make design decisions the plan left open — escalate instead |
| **reviewer** | Reviews the stage diff against the rules in `CLAUDE.md` and the relevant ADRs. | Fix things silently; findings go back to worker |
| **doc-curator** | Updates roadmap status, writes or amends ADRs for decisions taken during the stage. | Touch product code |

Every role appends one journal entry when done. Five lines, English, newest on top.

## Gates

A stage is not finished until all of these pass and the output has been read:

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

Plus, once an evaluation harness exists (Stage 3): any change to a prompt or a model
requires `python -m evals.run` before and after, with both results recorded in the
journal entry.

## Escalation

Agents stop and ask rather than guess when:

- the plan is silent on a decision that changes the data model or an interface
- a gate fails for a reason that looks like a design problem rather than a bug
- a change would violate a rule in `CLAUDE.md` or contradict an ADR

Contradicting an ADR is allowed — but it requires a new ADR that supersedes it, not a
silent deviation.

## Pausing and resuming

The repository is the memory. Resuming after weeks means reading, in order:

1. `docs/roadmap.md` — which stage is 🚧
2. the top few entries of `docs/journal.md` — where it stopped and why
3. `docs/decisions/` — why anything looks the way it does
4. `git log` — what actually changed

Nothing required to resume lives outside the repository. That is the point.

## Commits

Agents may commit. Never `--force`, never `--no-verify`, never amend a pushed commit.
The `gitleaks` pre-commit hook stays on; if it fires, fix the content.
