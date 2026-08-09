# Stage 0 — Skeleton: implementation plan

> **For the worker:** every design decision in this document is already made. Do not
> re-decide. If something here contradicts `CLAUDE.md`,
> `docs/specs/2026-08-09-expense-capture-design.md` or an ADR, stop and escalate at the
> BLOCK bar (`.claude/orchestration.md` → `## Escalation`).

**Goal:** a typed, linted, migrated Python package that persists every whitelisted
incoming Telegram message to Postgres exactly once, with an integration test against a
real Postgres that proves it.

**Branch:** `stage-0-skeleton`.

---

## Reality check

Every claim in the brief was checked against files. Verified means: the file was read.

| Claim | Verdict | Evidence |
|---|---|---|
| Repo has no source code yet — docs + `.env.example` + `.githooks` only | **Verified** | Glob returns only `docs/`, `.claude/`, `evals/README.md`, `.githooks/pre-commit`, `.env.example`, `.gitignore`, `README.md`, `CLAUDE.md` |
| No `lint-rules/`; `pyproject.toml` config is the executable standard | **Verified** | `.claude/orchestration.md` states it; no `pyproject.toml` exists yet, so this plan creates the standard |
| Gates are exactly three commands | **Verified** | `.claude/orchestration.md` and `CLAUDE.md` agree byte-for-byte |
| `pytest` today would pass on zero tests | **False, and better than assumed** | pytest exits **5** ("no tests collected"), which fails the gate. The real hazard is not zero tests but *shallow* ones. Plan unchanged: the DB-backed test is still mandatory. |
| Stage 0 scope includes VPS + `pg_dump` cron | **Verified** | `docs/roadmap.md`. Both depend on owner-side prerequisites that do not exist; moved to `## Owner prerequisites`, out of the steps |
| Layer layout | **Verified** | `CLAUDE.md` and spec §3 agree |
| `core/` must not import `adapters/` or `llm/` | **Verified, non-negotiable** | `CLAUDE.md` rule 3 |
| Money is `numeric` | **Verified, vacuous here** | Rule 2. Stage 0 creates no money column |
| Every LLM call recorded in `extractions` | **Verified, vacuous here** | Rule 6. No LLM call in Stage 0, so no `extractions` table |
| Only `users` + `messages` needed | **Verified** | `messages.user_id` in spec §5 forces `users`. The other five tables have no Stage-0 writer |
| Dedup on `telegram_update_id` | **Verified** | spec §5 `UNIQUE`, spec §7 `ON CONFLICT DO NOTHING` |
| Silent ignore for non-whitelisted senders | **Verified** | spec §7: an "access denied" reply is an invitation to keep poking |
| `.env.example` names the config keys | **Verified** | Stage 0 consumes `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_CHAT_ID`, `DATABASE_URL`, `TIMEZONE`; the `OPENROUTER_*` / `MODEL_*` block is unused until Stage 1 |
| aiogram 3 `Dispatcher.feed_raw_update` exists | **Verified** | `async def feed_raw_update(self, bot, update: dict[str, Any], **kwargs) -> Any` |
| aiogram ships a public `MockedBot` | **FALSE** | `aiogram/test_utils/mocked_bot.py` → 404. `MockedBot` lives inside aiogram's own test suite and is not distributed. Consequence: this plan writes a project-local fake session (Step 2) |
| `BaseSession.__init__` takes no required args | **Verified** | all four parameters have defaults; abstract methods are `close()`, `make_request()`, `stream_content()` |
| `PostgresContainer` API | **Verified** | `PostgresContainer(image, port, username, password, dbname, driver='psycopg2')`, `get_connection_url(driver=...)` |

### One contradiction found — flagged, not silently deviated from

**Spec §4 and §7 assert:** *"Offsets advance only after successful processing… The queue
is built into the protocol."*

**aiogram does not do this.** It advances the `getUpdates` offset as soon as updates are
fetched and dispatches handlers as background tasks; a handler exception is logged and
the offset moves on regardless.

**Ruling for Stage 0:** implement the straightforward path and make the gap survivable
rather than invisible — the DB-session middleware logs the full serialised `Update` at
ERROR on failure, which is the only recovery path once the offset has moved. This does
not meet the BLOCK bar: no table and no public interface changes, and Stage 0's
done-criterion is unaffected. It matters from Stage 1, where a lost write is a lost
expense.

**Recorded as [ADR-0011](../decisions/0011-at-least-once-delivery-is-not-free.md),** which
supersedes the spec's claim and makes closing the gap an explicit Stage 1 requirement.

