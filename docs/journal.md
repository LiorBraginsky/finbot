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

Current state is *derived*, not stored: stage statuses live in `docs/roadmap.md`, and
the most recent entry below says where work stopped. There is no STATE file to fall out
of date.

---

## 2026-08-09 · stage 0 · lior
**Did:** design settled end to end — hosting, storage, modality routing, error taxonomy, eval approach; repo scaffolded with vision, roadmap, spec and ten ADRs
**Hit:** OpenCode Zen has no speech-to-text models; switched to OpenRouter, which accepts audio inline and collapses transcription and extraction into one call
**Next:** Stage 0 — VPS, docker compose, long polling, raw message persistence, no LLM
**Open:** whether one multimodal call beats a dedicated STT model on Ukrainian mixed speech — first real eval
