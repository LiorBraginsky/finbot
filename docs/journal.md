# Journal

Append-only work log. **Newest entry at the top.** English only.

This file exists for what git does not record: intent, hypotheses, dead ends, and the
reason something was abandoned. The diff is already in git — do not restate it here.

## Format

```markdown
## YYYY-MM-DD · stage N · author
**Did:** what changed, one line
**Hit:** what went wrong or surprised you
**Next:** the immediate next step
**Open:** unresolved question, or omit the line
```

Rules: maximum five lines per entry. One entry per unit of work, not per commit.
Author is an agent role (`architect`, `worker`, `reviewer`, `doc-curator`) or `lior`.

An entry may carry one optional block, and only when there is something to teach:

```markdown
## Learning notes
Three or four sentences: what was chosen, what it was chosen over, and why.
```

That block is how work reaches the owner without stopping it — see
`.claude/orchestration.md` → `## Escalation`. It does not count against the five lines.

Current state is *derived*, not stored: stage statuses live in `docs/roadmap.md`, and
the most recent entry below says where work stopped. There is no STATE file to fall out
of date.

---

## 2026-08-09 · stage 0 · worker
**Did:** package skeleton, users/messages schema, aiogram long polling with allowlist and dedup, docker image + compose stack, real-Postgres test harness; also closed the two pytest deprecation warnings (`testcontainers.community.postgres`, alembic `path_separator = os`)
**Hit:** spec §4/§7 claim Telegram redelivers unacknowledged updates, but aiogram advances the polling offset before handlers finish — see ADR-0011; two more plan deviations went unrecorded until reviewed: the schema-drift guard needed a named exclusion for `message_kind`'s type-bound CHECK constraint (Alembic can't reflect it symmetrically), and ruff's Markdown exclusion had to generalize from `docs` to `*.md`, since its formatter reformats fenced python blocks in any Markdown file
**Next:** stage 1 (text → expense); owner-side VPS, deploy and pg_dump cron still outstanding
**Open:** at-least-once delivery needs its mechanism chosen at stage 1, where a lost write is a lost expense

## Learning notes
Two idioms here are the most TS-vs-Python-instructive things in the codebase. First, `INSERT … ON CONFLICT DO NOTHING/UPDATE … RETURNING` in `repo/users.py` and `repo/messages.py` pushes the uniqueness guarantee into one round-trip SQL statement, closing a race window a Node/Prisma-style read-then-write can't close without an explicit transaction and lock. Second, `expire_on_commit=False` on the async sessionmaker: SQLAlchemy's async session, unlike sync SQLAlchemy, cannot transparently re-fetch an expired attribute on access — that would need an awaited query in the middle of attribute access, which Python's attribute protocol can't express — so without this flag a lazy refresh after commit raises instead of silently issuing SQL. Underpinning both: `mypy --strict` plus the `pydantic.mypy` plugin checks `Settings`, the frozen `IncomingMessage`, and the `int | None` return of `add_if_new` structurally, the closest Python equivalent to TypeScript's structural typing — except fully erased at runtime, so `mypy src/` is the only thing standing between "type-checks" and a `NoneType has no attribute` in production. On the test side, the suite drives `Dispatcher.feed_raw_update()` through the real dispatcher with a project-local fake `BaseSession` instead of calling `ping()` directly, because the behaviour Stage 0 exists to prove — the allowlist, the dedup, "DB write before reply" — lives entirely in the three outer middlewares, not in any handler body; only the real `Dispatcher`, assembled by the same `build_dispatcher()` factory `main.py` calls, forces every middleware to run in actual production order (the same reason `build_router()` is a factory too — an aiogram `Router` can be attached to exactly one `Dispatcher` for its lifetime). aiogram ships no public `MockedBot`, so `FakeSession` is a ~35-line stand-in for `BaseSession` that records outgoing `TelegramMethod` calls instead of opening a socket, and raises `AssertionError` on anything it doesn't recognise, turning "the code called Telegram behind my back" into a loud failure instead of a hung socket. Finally, the suite starts a real Postgres via `testcontainers` rather than SQLite, because `ON CONFLICT` is exactly the behaviour under test and SQLite implements it differently — a green test against the wrong engine would prove nothing.

## 2026-08-09 · stage 0 · lior
**Did:** switched the substrate to autonomous operation — auto-merge on green gates, plan no longer a checkpoint, escalation split into BLOCK (stop) and TEACH (a `## Learning notes` block that informs without stopping)
**Hit:** unattended merge moves all the weight onto the gates, and there are none yet — so the substrate now requires any stage that cannot be verified mechanically to bring its own verification
**Next:** Stage 0, executed end to end without checkpoints
**Open:** an unattended loop over the plan is deferred to Stage 3, where the stopping criterion is numeric

## 2026-08-09 · stage 0 · lior
**Did:** vendored the four agent definitions into `.claude/agents/` and rewrote the orchestration substrate around the sections the agents actually parse
**Hit:** the agents look up `## Truth` and `## Gates` by name, and the worker refuses to commit unless the substrate authorizes it explicitly — the first version had neither, so the chain would have stalled on its first run
**Next:** unchanged — Stage 0
**Open:** vendored agents are a frozen copy of the user-level ones; no mechanism keeps them in sync

## 2026-08-09 · stage 0 · lior
**Did:** design settled end to end — hosting, storage, modality routing, error taxonomy, eval approach; repo scaffolded with vision, roadmap, spec and ten ADRs
**Hit:** OpenCode Zen has no speech-to-text models; switched to OpenRouter, which accepts audio inline and collapses transcription and extraction into one call
**Next:** Stage 0 — VPS, docker compose, long polling, raw message persistence, no LLM
**Open:** whether one multimodal call beats a dedicated STT model on Ukrainian mixed speech — first real eval
