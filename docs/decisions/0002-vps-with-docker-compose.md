# ADR-0002 — Self-hosted VPS with docker compose

**Date:** 2026-08-09 · **Status:** accepted

## Context

The bot needs to run continuously. Options: the owner's always-on laptop, a cheap VPS,
or a PaaS such as Railway, Fly.io or Render.

Long polling removes the usual constraint here — no public IP, domain or TLS certificate
is required, so a home machine is technically viable.

## Decision

A Hetzner VPS (~€4–6/month) running `docker compose` with `bot` and `postgres`.

## Rationale

- A laptop sleeps, travels and reboots, and the other user is writing expenses while it
  does. Availability matters because a second person depends on it.
- Docker and Linux are skills the owner explicitly wants; a VPS exercises them on a real
  system instead of a tutorial.
- A PaaS would abstract away exactly the layer worth learning, and free tiers idle
  containers, which breaks long polling.

## Consequences

- Backups, updates and basic server hygiene are now the owner's problem.
- Local LLMs are out of reach on this hardware. All inference goes through an API; local
  models remain a separate experiment on the owner's Mac.

## Rejected

**Home machine** — free, but availability and zero infrastructure learning.
**PaaS** — fastest to deploy, hides the layer that is the point, idles free containers.
