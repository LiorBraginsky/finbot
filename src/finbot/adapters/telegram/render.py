"""Telegram-facing text. `parse_mode=None` throughout, deliberately: `item`
originates from a model reading user input, and with no parse mode there is
nothing to escape and no formatting-injection surface for it to exploit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finbot.core.categories.catalog import CATALOG, SLUGS
from finbot.core.reporting import Report

# Ukrainian labels, presentation only — the stable identifier is the slug
# (finbot.core.categories.catalog.CATALOG). Asserted below to cover exactly
# SLUGS, so a fourteenth category cannot ship label-less.
CATEGORY_LABELS: dict[str, str] = {
    "groceries": "Продукти",
    "dining_out": "Кафе і доставка",
    "transport": "Транспорт",
    "housing": "Житло",
    "health": "Здоровʼя",
    "household": "Дім і побут",
    "clothing": "Одяг",
    "entertainment": "Дозвілля",
    "subscriptions": "Підписки",
    "gifts": "Подарунки і донати",
    "pets": "Тварини",
    "hookah": "Кальян",
    "other": "Інше",
}

if set(CATEGORY_LABELS) != SLUGS:
    msg = "CATEGORY_LABELS must cover exactly the catalog's slugs"
    raise AssertionError(msg)

_EMOJI_BY_SLUG: dict[str, str] = {category.slug: category.emoji for category in CATALOG}

NO_EXPENSE_REPLY = (
    "Не зрозумів, що саме витрачено. Напиши, будь ласка, що і скільки — "
    "наприклад: «хліб 50, таксі 200»."
)
UNSUPPORTED_MODALITY_REPLY = "Поки що я розумію лише текст і голос. Фото — скоро."
# Sent when a voice message arrives while `MODEL_VOICE` is unset
# (core.extraction.pipeline, docs/roadmap.md Stage 2) — the owner sets it
# after running the voice eval.
VOICE_NOT_CONFIGURED_REPLY = (
    "Голосові повідомлення ще не налаштовані. Напиши, будь ласка, текстом — "
    "наприклад: «хліб 50, таксі 200»."
)
CALLBACK_FAILURE_REPLY = "Не вдалося, спробуй ще"
EMPTY_REPORT_REPLY = "Нічого не записано за цей період."
# Sent once a message's processing rounds are exhausted (messages.status ->
# 'failed', repo/messages.py::schedule_retry) — not in the plan's own text,
# which specifies only the zero-expenses reply; a provider outage that never
# recovers must still tell the user something rather than staying silent
# forever (spec §7's "never stays silent" applies here too).
PROCESSING_FAILED_REPLY = (
    "Не вдалося розпізнати витрату з цього повідомлення. Спробуй, будь ласка, написати ще раз."
)
# Sent when `core.extraction.currency.detect_foreign_currency` fires
# (pipeline.py, before any model call) — currencies are Stage 1.5; until
# then this refuses rather than silently recording a foreign amount as UAH.
FOREIGN_CURRENCY_REPLY = (
    "Поки що я розумію лише гривні. Валюти зʼявляться скоро — а це запиши в гривнях, будь ласка."
)


def voice_too_long_reply(max_seconds: int) -> str:
    """Sent when `message.duration_seconds` exceeds `Settings.
    max_voice_seconds` (`core.extraction.pipeline`, docs/roadmap.md Stage 2,
    spec §7) — a function, not a constant, so the number in the message
    always matches whatever the deployment is actually configured with.
    """
    return (
        f"Це голосове задовге — я розумію нотатки до {max_seconds} секунд. "
        "Спробуй, будь ласка, коротше або текстом."
    )


# /help and the catch-all for an unrecognised "/command" (handlers.py) both
# answer with this — a command Telegram sent to a handler that doesn't exist
# must never be silence (docs/roadmap.md's Stage 1 hardening).
HELP_TEXT = (
    "Я записую витрати з тексту — просто напиши, що і скільки, "
    "наприклад: «хліб 50, таксі 200».\n\n"
    "Команди:\n"
    "/day — витрати за сьогодні\n"
    "/week — за тиждень\n"
    "/month — за місяць\n\n"
    "Помилився? Під моїм повідомленням є кнопки: ✏️ змінює категорію, "
    "🗑 видаляє запис.\n\n"
    "Поки що я розумію лише гривні."
)


@dataclass(frozen=True)
class ConfirmationLine:
    """One row of a confirmation message.

    `index` is fixed at the row's original position (1-based) among *all*
    the message's expenses and is never renumbered after a sibling is
    deleted — it is what the ✏️ N / 🗑 N buttons refer to
    (`keyboards.confirmation_keyboard`), and renumbering would desync the
    text from the buttons still attached to it. `expense_id` is what those
    buttons actually pack into their callback data.
    """

    index: int
    expense_id: int
    item: str
    amount: Decimal
    category_slug: str
    occurred_at: date
    deleted: bool = False


def _amount_text(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _date_suffix(occurred_at: date, *, today: date) -> str:
    return "" if occurred_at == today else f" ({occurred_at:%d.%m})"


def _row_text(line: ConfirmationLine, *, today: date) -> str:
    emoji = _EMOJI_BY_SLUG[line.category_slug]
    if line.deleted:
        # Deleted expenses lose their buttons (handlers.py) and, with them,
        # the number that pointed at those buttons — see ConfirmationLine's
        # docstring. "✖️", not "~": parse_mode is None throughout (see this
        # module's docstring), so a "~" intended as strikethrough rendered
        # as a literal tilde instead — a glitch, not a deletion marker.
        return f"✖️ {emoji} {line.item} — {_amount_text(line.amount)} ₴ (видалено)"
    suffix = _date_suffix(line.occurred_at, today=today)
    return f"{line.index}. {emoji} {line.item} — {_amount_text(line.amount)} ₴{suffix}"


_TRANSCRIPT_MAX_LENGTH = 500


def transcript_line(transcript: str) -> str:
    """The 🎤 «...» line a voice confirmation shows above the numbered list
    (docs/roadmap.md Stage 2) — also reused for the zero-expenses
    clarification (`runner.py`), so a household member always sees what the
    bot heard, not only what it managed to extract from it.

    Truncated to `_TRANSCRIPT_MAX_LENGTH` characters, with a trailing "…" —
    the same truncate-don't-reject choice `core.extraction.schema.
    ExpenseDraft._clean_item` makes for an over-long item, applied here
    because Telegram's own 4096-character message limit is real: an
    unbounded transcript plus a numbered list of several expenses could
    exceed it, `bot.send_message` would then raise, and — because
    `extract_and_store` already committed the expenses before this ever
    runs (write, then reply, ADR-0007) — the household would see no
    confirmation at all for expenses that were, in fact, recorded.
    `messages.raw_text` keeps the untruncated transcript regardless; only
    what is shown here is shortened.
    """
    shown = (
        transcript
        if len(transcript) <= _TRANSCRIPT_MAX_LENGTH
        else f"{transcript[:_TRANSCRIPT_MAX_LENGTH]}…"
    )
    return f"🎤 «{shown}»"


def render_confirmation(
    lines: Sequence[ConfirmationLine], *, today: date, transcript: str | None = None
) -> str:
    """ADR-0007: one message per incoming message. A single expense gets no
    number and no total line; several get a numbered list plus `Разом`,
    computed over the still-active lines only. `transcript` is voice only
    (docs/roadmap.md Stage 2): when given, it is shown as its own line above
    everything else, produced by neither of the two shapes below.
    """
    if not lines:
        msg = "render_confirmation requires at least one line; use NO_EXPENSE_REPLY for zero"
        raise ValueError(msg)

    if len(lines) == 1 and not lines[0].deleted:
        line = lines[0]
        emoji = _EMOJI_BY_SLUG[line.category_slug]
        suffix = _date_suffix(line.occurred_at, today=today)
        body = f"✅ {emoji} {line.item} — {_amount_text(line.amount)} ₴{suffix}"
    else:
        header = f"✅ Записав {len(lines)}:"
        rows = [_row_text(line, today=today) for line in lines]
        active_total = sum((line.amount for line in lines if not line.deleted), Decimal("0"))
        body = "\n".join([header, *rows, f"Разом: {_amount_text(active_total)} ₴"])

    return body if transcript is None else f"{transcript_line(transcript)}\n{body}"


def render_report(report: Report) -> str:
    if not report.lines:
        return EMPTY_REPORT_REPLY

    header = f"📊 {report.date_from:%d.%m}–{report.date_to:%d.%m}:"
    rows = [
        f"{_EMOJI_BY_SLUG[line.category_slug]} {CATEGORY_LABELS[line.category_slug]} — "
        f"{_amount_text(line.total)} ₴ ({line.count})"
        for line in report.lines
    ]
    return "\n".join([header, *rows, f"Разом: {_amount_text(report.total)} ₴"])