---

## Requirements

Derived from Truth; no open questions.

1. **R1 — Package skeleton.** `src/finbot/` with the layer directories Stage 0 actually
   uses: `core/`, `repo/`, `adapters/telegram/`. `llm/`, `prompts/`, `core/extraction/`,
   `core/fx/`, `core/categories/`, `core/reporting/` are **not** created. The layout in
   `CLAUDE.md` is a target materialised stage by stage; empty packages are the directory
   equivalent of empty tables.
2. **R2 — Executable standard.** `pyproject.toml` configures ruff (lint + format) and
   mypy in strict mode. It is binding.
3. **R3 — Layering is executable, not prose.** Rule 3 is enforced by a test in the
   `pytest` gate, not by convention.
4. **R4 — Schema.** Alembic wired to an async engine; one migration creating `users` and
   `messages` only, matching spec §5 column-for-column for those two tables.
5. **R5 — Bot.** aiogram 3 long polling; whitelist by Telegram user id with silent
   ignore; `/ping` → `pong`; every message from a whitelisted sender persisted to
   `messages`, deduplicated on `telegram_update_id`.
6. **R6 — Config from environment**, matching the keys already in `.env.example`,
   tolerating the keys Stage 0 does not use.
7. **R7 — Container.** `infra/Dockerfile` including `ffmpeg` (needed from Stage 2, per
   ADR-0004), `infra/docker-compose.yml` with `bot` + `postgres`.
8. **R8 — The stage brings its own verification.** An integration test against a **real
   Postgres** proving one update → exactly one row, including on redelivery of the same
   `update_id`, plus a migration/model drift guard.
9. **R9 — No LLM.** No OpenRouter client, no prompt file, no extraction, no categories,
   no currency, no report, no inline button.

---

## Approaches

### A. Test database: testcontainers vs a compose fixture vs SQLite

| Option | Pros | Cons |
|---|---|---|
| **testcontainers-python** (dev dep) | `pytest` alone brings the DB up — the gate is self-contained, which is what unattended merges require. Random host port. Torn down automatically, so no state leaks. Works unchanged if CI appears. | New dev dependency; needs a Docker daemon; ~3–5 s first-run startup |
| Compose-based fixture | No new dependency; identical image to production | Either the gate silently depends on a human having run compose first — a green gate that proves nothing — or `conftest.py` shells out to compose, which is slow to tear down and leaks volume state |
| SQLite in-memory | No Docker, milliseconds | `ON CONFLICT` semantics, `timestamptz` and `numeric` all differ. The one behaviour Stage 0 exists to prove would be tested against a different implementation. Green and meaningless. **Rejected on principle, not convenience** |

**Chosen: testcontainers.** The deciding argument is not speed — it is that the gate must
be honest without a human first doing something.

**Corollary, deliberate: if Docker is unavailable the DB tests FAIL, they do not skip.**
A skipped test in an auto-merging pipeline is a green gate that proves nothing. Unit
tests (config, mapping, layering) keep running without Docker, because only the DB tests
request the container fixture.

### B. Testing the aiogram handler without a live Telegram connection

| Option | Pros | Cons |
|---|---|---|
| Call handlers directly with `AsyncMock` | Trivial | Tests nothing that matters: Stage 0's behaviour lives in *middleware* and *routing order*, not handler bodies. A passing test would prove the whitelist and dedup paths are never exercised |
| **`feed_raw_update()` + a project-local fake `BaseSession`** | Exercises the real dispatcher, middleware chain and filters — the same object graph `main.py` builds. No socket opened. Outgoing API calls become assertable | ~40 lines of fake to maintain (aiogram ships no public equivalent) |
| Monkeypatch `Bot.send_message` | Fewer lines | Leaves the session intact, so an unmocked method silently attempts real HTTP from a test |

**Chosen: `feed_raw_update` + fake session.** The only option under which the whitelist
middleware, the persistence middleware and the `/ping` filter all actually execute. The
dispatcher comes from a **shared factory** (`build_dispatcher`) used by both `main.py` and
the tests, so the test cannot drift from production wiring.

### C. Where the layering rule is enforced

Ruff's `flake8-tidy-imports.banned-api` is global; expressing "only `core/` may not import
X" needs a global ban plus per-file exemptions, which inverts the rule and quietly stops
enforcing it as soon as a new exemption is added.

