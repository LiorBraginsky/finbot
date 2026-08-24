# Roadmap

Status: ⬜ not started · 🚧 in progress · ✅ done

Each stage is a thin vertical slice — something usable the same evening — not a
horizontal layer. A stage that never reaches a human produces no signal about whether
the model is any good.

---

## ✅ Stage 0 — Skeleton

Infrastructure only. **No LLM anywhere in this stage.**

- Hetzner VPS, `docker compose` with `bot` + `postgres`
- aiogram long polling, whitelist of two user IDs, `/ping`
- Raw incoming messages persisted to `messages`
- Alembic wired up
- `pg_dump` cron + off-site copy
- `gitleaks` hook enabled

**Done when:** the bot answers `/ping` from the VPS and messages land in the table.

*Closed 2026-08-09, verified in production: `/ping` answered from the VPS, five messages
from two whitelisted senders persisted, `count(*) == count(distinct telegram_update_id)`.
Daily `pg_dump` on cron, restore never yet exercised. **One item remains open: the dump
lives on the same host as the database**, so it survives a bad `DELETE` but not a dead
server. Off-site copy is the first thing to fix outside a stage.*

*Why separate: if infrastructure and model land together, the first failure costs half a
day of guessing whether it was Docker, the network, Telegram, Postgres or the prompt.*

---

## 🚧 Stage 1 — MVP: text → expense

- Extraction from text with a fixed Pydantic schema, repair loop on invalid output
- Fixed category list (~12), model must choose from it
- Write to `expenses`, record the call in `extractions`
- Confirmation message with ✏️ / 🗑 inline buttons
- `/day`, `/week`, `/month` — plain SQL
- ~10 hand-written eval cases and a throwaway runner, enough to pick a cheap model

**Done when:** a week of recording expenses by text, without opening a spreadsheet.

*Software complete: extraction, the thirteen categories, the confirmation/report flow, and
the eleven-case golden set with a runner (`python -m evals.run`) are all built and covered
by `pytest`. **The eval ran** against the five candidate models on 2026-08-10 (33 calls
each) and the model is chosen — `MODEL_TEXT=google/gemini-3.5-flash-lite`,
`MODEL_FALLBACKS=google/gemini-3.6-flash`, both clearing the pre-registered `schema_ok`
gate, the cheaper and faster of the two — see `docs/journal.md`'s top entry for the table,
the choice, and the criterion that produced it. **What remains is not software:**
deploying with these settings, and then the stage's actual done-criterion — a week of
recording expenses by text without opening a spreadsheet — which no gate can prove and has
not started yet. Not marked done until it has.*

*2026-08-10 hardening, from the first day of real production use: a foreign-currency
guard refuses ("$", "usd"/"eur", Ukrainian/Russian word forms) before the model is ever
called, since the schema has no currency field yet and the model was silently recording
"10 dollars" as 10.00 UAH; `setMyCommands` now registers a `day`/`week`/`month`/`help`
menu and a catch-all answers every unrecognised `/command` with `/help`'s text instead of
staying silent; and the render module's deleted-line marker is "✖️", not a literal "~"
that read as a glitch under `parse_mode=None`. A persistent `ReplyKeyboardMarkup` with
day/week/month buttons was considered and **rejected**, not deferred: it replaces the
chat's normal keyboard, and this chat is 95% typing — `setMyCommands` covers the same
need without that cost.*

---

## ⬜ Stage 1.5 — Currencies

- USD / EUR alongside UAH
- NBU rate for the expense date, cached in `fx_rates`
- Degrade to the last known rate and say so in the confirmation

**Done when:** *"100 dollars for hosting"* is stored with a correct `amount_uah`.

Two deferrals from Stage 1 land here, both recorded in ADR-0013 and the journal:

- **`fx_rate_date` is currently set on rows that were never converted** (every UAH row
  gets `fx_rate_date = occurred_at`, with `fx_rate = 1`). Stage 1.5's inevitable "which
  rows used a stale or missing rate" query would otherwise find a year of rows claiming a
  rate date they never had. Decide the honest value — `NULL` for unconverted rows — and
  backfill.
