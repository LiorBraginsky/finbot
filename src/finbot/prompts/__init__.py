"""Versioned prompt files. A prompt change means a new file and a new
`PROMPT_VERSION_*` string — never editing a shipped version in place, because
`extractions.prompt_version` is how a regression gets traced back to the
prompt that produced it.

Rendered with `string.Template.substitute`, not `str.format`: the prompt
contains no JSON braces today, but any future example that adds one would
break `str.format` silently. `substitute` (not `safe_substitute`) raises
loudly on a missing placeholder instead of shipping `$placeholder` verbatim
into a real prompt.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from string import Template
from typing import Final

from finbot.core.categories.catalog import CategorySpec

_DIR = Path(__file__).parent
PROMPT_VERSION_TEXT: Final[str] = "extract_text.v2"
PROMPT_VERSION_VOICE: Final[str] = "extract_voice.v1"
PROMPT_VERSION_BANK: Final[str] = "extract_bank.v3"


def load(version: str) -> str:
    """Read the raw Markdown template for a given prompt version.

    Raises FileNotFoundError for an unknown version — there is no silent
    fallback to a different prompt version.
    """
    return (_DIR / f"{version}.md").read_text(encoding="utf-8")


def render_text_prompt(*, today: date, catalog: Sequence[CategorySpec]) -> str:
    """Render `extract_text.v1.md` with today's date and the category list."""
    return Template(load(PROMPT_VERSION_TEXT)).substitute(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        categories="\n".join(f"- {c.slug} {c.emoji} — {c.description}" for c in catalog),
    )


def render_voice_prompt(*, today: date, catalog: Sequence[CategorySpec]) -> str:
    """Render `extract_voice.v1.md` with today's date and the category list —
    mirrors `render_text_prompt` exactly (docs/roadmap.md Stage 2).

    **Deliberately still `v1`, unlike text and bank.** The wire schema
    requires `suggested_category` on every modality (strict mode requires
    every property to be listed in `required`), but this prompt never asks for
    it, so the model returns `null` and a voice note proposes no categories
    (ADR-0021). That is a measurement, not an oversight: adding the rule here
    cost `transcript_ok` 10/10 -> 8/10 at n=10 on the golden set, with the
    model rendering a Russian word in Ukrainian, while every extraction metric
    stayed perfect. Reordering the rule and restating the transcript rule
    after it recovered 9/10, not 10/10. Voice is the least-used channel and
    `other` accumulates from screenshots, so the rule lives where it pays for
    itself.
    """
    return Template(load(PROMPT_VERSION_VOICE)).substitute(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        categories="\n".join(f"- {c.slug} {c.emoji} — {c.description}" for c in catalog),
    )


def render_bank_prompt(*, catalog: Sequence[CategorySpec]) -> str:
    """Render `extract_bank.v3.md` with only the category list — deliberately
    no `today`/`weekday` (docs/plans/stage-2_5-bank-screenshots.md, Approach
    B, R5): the model must not resolve a date and cannot be asked to if it
    does not know today. `bank_dates.resolve` owns the calendar instead.
    """
    return Template(load(PROMPT_VERSION_BANK)).substitute(
        categories="\n".join(f"- {c.slug} {c.emoji} — {c.description}" for c in catalog),
    )