**Chosen: an AST test** (`tests/unit/test_layering.py`, ~20 lines, stdlib only) that walks
`src/finbot/core/**/*.py` and asserts no import names `finbot.adapters` or `finbot.llm`.
It states the rule directly, runs in the existing gate, needs no Docker, and survives
ruff upgrades.

### D. Who runs migrations

**Chosen: the container entrypoint** — `infra/entrypoint.sh` runs `alembic upgrade head`
then `exec`s the bot. Single node, single replica, no rolling deploy, so no
concurrent-migration hazard, and the owner cannot deploy a container whose schema is
behind. Rejected: a separate one-shot compose service (more moving parts, easy to forget);
manual `alembic upgrade head` over SSH (will be forgotten).

---

## Chosen approach

- **Runtime deps:** `aiogram`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic`,
  `pydantic-settings`. **Dev:** `pytest`, `pytest-asyncio`, `testcontainers[postgres]`,
  `ruff`, `mypy`. All except `pydantic-settings` and `testcontainers` are already named
  in `README.md` and implied by ADR-0001/0002 — those two are the only genuinely new
  choices, covered by the ADR below.
- **Flow:** `Update` → `AllowlistMiddleware` (silent ignore) → `DbSessionMiddleware` (unit
  of work, rollback + ERROR-log on failure) → `PersistMessageMiddleware` (user upsert +
  `ON CONFLICT DO NOTHING` insert + **commit before the handler runs**, implementing spec
  §4 step 6 literally) → handler (`/ping`).
- All three are **outer** middlewares on `dp.update`, so they run for every update whether
  or not a handler matches. That is what makes "every message is persisted, including ones
  no handler cares about" true.
- `core/models.py` holds `MessageKind` + `IncomingMessage` (Pydantic). `repo/` imports
  `core`; `adapters/telegram/` imports `core` and `repo`. `core` imports neither —
  enforced by R3.

## ADR worthy: yes

**Title:** *Stage-0 verification: a real Postgres in tests, Telegram exercised through the
dispatcher.*

Load-bearing because it sets the standing test pattern every later stage inherits, and
because it adds the two dependencies not previously named in Truth. It must record:
SQLite rejected on principle; skip-on-no-Docker rejected on principle; aiogram ships no
public `MockedBot`, hence the local fake; the migration/model drift guard.

The recurring technical rule *"`core/` imports neither `adapters/` nor `llm/`"* is made
executable by `tests/unit/test_layering.py` — the lint-rule equivalent in a project with
no `lint-rules/`.

---

## Owner prerequisites — outside the plan's steps; nothing below blocks the worker

1. **BotFather.** `/newbot` → name + username. Then **`/setprivacy` → Disable** for that
   bot (spec §1: without this the bot cannot see the other person's messages). Token →
   `.env` as `TELEGRAM_BOT_TOKEN`.
2. **Group + `chat_id`.** Create a private group, add both people and the bot, make the
   bot an admin. Send any message, then
   `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and read
   `result[0].message.chat.id` — a supergroup id is negative, e.g. `-100…`. →
   `TELEGRAM_CHAT_ID`.
3. **User IDs.** From the same response, `result[*].message.from.id` for each person. →
   `TELEGRAM_ALLOWED_USER_IDS=<id1>,<id2>`. **These never enter the repository**
   (`CLAUDE.md` rule 4); tests use fabricated ids.
4. **VPS.** Hetzner CX22 or similar, Ubuntu LTS, Docker + compose plugin, SSH key only,
   `ufw` allowing 22 only — long polling needs no inbound port (ADR-0002, spec §2).
5. **Deploy.** `git clone`, `cp .env.example .env` and fill it,
   `docker compose --env-file .env -f infra/docker-compose.yml up -d --build`. Verify:
   `/ping` in the group returns `pong`, and
   `docker compose -f infra/docker-compose.yml exec postgres psql -U finbot -d finbot -c 'select count(*) from messages;'`
   is non-zero.
6. **Backup cron** (roadmap, ADR-0001): daily `pg_dump` + a copy **off the VPS**. Deferred
   with the VPS; it cannot be written meaningfully before a host exists.

Items 1–3 are needed only for the manual smoke test. The whole plan below is verifiable on
a laptop without them.

---

## Steps

Three steps, each ending in a commit on `stage-0-skeleton`. Follow TDD
(`superpowers:test-driven-development`): failing test first, minimal implementation,
green, commit.

### Step 1 — Toolchain, config, schema, and a real-Postgres test harness

**Deliverable:** all three gates pass; a migrated `users`/`messages` schema exists in a
throwaway Postgres started by the test suite; model↔migration drift is detected
automatically.