- **A lease column (`claimed_until`) instead of the crash-release guard.** One predicate
  in `claim_next` would then cover a caught exception, a SIGKILL and a stalled worker
  alike, replacing the startup `reset_processing` and the conditional release. Rejected
  in Stage 1 as a migration plus a renewal loop for two users; revisit if a second worker
  ever appears.

---

## 🚧 Stage 2 — Voice

- OGG/Opus from Telegram → multimodal model → `{transcript, expenses[]}`
- `ffmpeg` converts every voice note, unconditionally — see ADR-0015, which
  supersedes this stage's own original "fallback path" wording
- Transcript shown in the confirmation and stored as provenance

**Done when:** five expenses dictated in one voice note all land correctly.

*2026-08-24: the voice eval ran on five real recordings (2 models x 5 cases x 2 repeats) and
`MODEL_VOICE=google/gemini-3.5-flash-lite` is chosen and deployed — it took every metric
at once, and the 3.9x-pricier control lost the only case that separated them. A voice note
costs $0.000493 against text's $0.000276. Migration `0003` is applied in production. See
`docs/journal.md`'s top entry for the table and the criterion. **What remains is the
criterion itself:** five expenses dictated in one note, in the real chat, landing
correctly. No gate can prove that, and it has not been tried. Two spare Ukrainian takes of
the Russian case are recorded but unused, and the set saturated on the exact metrics — only
`transcript_ok` distinguished the two models, so Stage 3 needs harder voice cases before
accuracy can separate anything again.*

*Software complete: the voice extraction path (`core/extraction/voice.py`, a hand-built
strict schema carrying `transcript` alongside `expenses`, `extract_voice.v1`), the
download-to-memory-then-`ffmpeg`-to-mp3 module (`adapters/telegram/audio.py`, no temp
files, a size ceiling), the pipeline routing on `message.kind` with one repair loop
shared between text and voice, the confirmation's `🎤 «...»` transcript line, and the
`--modality voice` eval runner with its own `transcript_ok` metric are all built and
covered by `pytest` (255 → 329). Two guards run before any download or model call —
`MODEL_VOICE` unset, and a note over `MAX_VOICE_SECONDS` — refusing with an explanation
and a findable `last_error`, exactly like the text currency guard. The currency guard
itself runs on voice *after* extraction, on the model's own transcript, since there is no
text to inspect beforehand; the call is already paid for by then, which ADR-0004's
original design already accepted. **What remains is not software:** the owner sets
`MODEL_VOICE` after running `python -m evals.run --modality voice` against the recordings
`evals/golden/voice/README.md` describes producing, then the stage's actual
done-criterion — five expenses dictated in one voice note landing correctly, in the real
household chat — which no gate can prove and has not started yet. Not marked done until
it has.*

---

## 🚧 Stage 2.5 — Bank-app screenshots

- A screenshot of a bank-app transaction feed → vision model → rows classified into
  `expense` / `income` / `savings` / `own_transfer` / `transfer_out`
- **Only `expense` rows are written to `expenses`** — the other four are shown, never
  stored (vision.md excludes income, savings and transfers; ADR-0006 gains no fourth table)
- The model transcribes each row's date header verbatim; code resolves it against the
  message's own arrival time and cross-checks the header's own weekday as a checksum —
  the model is never told today's date
- A database-enforced dedup key (`date | time | amount`, merchant deliberately excluded)
  makes re-sending the same or an overlapping screenshot a no-op
- A two-message reply (a note, then the usual confirmation) so a bank-specific summary
  survives the confirmation buttons being tapped, plus a one-tap "delete all" for a batch
  above the 12-row keyboard cap

**Done when:** one day's screenshot lands as the right expenses, with savings, transfers
and incoming money visibly skipped rather than silently dropped, and re-sending the same
screenshot records nothing new — **no gate can prove this, and it has not been tried.**

*Software complete: bank-feed extraction (`core/extraction/bank.py`, its own hand-built
strict schema, `extract_bank.v1`), the date resolver with its own exhaustive table-driven
test (`bank_dates.py` — the model reads the header, code owns the calendar), the
`bank_txn_key` unique constraint (migration `0004`) and the manual-collision probe it
enables, the note-then-confirmation reply with the delete-all keyboard row, and
`evals/run.py --modality bank` with its own pre-registered `no_false_expense` gate are
all built and covered by `pytest`. **What remains is not software:** the owner builds the
private case set (`evals/golden/bank/README.md` — the labels are as private as the
pixels, so neither is committed), runs the eval, sets `MODEL_VISION`, deploys migration
`0004`, and then the stage's actual done-criterion above, which no gate can prove and has
not started yet.*

