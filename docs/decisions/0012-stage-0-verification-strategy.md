# ADR-0012 — Stage-0 verification: a real Postgres in tests, Telegram exercised through the dispatcher

**Date:** 2026-08-09 · **Status:** accepted
**Related:** [ADR-0001](0001-postgres-over-spreadsheet.md) (Postgres is the store),
[ADR-0006](0006-separate-provenance-tables.md) (`messages` is what arrived),
[ADR-0011](0011-at-least-once-delivery-is-not-free.md) (the delivery gap Stage 0 accepts).
Implements `.claude/orchestration.md` → `## Ownership / STOP`: *"A stage that cannot be
verified mechanically must bring its own verification."*

## Context

This repository merges to `main` unattended, on green gates. The gates are therefore not a
quality ritual; they are the only thing standing between an agent and a database two
people depend on. Stage 0 is the first stage with any code, so whatever it does becomes
the standing test pattern — every later stage inherits it, and nobody will re-derive it.

Stage 0's behaviour lives entirely at boundaries. `INSERT … ON CONFLICT DO NOTHING`
against a unique index is a database guarantee, not application logic. The allowlist and
the persistence step are aiogram **outer** middlewares on `dp.update`, so they are reached
through routing, not through a function call. The schema is a hand-written migration that
has to keep agreeing with the ORM models. None of this is in a function body a unit test
can call and learn anything from.

Stage 0 also added the two dependencies not previously implied by Truth:
**`pydantic-settings`** (runtime — environment parsing that fails at startup rather than at
2 a.m., with `extra="ignore"` so the unused `OPENROUTER_*` block in `.env` does not stop
the bot) and **`testcontainers`** (dev — the subject of this record).

## Decision

Six choices, each of which later stages inherit without deciding again.

### 1. A real Postgres, started by the test suite

`testcontainers` brings up `postgres:16-alpine` — the same major version as compose — once
per session, runs `alembic upgrade head` against it, and hands the URL to the integration
tests. `pytest` alone is sufficient; no human has to start anything first. The per-test
`db_session` fixture built on top of it truncates before yielding, not after as originally
planned: pytest throws into a fixture's generator at the `yield` point when a test fails,
so a truncate placed after `yield` inside a `try` is skipped on exactly that path, and rows
leak into the next test. Every later stage that uses `db_session` inherits the resulting
rule for free — a test's starting state cannot depend on how the previous test's teardown
behaved.

**SQLite in memory was rejected on principle, not on convenience.** `ON CONFLICT DO
NOTHING` on a unique index is precisely the behaviour Stage 0 exists to prove, and SQLite
implements it differently. A green test against a different engine proves nothing about
the engine in production. `timestamptz` and `numeric` semantics differ too, and Stage 1
adds money columns, where rule 2 makes `numeric` non-negotiable.

A compose-based fixture was rejected for a related reason: it is green either because the
database is right or because someone remembered to run `docker compose up` — the gate
cannot tell which.

### 2. Without Docker the tests fail; they do not skip

There is no `skipif` on Docker availability, and adding one later is a regression, not a
convenience. On an auto-merging branch a skipped database test is a green gate that proves
nothing, which is the exact failure the substrate names.

The cost is bounded because only the DB tests request the container fixture: config,
mapping and layering tests still run on a laptop with no Docker daemon.

### 3. Telegram is exercised through the real `Dispatcher`

Tests feed raw update dictionaries to `Dispatcher.feed_raw_update()` with a project-local
`FakeSession` (`tests/support/fake_session.py`) replacing the transport. They do not call
handler functions.

The behaviour under test — allowlist, dedup, "DB write before reply" — lives in the outer
middlewares and in registration order, not in handler bodies. A direct call to `ping()`
would pass whether or not the middleware chain existed, which makes it worse than no test.

aiogram ships no public `MockedBot`: `aiogram/test_utils/mocked_bot.py` exists only inside
aiogram's own test suite and is not distributed. Hence the local fake. It records every
outgoing `TelegramMethod` instead of sending it, and raises `AssertionError` on any method
it does not recognise — so code that calls Telegram behind the test's back fails loudly
instead of opening a socket or hanging.

`build_dispatcher()` and `build_router()` are the single factories both `main.py` and the
tests use, so production wiring and tested wiring cannot drift. `build_router()` is a
factory rather than a module-level `Router` because an aiogram `Router` can be attached to
exactly one `Dispatcher` for its lifetime; a shared instance breaks the second test that
builds a dispatcher.

### 4. A migration/model drift guard