**Files to create**

```
pyproject.toml
.dockerignore
alembic.ini
src/finbot/__init__.py
src/finbot/py.typed
src/finbot/config.py
src/finbot/core/__init__.py
src/finbot/core/models.py
src/finbot/repo/__init__.py
src/finbot/repo/models.py
src/finbot/repo/engine.py
src/finbot/repo/users.py
src/finbot/repo/messages.py
migrations/env.py
migrations/script.py.mako
migrations/versions/0001_users_and_messages.py
tests/__init__.py
tests/conftest.py
tests/unit/__init__.py
tests/unit/test_settings.py
tests/unit/test_layering.py
tests/integration/__init__.py
tests/integration/test_schema_matches_models.py
tests/integration/test_message_repo.py
```

**1.1 `pyproject.toml`** — this file *is* the executable standard. Exact content:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "finbot"
version = "0.1.0"
description = "Telegram bot that turns speech, text and receipts into household expenses"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "aiogram>=3.15,<4",
    "sqlalchemy[asyncio]>=2.0.36,<3",
    "asyncpg>=0.30,<0.31",
    "alembic>=1.14,<2",
    "pydantic>=2.9,<3",
    "pydantic-settings>=2.6,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "testcontainers[postgres]>=4.8",
    "ruff>=0.8",
    "mypy>=1.13",
]