Three deferrals this stage names rather than actions:

- **Stage 4 inherits a photo-disambiguation debt.** Every incoming photo runs this
  stage's bank-feed prompt today, guarded only by the model's own `is_transaction_feed`
  flag — a soft signal, not a router. Stage 4 must decide how a photographed receipt and
  a bank-feed screenshot, both `MessageKind.PHOTO`, choose which prompt actually runs.
- **Stage 5 gains a candidate category.** The spike's screenshots showed an education service
  landing in `other` for want of an `education` slot in the thirteen-category catalog.
- **Stage 6 owns date and amount editing** — already recorded there as a deferral, and
  the only real remedy for this stage's one known-wrong case: a screenshot sent the day
  after it was taken resolves `Сьогодні`/`Вчора` against the wrong arrival time and lands
  a day late, today correctable only by 🗑 and retyping the whole expense by hand.

---

## ⬜ Stage 3 — Evaluation harness

- Metrics: `amount_exact`, `count_exact`, `category_exact`, `date_exact`,
  `item_similar` (the only one that needs a judge), `cost_per_item`, latency p50/p95
- Real cases read straight from Postgres (`messages` + `expenses` + `corrections`),
  never copied into files; synthetic cases stay in `evals/golden/`
- Prompt versioning compared version-to-version
- Runner across N models × M cases

**Done when:** there is a model × accuracy × cost × latency table built on real data.

*Do not defer this further. After Stage 2 there is real traffic and real corrections —
the harness is cheapest and most useful exactly then.*

*This is also the first stage where an unattended loop earns its place: the stopping
criterion is numeric (metrics stop improving), so an agent can iterate on prompts and
model choice without supervision. Autonomy follows gate quality, not the other way
round.*

---

## ⬜ Stage 4 — Receipt photos

- Image → vision model → `{merchant, receipt_total, expenses[]}`
- Cross-check `sum(expenses) ≈ receipt_total`; flag the mismatch instead of hiding it

**Done when:** a supermarket receipt decomposes into line items and the total reconciles.

---

## ⬜ Stage 5 — Category proposals

- Model returns `proposed_category` when nothing fits, never invents silently
- Confirmation offers *Create "Pets"?* / *Put in "Other"*
- Duplicate check against existing categories before creating
- Rejected proposals are remembered so they are not offered again

*Deliberately late: first find out on real data which categories are actually missing.
Finding out that twelve were enough is the best possible outcome.*

---

## ⬜ Stage 6 — Web UI for corrections (C)

FastAPI as a second adapter over the same `core`, plus a minimal frontend in `web/`.
Only if the inline buttons prove insufficient in practice.

- **Editing an amount.** Only the category is editable today (✏️ picks a category; fixing
  an amount means 🗑 and retyping the whole expense) — deferred deliberately from the
  2026-08-10 hardening batch, since deleting and retyping is acceptable for now. This
  stage already owns corrections UX, so the fix belongs here: `ForceReply` on the
  confirmation message, plus a small `pending_edits` table so the reply survives a
  restart. The foreign-currency guard must apply to the replied amount too — a
  household member correcting "50" to "50 dollars" must be refused exactly like a fresh
  message would be.

---

## ⬜ Stage 7 — Dashboard and free-form questions (D)

Charts, and questions like *"how much did we spend on coffee in June?"*.

**This is where a real agent belongs** — read-only tools or text-to-SQL, with its own
budget and its own evaluation set. Nowhere earlier.

- A web-stats link belongs on an inline button under a report, not on Telegram's blue
  Menu button: a Menu button with a WebApp only exists in private chats, and this bot
  lives in a group (2026-08-10 hardening batch).

---

## Continuous, from day one

- Backups: `pg_dump` on cron, copy stored **off the VPS**
- Every LLM call recorded in `extractions` with cost and latency