`tests/integration/test_schema_matches_models.py` runs Alembic's `compare_metadata`
against the migrated database and asserts the diff is empty. From now on, a model changed
without a migration fails the gate — in every later stage, for free. This is the cheapest
permanent gate Stage 0 could add.

One documented exception. Alembic excludes *type-bound* CHECK constraints — the ones a
column type such as `Enum(native_enum=False, create_constraint=True)` attaches to itself —
on the metadata side, but cannot make the same exclusion on the reflected side, because
the type-bound marker does not survive a round trip through the database. Left unfiltered,
the `message_kind` column reports a permanent, spurious `remove_constraint` diff. The
exclusion is therefore narrow: that one constraint, by name and by type. Every other kind
of drift is still detected.

### 5. The layering rule is executable, not prose

`CLAUDE.md` rule 3 — `core/` imports neither `adapters/` nor `llm/` — is enforced by
`tests/unit/test_layering.py`, an AST walk in the existing gate. In a project with no
`lint-rules/` directory, this test is the lint-rule equivalent; the executable standard is
`pyproject.toml` plus checks like this one, never a sentence in a document.

The review round taught what a rule-as-test must also prove about itself. The first
version could pass vacuously on a missing or empty directory, and it missed the relative
import forms — including `from .. import adapters`, where the banned name is in
`node.names` because `node.module` is `None`. The fix is now held in place by a
table-driven test over every AST shape a banned target can take, with two control cases
that must **not** be flagged. A lint rule that only ever
sees passing input is not known to work.

### 6. The gate must be deterministic, not merely usually green

`tests/conftest.py` sets `TESTCONTAINERS_RYUK_DISABLED=true`.

The root cause was read in the library, not guessed. `Reaper._create_instance()` calls
`.start()` on the Ryuk sidecar and applies its readiness wait only afterwards, so the wait
is a no-op; the `get_exposed_port(8080)` on the following line then loses a race against
Docker's port registration roughly one run in three, raising `ConnectionError: Port
mapping … is not available`. Disabling Ryuk removes the racing container entirely.

The accepted cost, stated plainly: Ryuk is what force-removes the throwaway Postgres if
the test process dies hard — SIGKILL, OOM, power loss. Without it, such a death leaks one
container. Every normal exit — pass, fail, Ctrl-C — still stops it through
`with PostgresContainer(...)`.

## Rationale

An intermittent gate on an auto-merging branch has two outcomes and both are bad. It
blocks at random, or it trains a rerun-until-green habit — and the second destroys the
gate's meaning, because after that nobody can tell a flake from a real failure. A gate is
either evidence or decoration.

The same argument runs through all six choices. A test against the wrong engine, a test
that skipped, a test that called past the middleware it was meant to check, a drift guard
that was never wired, a rule that lives only in a Markdown file, a suite that fails one
run in three — each is green in a way that carries no information. Autonomy here is
granted against the gates, so a gate that proves nothing is worse than no gate at all: it
buys permission it has not earned.

## Consequences

- **Docker is a hard requirement for the test suite**, on a laptop and in any future CI.
  `README.md` says so, and says the tests fail rather than skip by design.
- **Later stages get the harness for free.** `postgres_url` and `db_session` already
  exist; a Stage-1 test of `expenses` writes assertions, not infrastructure.
- **New tables and columns are guarded from the moment they are written** — the drift
  check needs nothing added to it.
- **The exception list in the drift guard is a liability to watch.** It is one constraint
  today. Every future addition to it narrows the guard, so each one needs the same
  justification: the diff is spurious, not merely inconvenient.
- **Telegram-facing tests keep going through `feed_raw_update()`.** When Stage 1 adds
  `callback_query` for the ✏️ / 🗑 buttons, the inline-button flow is tested the same way,
  and `FakeSession` grows a branch for the new method rather than a mock appearing beside
  it.
- **A leaked container after a hard kill is possible**, at the price of a deterministic
  gate. `docker ps` and `docker rm` clear it; the trade is deliberate.
- **`pydantic-settings` and `testcontainers` are now named in the record.** They were the
  only dependencies Stage 0 introduced that no earlier document implied.

## Rejected

**SQLite in memory** — milliseconds, no daemon, and it tests a different `ON CONFLICT`
implementation than the one that runs in production. Rejected on principle: the one
behaviour Stage 0 exists to prove would be proven against the wrong engine.

**`skipif` when Docker is absent** — turns "I could not verify this" into a green gate on
a branch that merges itself.

**Calling handlers directly with mocks** — Stage 0's behaviour is in middleware and
routing order; such a test passes whether or not either exists.

**A bounded retry around container startup** — the obvious fix, and worse than it looks. A
correct retry has to guard two racy call sites, and has to clear the orphaned Ryuk
container between attempts through a private API. Disabling Ryuk removes the racing path
instead of papering over it, and the only thing lost is cleanup after an abnormal death.

---

## Amendment — 2026-08-10, Stage 1

Nothing above is withdrawn. Stage 1 extended the standing pattern in four ways, recorded
here rather than in a new ADR because they are additions to this decision, not a different
one. Written alongside [ADR-0013](0013-messages-table-is-the-inbox.md) and
[ADR-0014](0014-structured-output-and-the-evals-split.md); the plan is
`docs/plans/stage-1-text-to-expense.md`.

### A fake **LLM** client beside the fake transport session

Choice 3 replaced the Telegram transport. Stage 1 added the second thing the code talks to
over a socket: `tests/support/fake_llm.py` is `FakeSession`'s counterpart for the model. It
is scripted with responses in order, records every `LlmRequest` it was given — which is how
the repair prompt is asserted — and raises `AssertionError` on any call beyond its script,
because "the code called a model behind my back" must be a loud failure, never a bill.

It parses its scripted bodies through `finbot.llm.openrouter.parse_response_body`, the exact
function `OpenRouterClient` calls, so a change to cost or model-id extraction cannot pass
here while failing in production. As a second line, `tests/conftest.py` points
`OPENROUTER_BASE_URL` at the discard port (`http://127.0.0.1:9`), so a real client
constructed by accident fails in milliseconds instead of spending money. A blanket socket
ban is deliberately **not** used: testcontainers talks to the Docker socket over HTTP.

