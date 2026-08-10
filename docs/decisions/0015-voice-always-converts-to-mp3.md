# ADR-0015 — Every voice note is converted to mp3, unconditionally; no OGG/Opus fallback path

**Date:** 2026-08-10 · **Status:** accepted
**Amends:** [ADR-0004](0004-openrouter-and-model-routing.md), whose Consequences section
said `ffmpeg` conversion "stays in the image as the fallback path" — read on its own,
that implies a primary path that tries the original OGG/Opus first. This record replaces
that implication with what Stage 2 actually built and says why.

## Context

Telegram voice notes arrive as OGG/Opus. OpenRouter's documented audio-input formats
(`docs/roadmap.md`'s own Reality check against the live docs) are `wav`, `mp3`, `aiff`,
`aac`, `ogg` (Vorbis), `flac`, `m4a`, `pcm16`, `pcm24` — "`ogg`" there names the Vorbis
codec in an Ogg container, not Opus-in-Ogg, which is a different codec sharing the same
container format. `ffmpeg` was already in the image from Stage 0, specifically anticipating
this mismatch.

ADR-0004's Consequences section named the fix — "`ffmpeg` conversion stays in the image
as the fallback path" — without specifying what the *primary* path was. Read literally,
"fallback" implies trying the original bytes against a provider first and converting only
on failure. Stage 2 did not build that.

## Decision

**Every voice note is converted to mp3 before it is ever sent to a model. There is no
attempt to send the original OGG/Opus first.** `adapters/telegram/audio.py`'s
`fetch_and_convert` downloads to memory and pipes the result through `ffmpeg` — stdin to
stdout, no temp files — unconditionally, every time.

## Rationale

A conditional fallback buys nothing here and costs a second failure mode:

- **The mismatch is not provider-specific.** OpenRouter's documented format list is the
  same for every request; a provider that would accept raw Opus-in-Ogg despite the
  mismatch is not something this project can detect except by trying and billing for the
  attempt.
- **A fallback still has to build the same conversion path.** The `ffmpeg` invocation
  this ADR describes would exist either way; a "try original, then convert" design adds
  a second code path around it, not less code.
- **Two paths mean two failure surfaces in production, not one.** A provider that
  silently degrades on unrecognised audio (transcribing garbage instead of rejecting the
  request outright) would show up as a *quality* regression on the primary path — measured
  in `evals/`, days later, indistinguishable from a bad model — rather than as a `format
  error the code already handles`. One path removes that ambiguity entirely.
- **The cost is small and fixed.** `ffmpeg` decoding Opus and re-encoding to mp3 for a
  voice note well under `MAX_VOICE_SECONDS` (120s) costs on the order of 100ms, paid on
  every voice message regardless. That is cheaper than the debugging cost of a fallback
  path's failure mode showing up rarely and only in production.

## Consequences

- `adapters/telegram/audio.py` has exactly one path from Telegram bytes to what the model
  receives, which is what its own tests exercise — no second, untested branch for "the
  original format worked after all."
- If OpenRouter or a given provider ever documents native Opus-in-Ogg support, adopting it
  would be a new ADR, not a quiet change to this one: the trade-off above (one failure
  surface vs. a small fixed cost) would need to be re-made explicitly, not assumed to still
  hold.
- ADR-0004's own Consequences line is superseded by this one; ADR-0004's Decision and
  Rationale sections (OpenRouter as the gateway, the `{transcript, expenses[]}` shape) are
  untouched.
- **A repair round re-sends the whole base64 audio, not just a corrected prompt.**
  `core.extraction.pipeline`'s shared repair loop (`_run_extraction_round`) treats voice
  exactly like text: `max_extraction_attempts × max_message_attempts` retries, up to ten
  for the settings Stage 1 chose. For text that is ten cheap text calls; for voice it is
  up to ten billed *audio* calls for one note, since each attempt — including every
  repair — carries the original audio again, not only the growing text conversation.
  With strict structured output the repair path is expected to be rare, so this is left
  as-is rather than shrinking the attempt budget or building an audio-specific repair
  path — but it is a real cost multiplier the owner should see before picking a
  per-minute- or per-second-priced audio model, not discover from a bill.

## Rejected

**Try the original OGG/Opus first, convert only on a provider error** — the literal reading
of ADR-0004's "fallback" wording. Rejected for the reasons above: it does not remove the
`ffmpeg` code path, it adds a second one, and the failure mode it introduces (silent
quality degradation on unrecognised audio) is harder to detect than the fixed conversion
cost it would occasionally save.