[tool.hatch.build.targets.wheel]
packages = ["src/finbot"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]
extend-exclude = ["migrations/versions"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "DTZ", "T20", "SIM", "RUF", "ASYNC", "S", "PT"]
ignore = ["S104"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106"]

[tool.mypy]
python_version = "3.12"
strict = true
warn_unreachable = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["testcontainers.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q --strict-markers"
```

Not to be "improved": `T20` bans `print` (logging only); `DTZ` bans naive datetimes; `S`
is bandit, with `S101` (assert) allowed only in tests; `migrations/versions` is excluded
from ruff because generated migration headers fight the formatter. Install with
`pip install -e ".[dev]"`.

**1.2 `src/finbot/config.py`** — `Settings(BaseSettings)` with
`model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)`.

`extra="ignore"` is **required, not stylistic**: `.env.example` carries `OPENROUTER_*`,
`MODEL_*`, `POSTGRES_*`, `DEFAULT_CURRENCY`, `MAX_VOICE_SECONDS`, none of which Stage 0
declares. Without it the bot refuses to start on a correctly-filled `.env`.

Fields: `telegram_bot_token: SecretStr`, `telegram_allowed_user_ids: str`,
`telegram_chat_id: int`, `database_url: str`, `timezone: str = "Europe/Kyiv"`.

`telegram_allowed_user_ids` is `str` **deliberately**: pydantic-settings JSON-decodes
complex-typed fields straight from the environment source, so `frozenset[int]` would fail
on `111,222` before any validator ran. Parse in a property and validate eagerly:

```python
@property
def allowed_user_ids(self) -> frozenset[int]:
    return frozenset(int(p) for p in self.telegram_allowed_user_ids.split(",") if p.strip())

@model_validator(mode="after")
def _require_non_empty_allowlist(self) -> "Settings":
    if not self.allowed_user_ids:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one numeric id")
    return self
```

An empty allowlist means the bot silently ignores everyone — fail at startup, not at
2 a.m.

`tests/unit/test_settings.py` (no Docker): `"111,222"` → `frozenset({111, 222})`;
`" 111 , 222 "` → same; `""` → `ValidationError`; `"abc"` → `ValidationError`; an env
containing `OPENROUTER_API_KEY` does not raise. Construct `Settings(...)` with explicit
kwargs; do not touch the real environment.

**1.3 `src/finbot/core/models.py`** — imports nothing from `finbot.repo`,
`finbot.adapters`, `finbot.llm`, aiogram or SQLAlchemy.

```python
class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"

class IncomingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    telegram_update_id: int
    telegram_message_id: int
    chat_id: int
    telegram_user_id: int
    display_name: str
    kind: MessageKind
    raw_text: str | None = None
    file_id: str | None = None
```

**1.4 `src/finbot/repo/models.py`** — `Base(DeclarativeBase)` plus:

- `User`: `users`; `id` `BigInteger` PK autoincrement; `telegram_user_id` `BigInteger`
  unique not-null; `display_name` `String(128)` not-null; `created_at`
  `DateTime(timezone=True)` `server_default=func.now()` not-null.
- `Message`: `messages`; `id` as above; `telegram_update_id` `BigInteger` unique not-null;
  `telegram_message_id` `BigInteger` not-null; `chat_id` `BigInteger` not-null; `user_id`
  `BigInteger` FK → `users.id` not-null; `kind` mapped with
  `Enum(MessageKind, name="message_kind", native_enum=False, create_constraint=True, length=8, values_callable=lambda c: [m.value for m in c])`;
  `raw_text` `Text` nullable; `file_id` `String(256)` nullable; `created_at` as above.

`values_callable` is **required**: without it SQLAlchemy stores the member *name*
(`TEXT`), not the value (`text`), and the column would disagree with spec §5.
`native_enum=False` keeps it `VARCHAR(8)` + `CHECK`, so adding `document` later is an
`ALTER` of a check constraint rather than a Postgres enum-type dance.

No extra indexes: the FK and two unique constraints are all this volume needs.

**1.5 `src/finbot/repo/engine.py`**

```python
def create_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
```

`expire_on_commit=False` so committed ORM objects stay readable — with async sessions a
lazy refresh after commit raises rather than silently issuing SQL.

**1.6 `src/finbot/repo/users.py`** — `get_or_create(session, telegram_user_id, display_name) -> int`
using `postgresql.insert(User)…on_conflict_do_update(index_elements=[User.telegram_user_id], set_={"display_name": display_name}).returning(User.id)`.
**`DO UPDATE`, not `DO NOTHING`**: with `DO NOTHING` a concurrent second insert returns no
row and the caller has no id. Keeps `display_name` current for free. Does not commit.

**1.7 `src/finbot/repo/messages.py`** — `add_if_new(session, message, user_id) -> int | None`
using `on_conflict_do_nothing(index_elements=[Message.telegram_update_id]).returning(Message.id)`
then `scalar_one_or_none()`. Returns the new row id, or **`None` when the update was
already stored** — that `None` is how every later stage knows to skip re-extraction. Does
not commit.

**1.8 Alembic.** `alembic.ini`: `script_location = migrations`, `prepend_sys_path = src`,
**`sqlalchemy.url` left empty** — the URL comes from the environment, never from a
committed file (rule 4; `gitleaks` would block it anyway).

`migrations/env.py` — async template with these exact adaptations:

- `from finbot.repo.models import Base`, `target_metadata = Base.metadata`
- `config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("%", "%%"))`
  — the `%%` escape is not optional; ConfigParser interpolates `%` and a password
  containing one produces a baffling error
- `run_migrations_online()` is `asyncio.run(run_async_migrations())` using
  `async_engine_from_config(..., poolclass=pool.NullPool)` and
  `await connection.run_sync(do_run_migrations)`

Because `env.py` owns its own event loop, `alembic.command.upgrade(...)` is callable from
a **synchronous** pytest fixture — which is what makes 1.10 work.

`migrations/versions/0001_users_and_messages.py`: `revision = "0001"`,
`down_revision = None`. Hand-written, not autogenerated. Create `users` then `messages`.
The migration **must not import `finbot`** — spell the enum literally so it stays a frozen
snapshot:

```python
sa.Column("kind", sa.Enum("text", "voice", "photo", name="message_kind",
                          native_enum=False, create_constraint=True, length=8), nullable=False)
```

`downgrade()` drops `messages` then `users`.

**1.9 `tests/unit/test_layering.py`** — walk `src/finbot/core` for `*.py`, `ast.parse`
each, and for every `Import`/`ImportFrom` assert the module does not start with
`finbot.adapters` or `finbot.llm`. Fail with the offending file and line. No Docker,
milliseconds, and it keeps working when those packages appear in Stage 1.

**1.10 `tests/conftest.py`** — the harness the rest of the stage stands on.

```python
@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        os.environ["DATABASE_URL"] = url
        cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
        command.upgrade(cfg, "head")
        yield url
```

Session-scoped and **synchronous** — `PostgresContainer` is sync and Alembic's async
`env.py` runs its own `asyncio.run`, so there is no loop to clash with. Image pinned to
`postgres:16-alpine`, the same major version as compose.

*Contingency, not a decision:* if testcontainers needs a sync DBAPI to probe readiness,
add `psycopg[binary]` to the `dev` extra and keep `get_connection_url(driver="asyncpg")`
for the app URL.

Then a **function-scoped async** fixture `db_session` building a sessionmaker from
`postgres_url`, yielding an `AsyncSession`, and afterwards running
`TRUNCATE messages, users RESTART IDENTITY CASCADE` and disposing the engine.

**There is no `skipif` on Docker availability.** If the container cannot start, these
tests fail.

**1.11 `tests/integration/test_schema_matches_models.py`** — the drift guard, and the
cheapest permanent gate this stage can add:

```python
async def test_migration_matches_models(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(
                MigrationContext.configure(sync_conn, opts={"compare_type": True}), Base.metadata
            )
        )
    await engine.dispose()
    assert diff == []