### Fixtures are whole recorded response bodies, never domain objects

`tests/fixtures/openrouter/*.json` hold complete HTTP response bodies, because the failures
worth catching are *shape* failures nobody invents correctly by hand: `content` arriving as
a string containing JSON, a provider adding a ```json fence under a strict `response_format`,
`usage.cost` being `null`, an error object returned inside a 200. The unit under test is the
parser, so its input has to be what the wire carries.

Their provenance is part of the record: the initial versions are hand-written from the
documented response schema, because a prerequisite (an API key) must never gate a step, and
`python -m evals.run --save-raw DIR` refreshes them from **synthetic golden cases only**
(ADR-0009). The same pattern applies to any later provider surface — Stage 2's audio and
Stage 4's vision responses get recorded bodies, not hand-built DTOs.

### Two more executable rules

Choice 5 established that a technical rule belongs in the gate, not in prose. Stage 1 added
two:

- **`tests/unit/test_no_float_money.py`** — `CLAUDE.md` rule 2 at the one place it can be
  broken silently, the JSON wire. An AST walk over `src/finbot/` and `evals/` resolves calls
  to json's `loads` by binding rather than by name (module attribute or bare name, aliased
  either way) and requires `parse_float`; `core/money.py` is the single allow-listed file.
  Table-driven over the grammar, with control cases that must not be flagged — including the
  substring trap `loads_decimal`. See ADR-0014 §5.
- **`tests/unit/test_main.py::test_allowed_updates_matches_registered_handlers`** —
  `ALLOWED_UPDATES` is what the bot asks Telegram for; `dp.resolve_used_update_types()` is
  what the router can actually handle, and the test asserts they are equal. Stage 0 shipped
  `["message"]`, so every ✏️ tap would have been dropped by Telegram before reaching the
  process: no log, no reply, nothing to debug. This equality makes that class of bug
  unmergeable. Its sibling, `test_no_global_error_handler_is_registered`, is ADR-0013's.

Both follow choice 5's rule about rules: a check that only ever sees passing input is not
known to work.

### Two consequences above came due

- **The drift guard's exception list grew from one name to three** — `message_status` and
  `extraction_status` join `message_kind`. Each is the identical spurious diff for the
  identical reason (a type-bound CHECK constraint that cannot be excluded symmetrically on
  the reflected side), and each is excluded by name, never by broadening the guard to "all
  check constraints". The liability this ADR named is being watched, not forgotten.
- **`FakeSession` grew branches rather than acquiring a mock beside it**, exactly as the
  consequence about `callback_query` predicted: `AnswerCallbackQuery`, `EditMessageText`,
  `EditMessageReplyMarkup`, and a `GetUpdates` branch that records the offset it was called
  with — which is what lets `tests/unit/test_polling_offset.py` prove ADR-0013's guarantee
  with no database and no network.

One new harness shape was needed, and it obeys choice 2.
`tests/integration/test_migration_backfill.py` starts its **own** container rather than
using the session-scoped `postgres_url`, because that fixture is already at `head` before
any test could observe the pre-`0002` state the backfill exists to fix. It still fails
rather than skips when Docker is absent.
