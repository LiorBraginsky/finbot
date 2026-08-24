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

## 2026-08-24 · stage 2 · lior
**Did:** ran the voice eval on five real recordings — 2 models x 5 cases x 2 repeats — and set `MODEL_VOICE=google/gemini-3.5-flash-lite`, then deployed Stage 2 (migration `0003`) to the VPS.
**Hit:** the samples had to be identified before they could be labelled: seven voice notes existed with no record of which phrase was in which, so each was transcribed through the production path first — used for identification only, never as ground truth, which came from the agreed script.
**Next:** dictate five expenses in one note in the real chat; that, not a green gate, is Stage 2's done-criterion.
**Open:** five cases saturated the exact metrics again (10/10 for both models); only `transcript_ok` separated them, and it did so in the cheaper model's favour.

| model | schema_ok | count_exact | amount_exact | category_exact | date_exact | transcript_ok | mean cost (USD) | p50 latency (ms) | p95 latency (ms) |
|---|---|---|---|---|---|---|---|---|---|
| google/gemini-3.5-flash-lite | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 0.000493 | 1050 | 1643 |
| google/gemini-3.6-flash | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 | 0.001911 | 2876 | 4328 |

**Chosen:** `MODEL_VOICE=google/gemini-3.5-flash-lite` — it won every axis at once, including the one metric that separated the two: the 3.9x-pricier control lost a `transcript_ok` case the cheaper model got right. A voice note costs $0.000493 against text's $0.000276, only 1.8x more; audio pricing was the cost risk ADR-0015 flagged, and it did not materialise.

## Learning notes
Three things this run taught that the harness could not. First, the eval had been sending raw OGG/Opus bytes labelled `"format": "mp3"` — `ffmpeg` was never invoked outside the bot, so the runner measured a different input than production sends. It was found while writing documentation, not by a test: a worker asked to state in a README that "the runner converts exactly as the bot does" went to check whether that was true. Prose forces a claim to be verified; tests only compare code against code. The fix imports the bot's own `convert_to_mp3` and is pinned by two tests — an identity assertion (a future private reimplementation fails even if it behaves identically) and a behavioural one through a fake `ffmpeg` (an import that is correct but never awaited fails that one). Second, `expected_transcript_contains` must never carry an amount: the speaker says "двісті пʼятдесят" and this model transcribes "250", so asserting on the surface form would fail for a reason unrelated to hearing. That is now a property test — no entry anywhere in the set may contain a digit — rather than a convention. Third, the sample-pulling helper required Postgres access that exists on no machine: the compose file publishes no port (ADR-0002) and the image does not contain `evals/`. It was formally correct and practically unrunnable, which is a class of defect a reality check catches only if it asks "from where, exactly, does this run".

---

