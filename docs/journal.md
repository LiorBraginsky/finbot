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

## 2026-08-10 · stage 1 · doc-curator
**Did:** wrote ADR-0013 (the `messages` inbox, the ack-on-durable-write guarantee, the status machine and the conditional crash release — supersedes ADR-0011) and ADR-0014 (strict structured output under a hand-derived schema, cost and model id from the response body, the malformed-200 failure class, the pytest/evals split); appended a Stage 1 amendment to ADR-0012; pointed ADR-0011 and the index at 0013.
**Hit:** eight production modules cite ADR-0013/0014 by name and neither existed, so every citation was dangling; `middlewares.py`'s `DbSessionMiddleware` docstring still asserts ADR-0011's premise as present fact, which `polling.py` falsified — reported to the worker rather than edited, since this role does not touch product code.
**Next:** Lior ratifies 0013 and 0014 (`proposed` -> `accepted`), at which point ADR-0011's status line flips to superseded.
**Open:** `tests/fixtures/openrouter/README.md` lists five fixtures but the review round added three (`error_envelope_200`, `no_choices`, `null_content`); Stage 1.5 in the roadmap does not yet name ADR-0013's deferred lease-column alternative.

---

## 2026-08-10 · stage 1 · worker
**Did:** closed the Stage 1 review — migration now backfills pre-existing `messages` to `skipped` instead of replaying Stage 0's backlog; `parse_response_body` validates shape and raises `LlmError` on a malformed 200; the drain releases a crashed claim via a fresh session instead of leaving it `processing` forever; plus four MAJORs (backoff reset timing, an open read transaction across the LLM call, SIGTERM racing the long poll, `--save-raw` writing raw text verbatim) and six MINORs. Test count 197 -> 205.
**Hit:** the crash-release fix needed a guard the finding didn't spell out — `extract_and_store` commits `messages.status` before `_process_claimed` ever calls Telegram, so unconditionally calling `schedule_retry` from the crash handler would have resurrected an already-`done` row and reprocessed it (double billing, duplicate expenses); the release now only touches a row still `processing`.
**Next:** owner runs `python -m evals.run` against the five candidates and sets `MODEL_TEXT`/`MODEL_FALLBACKS`, closing Stage 1.
**Open:** the lease-column alternative for Critical 3 is deferred to Stage 1.5, as is `fx_rate_date` on unconverted rows (MINOR 14, left untouched — plan-specified).

---

## 2026-08-10 · stage 1 · worker
**Did:** re-review round two — `parse_response_body`'s `try` now starts at `loads_decimal` itself (non-JSON bodies, a null model, `usage` not a mapping, `usage.cost` not a number all now raise `LlmError`, four new fixtures); `LlmError` carries an optional `cost_usd` that a malformed-but-partially-legible 200 still fills in, recorded by both `pipeline.py` and `evals/run.py`; `_release_crashed_claim` gets a pinning test proving the `!= PROCESSING` guard is load-bearing; `polling.py`'s backoff wait is now stop-aware (`_sleep_or_stop`), and `_release_crashed_claim`'s reply-send failure no longer misreports as a release failure. Test count 205 -> 211.
**Hit:** deleting the `!= PROCESSING` guard alone left all 205 prior tests green — the first crash-recovery test always crashes before `extract_and_store` ever commits, so it can't see the guard; the new test crashes `render_confirmation` *after* a real `ok_two_items` commit, and does turn red without the guard (`done` flips to `pending`).
**Next:** owner runs `python -m evals.run` against the five candidates and sets `MODEL_TEXT`/`MODEL_FALLBACKS`, closing Stage 1.
**Open:** `tests/fixtures/openrouter/README.md` now lists five fixtures against eleven that exist (three from round one, four from round two) — compounding the doc-curator's own noted gap; the open-transaction test still observes `_category_ids`, a test helper, rather than the production sequence (reviewer's finding C, deferred to Stage 1.5 per instruction).

---

## 2026-08-10 · stage 1 · worker
**Did:** Stage 1 end to end — money/category rules, versioned prompt and an OpenRouter client with a repair loop, inbox delivery with working inline buttons and SQL reports, and now the eleven-case golden set plus a runner (`python -m evals.run`) scoring five candidate models through that same production code path. Test count 163 -> 197.
**Hit:** `qwen/qwen3.7-flash`, the cheapest candidate, was dropped after the live catalogue showed no `structured_outputs` support — `provider.require_parameters: true` would have left it zero eligible routes; separately, `aiohttp` raises the builtin `TimeoutError`, not a `ClientError` subclass, on total-timeout expiry.
**Next:** the owner runs the evals against the five candidates with a real OpenRouter key, chooses `MODEL_TEXT`/`MODEL_FALLBACKS`, and Stage 1 closes.
**Open:** whether the cheapest model clearing the `schema_ok` gate also holds `amount_exact`/`count_exact`, or the 116x-pricier control model earns its keep — that comparison is the eval run itself, still to be run.

## Learning notes
`core.money.loads_decimal`'s `parse_float=Decimal` is the entire distance between CLAUDE.md rule 2 and a float silently entering the ledger: plain `json.loads` parses `50.89` through the C float parser before `Decimal` ever sees it, so the guard has to intercept the JSON text itself, not the Python value that comes out of it. The JSON Schema sent to OpenRouter is hand-derived rather than taken from `ExpenseDraft.model_json_schema()`, because Pydantic's emitter produces `$defs`/`$ref` for nested models, omits `additionalProperties: false`, and types `Decimal` as `anyOf[number, string]` — none of which strict structured-output mode accepts — so the derivation is tested recursively rather than trusted. `provider.require_parameters: true` matters because structured-output support is a property of the *endpoint* actually serving a model, not of the model itself; without it, a request can silently route to a provider that ignores `response_format` and returns prose, which reads downstream as a bad model rather than bad routing. The processing round's ack is withheld only at the durable write in `messages`/`expenses` — never in a handler, never on an earlier step — because that write is the one point whose failure must make Telegram redeliver; acking anything upstream of it would let a crash between "read" and "write" lose the message for good.

## 2026-08-09 · stage 0 · lior
**Did:** deployed to a Hetzner CX23 in Frankfurt and verified in production — `/ping` answered, five messages from two whitelisted senders persisted, `count(*) == count(distinct telegram_update_id)`; daily `pg_dump` on cron. Stage 0 closed.
**Hit:** Telegram's privacy-mode change does not apply to groups the bot has already joined — the bot must be removed and re-added, otherwise `getUpdates` stays empty and looks like a token problem
**Next:** stage 1 (text → expense) — needs an OpenRouter key with a spend limit set before any model call
**Open:** the dump sits on the same host as the database; an off-site copy is still missing, and no restore has been exercised

## 2026-08-09 · stage 0 · worker
**Did:** package skeleton, users/messages schema, aiogram long polling with allowlist and dedup, docker image + compose stack, real-Postgres test harness; also closed the two pytest deprecation warnings (`testcontainers.community.postgres`, alembic `path_separator = os`)
**Hit:** spec §4/§7 claim Telegram redelivers unacknowledged updates, but aiogram advances the polling offset before handlers finish — see ADR-0011; two more plan deviations went unrecorded until reviewed: the schema-drift guard needed a named exclusion for `message_kind`'s type-bound CHECK constraint (Alembic can't reflect it symmetrically), and ruff's Markdown exclusion had to generalize from `docs` to `*.md`, since its formatter reformats fenced python blocks in any Markdown file; and the harness itself went red about one run in three until testcontainers' Ryuk sidecar — which races its own port lookup — was disabled, making the gate deterministic (ADR-0012)
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
