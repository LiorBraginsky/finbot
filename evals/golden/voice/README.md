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
   inside this repository:

   1. Find the message ids you want, e.g.
      `select id, created_at from messages where kind = 'voice' order by id desc;`
   2. `python -m evals.pull_voice_samples --message-ids 1042,1043 --out ~/finbot-voice-samples`
      (`--out` must resolve outside this repository — the script refuses
      otherwise; needs `TELEGRAM_BOT_TOKEN` and `DATABASE_URL`).
   3. Move the ones you want to keep from `~/finbot-voice-samples` into
      *this* directory by hand, renamed to match a case `id` in
      `voice_v1.jsonl`, and write that case's `expected*` fields from what
      you actually said.

Either way: after adding or changing a case here, listen back to it once and
confirm `expected_transcript_contains` names substrings that really are in
what was said — a golden case with an unlistenable-to expectation is worse
than no case at all.