```

From now on, a model changed without a migration fails the gate — in every later stage,
for free.

**1.12 `tests/integration/test_message_repo.py`**

- `test_add_if_new_returns_id_then_none_for_same_update_id` — second identical insert
  returns `None`, `count(*)` is 1.
- `test_get_or_create_is_idempotent_and_refreshes_display_name` — same
  `telegram_user_id` twice with different names → same id, one row, latest name.
- `test_kind_round_trips_as_lowercase_value` — store `MessageKind.VOICE`, read the raw
  column → `"voice"`, not `"VOICE"`.

**1.13 `.dockerignore`:** `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`,
`.mypy_cache`, `tests`, `docs`, `.claude`, `*.egg-info`, `.env`.

**Gates for Step 1** — from the repo root, output read:

```bash
ruff check . && ruff format --check .
mypy src/
pytest
```

**Commit:** `feat: package skeleton, config, users/messages schema and a real-Postgres test harness`

---

### Step 2 — Telegram adapter, and the test that makes the Stage 0 gate mean something

**Deliverable:** feeding a raw update through the real dispatcher persists exactly one
row; a redelivered `update_id` persists none; a stranger's update persists nothing and
sends nothing; `/ping` answers `pong`. No socket is opened.

**Files to create**

```
src/finbot/adapters/__init__.py
src/finbot/adapters/telegram/__init__.py
src/finbot/adapters/telegram/mapping.py
src/finbot/adapters/telegram/middlewares.py
src/finbot/adapters/telegram/handlers.py
src/finbot/adapters/telegram/main.py
tests/support/__init__.py
tests/support/fake_session.py
tests/support/updates.py
tests/unit/test_mapping.py
tests/integration/test_telegram_flow.py
```

**2.1 `mapping.py`** — the only place that knows aiogram's shape.
`to_incoming(update_id: int, message: Message) -> IncomingMessage | None`:

- `message.from_user is None` → `None`
- `message.voice` → `VOICE`, `file_id=message.voice.file_id`, `raw_text=message.caption`
- `message.photo` → `PHOTO`, `file_id=message.photo[-1].file_id` (last is the largest
  rendition), `raw_text=message.caption`
- `message.text is not None` → `TEXT`, `raw_text=message.text`, `file_id=None`
- anything else (sticker, document, video, service message) → `None`: silently ignored,
  not persisted. Stage 0 has no schema slot for it.

Check voice/photo **before** text, because a captioned photo has both.
`display_name = message.from_user.full_name`.

`tests/unit/test_mapping.py` (no Docker) covers all five branches.

**2.2 `middlewares.py`** — three `BaseMiddleware` subclasses, all registered as **outer**
middlewares on `dp.update`:

- `AllowlistMiddleware(allowed: frozenset[int])` — reads `event.message` and
  `message.from_user` **directly** rather than `data["event_from_user"]`, so it does not
  depend on aiogram's internal middleware ordering. Missing message, missing user, or
  `from_user.id not in allowed` → `return None`. **No reply, no INFO log** (spec §7:
  silence is the design).
- `DbSessionMiddleware(sessionmaker)` — `async with self._sessionmaker() as session:`,
  `data["session"] = session`, handler chain inside `try/except Exception`:
  `await session.rollback()`,
  `logger.exception("failed to process update_id=%s: %s", event.update_id, event.model_dump_json(exclude_none=True))`,
  `raise`. The serialised update is logged **deliberately**: per ADR-0011 the polling
  offset has already advanced, so this line is the only remaining copy. It goes to the
  container log on a private VPS, never to the repository.
- `PersistMessageMiddleware()` — no message or `to_incoming(...) is None` → `return None`;
  otherwise `user_id = await users.get_or_create(...)`,
  `row_id = await messages.add_if_new(...)`, **`await session.commit()`**, then
  `data["message_row_id"] = row_id`, `data["is_duplicate"] = row_id is None`, then call
  the handler.

The commit sits **before** the handler on purpose: spec §4 step 6, *"Write to the database
before replying."* Vacuous for `pong`, load-bearing from Stage 1 — build the shape now.

**2.3 `handlers.py`** — `router = Router(name="commands")` and

```python
@router.message(Command("ping"))
async def ping(message: Message) -> None:
    await message.answer("pong")
