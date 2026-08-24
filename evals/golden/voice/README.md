# Voice golden audio

This directory holds the actual audio files `../voice_v1.jsonl` references by
filename (`single-01.oga`, `multi-02.oga`, ...). They are **not committed** —
see `.gitignore` and [ADR-0009](../../../docs/decisions/0009-public-repo-private-eval-data.md):
they are the owner's own household recordings, and a public repository is
the wrong place for them.

`voice_v1.jsonl` itself is committed and describes every case's *expected*
answer; only the audio behind each `id` is missing on a fresh clone. Running
`python -m evals.run --modality voice` without these files fails on a
missing file, which is the correct, honest failure — not a reason to commit
one.

## Why `.oga`

Every file here is `.oga` — OGG container, Opus codec — because that is the
exact format Telegram hands the bot for a real voice note (`GetFile` +
download; see `finbot.adapters.telegram.audio`). Recording or pulling a case
in that same format is what makes it representative of what the bot actually
receives, not an approximation of it.

**What happens to it next differs by path, and this is worth knowing before
you hand-label anything:**

- The bot (`finbot.adapters.telegram.audio.fetch_and_convert`) always
  converts `.oga` to mp3 with `ffmpeg` before sending it to the model —
  never the original bytes (ADR-0015).
- `python -m evals.run --modality voice` (`evals/scoring.py`'s
  `load_voice_golden_cases`) currently does **not** do the same conversion:
  it reads this directory's `.oga` bytes as-is and sends them to the model
  labelled `"format": "mp3"` (`finbot.core.extraction.voice.AUDIO_FORMAT`),
  unconditionally. That is a real gap between what this eval measures and
  what production actually sends — not something this README can paper
  over. Keep recording `.oga` here regardless (it is still the correct
  source format, and fixing the runner is the right side to close the gap
  on, not this directory); just do not treat a passing voice eval as proof
  that the exact production audio path was exercised until someone closes
  that gap in `evals/run.py`.

## Producing a set

Two ways to get audio here, in the order to try them:

1. **Record fresh.** Say the case's content in your own voice, exactly as
   you would dictate a real expense — Ukrainian, Russian, or a mix, the same
   shorthand you actually use. Send each as a voice note to the bot in the
   real household chat, or record one directly (Telegram's own voice-message
   recorder, or any tool that produces OGG/Opus — the bot converts on
   arrival regardless, so the exact source format does not matter). Save each
   under the filename `voice_v1.jsonl` expects for that case's `id`.

2. **Pull specific past voice notes.** If you already sent voice messages to
   the bot before this set existed,
   [ADR-0016](../../../docs/decisions/0016-narrow-exception-for-owner-named-voice-samples.md)
   permits pulling *named* ones — never a bulk export, and never to a path
   inside this repository. `evals/pull_voice_samples.py` has two ways to name
   them; use whichever matches what you have access to:

   **From a laptop, no database access (the common case — `infra/docker-
   compose.yml` publishes no port for Postgres, so nothing outside the
   server's own compose network can reach it):**

   1. On the server, find the `file_id`s you want with a read-only query run
      *inside* the running Postgres container:

      ```bash
      docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "select id, file_id from messages where kind = 'voice' order by id desc limit 20;"
      ```

   2. From your laptop (needs `TELEGRAM_BOT_TOKEN` and network access to
      Telegram — nothing else):

      ```bash
      python -m evals.pull_voice_samples --file-ids AwACAgIAAxkBAAI... --out ~/finbot-voice-samples
      ```

   **From a machine that does have `DATABASE_URL` and network access to
   Postgres** (e.g. an SSH tunnel to the server — nothing in this deployment
   exposes that by default):

   ```bash
   python -m evals.pull_voice_samples --message-ids 1042,1043 --out ~/finbot-voice-samples
   ```

   Either flag: `--out` must resolve outside this repository — the script
   refuses otherwise. Move the ones you want to keep from
   `~/finbot-voice-samples` into *this* directory by hand, renamed to match a
   case `id` in `voice_v1.jsonl` (the script itself never names a file after
   a raw `file_id`, since that string is a capability token, not a label).

## What to write into `voice_v1.jsonl`

One JSON object per line, alongside the audio file it describes:

```json
{
  "id": "multi-02",
  "audio": "multi-02.oga",
  "expected": [
    {"item": "хліб", "amount": "50.00", "category": "groceries", "occurred_offset_days": 0},
    {"item": "таксі", "amount": "200.00", "category": "transport", "occurred_offset_days": 0}
  ],
  "expected_transcript_contains": ["хліб", "таксі"]
}
```

- **`id`** — matches the audio filename's stem (`multi-02` ↔ `multi-02.oga`).
- **`audio`** — the filename in this directory, read relative to `--audio-dir`
  (default: this directory).
- **`expected`** — same shape as the text golden set (`../text_v1.jsonl`):
  one entry per expense actually said, `amount` as a JSON *string* (never a
  bare number — see `evals/README.md`'s Case format section for why),
  `category` one of the thirteen slugs in
  `finbot.core.categories.catalog.CATALOG` (`groceries`, `dining_out`,
  `transport`, `housing`, `health`, `household`, `clothing`,
  `entertainment`, `subscriptions`, `gifts`, `pets`, `hookah`, `other`), and
  `occurred_offset_days` relative to the day the runner executes (`0` today,
  `-1` yesterday) — never an absolute date. Zero expenses said → `expected: []`.
- **`expected_transcript_contains`** — a list of substrings that must all
  appear in the model's own transcript, case-insensitively. Never empty
  (`load_voice_golden_cases` rejects that). Pick words you actually said that
  a correct transcript could not omit — a number, a distinctive item name —
  not the whole sentence.

After adding or changing a case here, listen back to it once and confirm
`expected_transcript_contains` names substrings that really are in what was
said — a golden case with an unlistenable-to expectation is worse than no
case at all.
