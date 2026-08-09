# ADR-0004 — OpenRouter as the single gateway, one model per modality

**Date:** 2026-08-09 · **Status:** accepted

## Context

Three modalities need three different capabilities: speech, image understanding, and
cheap reliable text-to-JSON. Going direct to each provider means several accounts and
keys. OpenCode Zen was considered first but lists no speech-to-text models at all.

## Decision

OpenRouter as the single gateway. A separate model per modality, selected by evaluation
rather than intuition, with model IDs in environment configuration and never hard-coded.

Voice is handled by sending audio inline to a multimodal model that returns both the
transcript and the expenses in one call:

```json
{ "transcript": "...", "expenses": [ ... ] }
```

## Rationale

- OpenRouter accepts audio as base64 `input_audio` on the standard chat completions API,
  so no separate transcription provider or key is needed.
- One key, one interface, and swapping a model becomes a configuration change.
- A fallback model list gives graceful degradation for free, instead of a hand-rolled
  retry wrapper.
- Per-request cost accounting can be recorded alongside each expense.

Keeping the transcript as a schema field preserves the debuggable intermediate artefact
that a collapsed pipeline would otherwise lose.

## Consequences

- A gateway may not pass through provider-specific features; image support in particular
  must be verified with a spike before Stage 4 is planned around it.
- Providers that log or train on request data must be excluded in account and request
  settings before the first real message. This is household financial data.
- Telegram sends voice as OGG/Opus while the documented format is OGG Vorbis. `ffmpeg`
  conversion stays in the image as the fallback path.

## Rejected

**One multimodal model for everything** — fewer moving parts, but hides the cost and
accuracy differences that this project exists to observe.
**Direct provider SDKs** — more control, several keys, and swapping models becomes a
code change.
**A dedicated STT model plus a separate text model** — likely more accurate on mixed
Ukrainian/Russian speech. Not rejected on principle: this is the first question the
evaluation harness is meant to answer.