```

**2.4 `main.py`** — the factory is the point:

```python
def build_dispatcher(sessionmaker: async_sessionmaker[AsyncSession],
                     allowed_user_ids: frozenset[int]) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(AllowlistMiddleware(allowed_user_ids))
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(PersistMessageMiddleware())
    dp.include_router(router)
    return dp
```

Registration order **is** execution order: reject strangers before opening a database
session for them.

```python
async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    dp = build_dispatcher(create_sessionmaker(settings.database_url), settings.allowed_user_ids)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    await dp.start_polling(bot, allowed_updates=["message"])
```

`allowed_updates=["message"]` — Stage 1 adds `callback_query`. `SecretStr` keeps the token
out of any accidental repr.

**2.5 `tests/support/fake_session.py`** — aiogram ships no public `MockedBot`, so:

```python
class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod[TelegramType],
                           timeout: int | None = None) -> TelegramType:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return cast(TelegramType, _CANNED_USER)
        if isinstance(method, SendMessage):
            return cast(TelegramType, _CANNED_MESSAGE)
        raise AssertionError(f"unexpected Telegram API call: {type(method).__name__}")
```

`super().__init__()` with no args is correct (all four parameters have defaults). The
`cast` is required under mypy strict, since `TelegramType` is unbound. `GetMe` is handled
because aiogram's `Command` filter calls `bot.me()` when a command carries an `@mention`;
an unexpected method raising `AssertionError` is intentional — it turns "the code called
Telegram behind my back" into a loud failure rather than a hung socket. `stream_content`
raises `NotImplementedError` and still yields once so it stays an async generator.

**2.6 `tests/support/updates.py`** — raw-dict builders (`text_update`, `voice_update`) with
a module docstring stating that **all ids are fabricated** (rule 4). Use
`ALLOWED_USER_ID = 111111111`, `STRANGER_USER_ID = 999999999`,
`CHAT_ID = -1001111111111`. Text payloads must be plain `/ping`, without `@mention`.

**2.7 `tests/integration/test_telegram_flow.py`** — the load-bearing test. Fixture:
`bot = Bot(token="42:TESTTOKEN", session=FakeSession())`,
`dp = build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))` — the same factory
`main.py` calls, so the test cannot drift from production wiring.

- **`test_update_is_persisted_exactly_once_on_redelivery`** — the one the stage exists
  for. Feed `text_update(update_id=1001, text="bread 50")`; assert one row with correct
  `telegram_update_id`, `telegram_message_id`, `chat_id`, `kind == TEXT`,
  `raw_text == "bread 50"`. Then feed **the byte-identical payload again**; assert
  `messages` is still **1** and `users` still **1**.
- **`test_stranger_is_ignored_silently`** — `messages` 0, `users` 0, and
  **`bot.session.requests == []`**.
- **`test_ping_replies_pong_and_is_persisted`** — exactly one `SendMessage` with
  `.text == "pong"`, and one `messages` row with `raw_text == "/ping"`. Commands are
  persisted: `messages` is *what arrived* (ADR-0006); filtering them out of extraction is
  Stage 1's job.
- **`test_voice_message_stores_file_id`** — `kind == VOICE`, `file_id` set,
  `raw_text is None`.
- **`test_unsupported_content_is_not_persisted`** — a sticker → 0 rows, no API call.

**Gates for Step 2:** all three commands again, full output read.

**Commit:** `feat: telegram adapter with allowlist, /ping and deduplicated message persistence`

---

### Step 3 — Container, compose, docs, and the record

**Deliverable:** the image builds and contains `ffmpeg`; the stack starts with one
command; the repository states how to run it; roadmap and journal reflect reality.

```
Create: infra/Dockerfile
Create: infra/docker-compose.yml
Create: infra/entrypoint.sh
Modify: README.md            ("Running it" section)
Modify: docs/roadmap.md      (Stage 0 status)
Modify: docs/journal.md      (new entry at the TOP)
```

**3.1 `infra/Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations/ migrations/
COPY infra/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && useradd --create-home --uid 1000 finbot \
 && chown -R finbot:finbot /app
USER finbot

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