## 2026-08-10 · stage 2 · worker
**Did:** Stage 2 end to end — voice extraction parallel to text (`core/extraction/voice.py`, a hand-built `VoiceExtractionResult` schema, `extract_voice.v1`, a shared repair loop factored out of `pipeline.py`'s text/voice split), `adapters/telegram/audio.py` (download to memory, `ffmpeg` stdin→stdout, no temp files, always converts — never a try-original fallback), the two voice-only guards (`MODEL_VOICE` unset, over `MAX_VOICE_SECONDS`) and the transcript-after-extraction currency guard, the `🎤 «...»` confirmation line, and `evals/run.py --modality voice` with its own `transcript_ok` metric. Test count 255 -> 329.
**Hit:** ADR-0004's own Consequences line read as "try the original OGG/Opus first, fall back to `ffmpeg`", which this stage's decisions explicitly contradict (always convert, unconditionally) — wrote ADR-0015 to supersede that line rather than silently deviate from an existing ADR, per `.claude/orchestration.md`'s own escalation rule.
**Next:** owner records real voice notes into `evals/golden/voice/` (see that directory's README, and `evals/pull_voice_samples.py` for pulling ones already sent), runs `python -m evals.run --modality voice`, and sets `MODEL_VOICE`/`MODEL_FALLBACKS` — Stage 2's real done-criterion, five dictated expenses landing correctly in the real chat, starts only after that.
**Open:** `evals/golden/voice_v1.jsonl` ships five hand-written cases with no audio behind them yet (ADR-0009: audio is never committed) — the runner fails on a missing file until the owner records some, which is the correct, honest failure, not a reason to commit one.

## Learning notes
The repair loop, the `extractions` row per attempt, cost/model_id extraction and the retry backoff are now one function (`pipeline._run_extraction_round`) parameterised over a `parse: Callable[[str], ResultT]` — a PEP 695 generic (`def _run_extraction_round[ResultT](...)`, Python 3.12's own syntax, not `typing.TypeVar`, since `ruff`'s `UP047`/`UP049` rules push toward it) — called once with `text.parse_content` and once with `voice.parse_content`. The alternative was two near-identical copies of the loop, one per modality, which is exactly the kind of drift CLAUDE.md rule 6 exists to prevent: a fix to cost extraction or backoff timing landing in one copy and not the other. Separately, `core.extraction.pipeline` never imports `adapters.telegram.audio` (which owns aiogram's download API and the `ffmpeg` subprocess) — it only imports `AudioFetchError` from `core.extraction.ports` and accepts an already-bound `fetch_audio: Callable[[], Awaitable[bytes]]` from its caller. This is the same seam `LlmClient` already provides for the model call: `core` depends on an abstract capability and an exception type, never on the concrete thing that performs the capability, which is what keeps `core` importable and testable with no aiogram, no subprocess, and no Docker.

---

## 2026-08-10 · stage 1 · worker
**Did:** first-production-day hardening — a foreign-currency guard refuses ("$", "usd"/"eur", Ukrainian/Russian word forms) before any model call, since the schema has no currency field yet and the model was silently recording "10 dollars" as 10.00 UAH; `setMyCommands` registers a day/week/month/help menu; a catch-all now answers every unrecognised `/command` instead of silence; the confirmation's deleted-line marker is "✖️", not a literal "~". Test count 217 -> 255.
**Hit:** `/weer` and `/mounth` (typos for `/week`/`/month`) matched no handler at all and got no reply — the exact bug the catch-all fixes, and the one a wrongly-ordered catch-all could just as easily reintroduce by swallowing `/day` instead.
**Next:** the household's real usage is the only gate that can prove the currency refusal message reads right; nothing to change until it says otherwise.
**Open:** editing an amount (Stage 6) and the report web-stats link (Stage 7) are recorded as a deferral and a rejection respectively in docs/roadmap.md, not actioned here.

## Learning notes
The foreign-currency detector needed "attached to a digit" matching ("10дол") alongside whole-word matching ("10 доларів"), which plain `\b` cannot give: `\b` never places a boundary between two `\w` characters, and a digit and a following letter are both `\w`. The fix is the standard Unicode-aware idiom `(?<![^\W\d])`/`(?![^\W\d])` — "not preceded/followed by a `\w` character that isn't a digit", i.e. not a letter — which treats a digit as a valid boundary without hand-listing an alphabet, working for Cyrillic exactly as it does for Latin. Separately, the guard's "skip" outcome deliberately does *not* extend `ExtractionStatus` (exactly Postgres's three-value CHECK constraint on `extractions.status`, per that enum's own docstring) even though it needed a fourth case to signal: reusing it would have required a migration for a value that would never actually appear in that table, since a currency hit writes no `extractions` row at all. A plain `bool` field on `ExtractionOutcome`, checked before `status` is ever read, kept the guard's result out of a type that describes something else.

---

## 2026-08-10 · stage 1 · lior
**Did:** ran the Stage 1 model eval — 11 golden cases × 3 repeats, 33 calls/model, five candidates — and set `MODEL_TEXT=google/gemini-3.5-flash-lite`, `MODEL_FALLBACKS=google/gemini-3.6-flash`.
**Hit:** the pre-registered gate picked cleanly between the two survivors, but three of five candidates failed for reasons that have nothing to do with extraction quality — see Learning notes.
**Next:** deploy with these model settings; Stage 1's real done-criterion — a week of recording expenses without opening a spreadsheet — starts now, and no gate can prove it.
**Open:** the golden set saturated at 33/33 for both Gemini candidates, so it cannot show which is more accurate — Stage 3 needs harder cases before accuracy, rather than just cost and latency, can separate them.

| model | schema_ok | count_exact | amount_exact | category_exact | date_exact | mean cost (USD) | p50 latency (ms) | p95 latency (ms) |
|---|---|---|---|---|---|---|---|---|
| mistralai/mistral-nemo | 15/33 | 15/33 | 15/33 | 15/33 | 15/33 | 0.000030 | 7049 | 37239 |
| openai/gpt-oss-20b | 28/33 | 28/33 | 28/33 | 28/33 | 28/33 | 0.000074 | 3299 | 30546 |
| openai/gpt-5.6-luna | 0/33 | 0/33 | 0/33 | 0/33 | 0/33 | n/a | 0 | 0 |
| google/gemini-3.5-flash-lite | 33/33 | 33/33 | 33/33 | 33/33 | 33/33 | 0.000276 | 558 | 796 |
| google/gemini-3.6-flash | 33/33 | 33/33 | 33/33 | 33/33 | 33/33 | 0.002408 | 1663 | 2101 |

**Chosen:** `MODEL_TEXT=google/gemini-3.5-flash-lite`, `MODEL_FALLBACKS=google/gemini-3.6-flash`. Both Gemini rows cleared the `schema_ok ≥ 30/33` gate with perfect `amount_exact`/`count_exact`; criterion 3 takes the cheaper, which is also 3× faster (8.7× cheaper: $0.000276 vs $0.002408).

## Learning notes
Three findings, each a different failure kind, none about language understanding. `mistral-nemo` — infrastructure: HTTP 504 and 429, p95 37s, the cheapest model in the catalogue effectively unavailable, because narrowing the provider pool with `require_parameters: true` leaves thin models with few endpoints. `gpt-5.6-luna` — 404 on every call despite appearing in `GET /api/v1/models` with `structured_outputs`: presence in the catalogue is not availability to an account, so the pre-run check verified the wrong property. `gpt-oss-20b` — the endpoint advertises structured output but the model does not honour it: it returned a list where an object was required, and on `quantity-11` emitted an `analysis` field full of chain-of-thought plus junk keys (`'дві кави по 65': 1`); `require_parameters: true` constrains routing, not compliance, and only measurement separates the two. Separately, the eval saturated: eleven cases proved too easy for the strong models, so Stage 3 must add hard ones (mixed Ukrainian/Russian shorthand, clipped phrases, lending and repayment, several expenses with no separators), which the production corrections source will eventually supply.

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
