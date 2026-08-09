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