`ffmpeg` is installed now, in Stage 0, because ADR-0004 names it as the Stage 2 fallback
for Telegram's OGG/Opus, and discovering a missing system package during a
model-integration stage is exactly the half-day of guessing the staged start exists to
prevent. `README.md` is copied because `pyproject.toml` declares it as `readme` and
hatchling fails without it.

**3.2 `infra/entrypoint.sh`**

```sh
#!/bin/sh
set -e
alembic upgrade head
exec python -m finbot.adapters.telegram.main
```

**3.3 `infra/docker-compose.yml`** — services `postgres` (`postgres:16-alpine`, named
volume `pgdata`, credentials from the environment, `pg_isready` healthcheck at
`interval: 5s / retries: 10`, `restart: unless-stopped`) and `bot`
(`build: {context: .., dockerfile: infra/Dockerfile}`, `env_file: ../.env`,
`depends_on: {postgres: {condition: service_healthy}}`, `restart: unless-stopped`).

**Postgres publishes no ports.** Long polling needs no inbound port at all (ADR-0002,
spec §2), and an unpublished database on a public VPS cannot be port-scanned. Access it
with `docker compose exec postgres psql`. In the healthcheck write `$${POSTGRES_USER}` —
the doubled `$` escapes Compose interpolation so the container's shell expands it.

Paths inside the compose file are relative to `infra/`; the documented invocation from the
repo root is:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

**3.4 `README.md`** — replace *"Nothing to run yet"* with a "Running it" section:
`cp .env.example .env` and fill it; local development `pip install -e ".[dev]"` and the
three gate commands; **an explicit note that the test suite starts a real Postgres
container and therefore needs a running Docker daemon, and that these tests fail rather
than skip when Docker is absent — by design**; the compose command above; keep the
`git config core.hooksPath .githooks` line.

**3.5 `docs/roadmap.md`** — leave Stage 0 as **🚧**, not ✅, and add one line:

> *Software complete (package, schema, bot, tests). Remaining: Hetzner VPS provisioning,
> deployment, and the `pg_dump` cron — all blocked on owner-side prerequisites, see
> `docs/plans/stage-0-skeleton.md` → Owner prerequisites.*

Marking a stage done when a third of its bullets are untouched is the one lie that makes
the roadmap useless to a reader returning in three months.

**3.6 `docs/journal.md`** — append **at the top**, exactly the documented format, English,
five lines maximum:

```markdown
## 2026-08-09 · stage 0 · worker
**Did:** package skeleton, users/messages schema, aiogram long polling with allowlist and dedup, real-Postgres test harness
**Hit:** spec §4/§7 claim Telegram redelivers unacknowledged updates, but aiogram advances the polling offset before handlers finish — see ADR-0011
**Next:** stage 1 (text → expense); owner-side VPS, deploy and pg_dump cron still outstanding
**Open:** at-least-once delivery needs its mechanism chosen at stage 1, where a lost write is a lost expense
```

Then a `## Learning notes` block (the TEACH channel; it does not count against the five
lines) covering, in three or four sentences: why dedup is `INSERT … ON CONFLICT DO
NOTHING` returning `None` rather than `SELECT`-then-`INSERT` (the read-then-write version
has a race that a `UNIQUE` index closes atomically in one round trip); why the tests drive
`Dispatcher.feed_raw_update` with a fake `BaseSession` instead of mocking handler
functions (the behaviour under test lives in middleware and routing, not handler bodies);
and why the suite starts a real Postgres rather than SQLite (`ON CONFLICT` is the thing
being proven, and SQLite's is a different implementation).

**3.7 Verification for Step 3** — the three gates, plus two container checks that need no
bot token:

```bash
ruff check . && ruff format --check .
mypy src/
pytest

# requires a local .env: cp .env.example .env, set POSTGRES_PASSWORD to anything
docker compose -f infra/docker-compose.yml build bot
docker compose -f infra/docker-compose.yml run --rm --no-deps --entrypoint ffmpeg bot -version
```

The second command must print an ffmpeg version banner. That is the proof for R7 and the
only cheap way to find out now, rather than in Stage 2, that the image is missing a system
package.

Run `ruff format .` before committing — the gate is `ruff format --check .`.

**Commit:** `chore: docker image with ffmpeg, compose stack, run instructions and stage 0 journal entry`

**Then:** merge `stage-0-skeleton` to `main` once all three gates are green **and** review
is clean. Never `--force`, never `--no-verify`. If `gitleaks` fires, fix the content.

**Hand-off to `doc-curator`:** this stage is `## ADR worthy: yes` — the verification ADR
named above must be written before the stage counts as documented.

---

## Status: Done
