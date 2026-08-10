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
UNSUPPORTED_MODALITY_REPLY = "Поки що я розумію лише текст. Голос і фото — скоро."
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
        # docstring.
        return f"~ {emoji} {line.item} — {_amount_text(line.amount)} ₴ (видалено)"
    suffix = _date_suffix(line.occurred_at, today=today)
    return f"{line.index}. {emoji} {line.item} — {_amount_text(line.amount)} ₴{suffix}"


def render_confirmation(lines: Sequence[ConfirmationLine], *, today: date) -> str:
    """ADR-0007: one message per incoming message. A single expense gets no
    number and no total line; several get a numbered list plus `Разом`,
    computed over the still-active lines only.
    """
    if not lines:
        msg = "render_confirmation requires at least one line; use NO_EXPENSE_REPLY for zero"
        raise ValueError(msg)

    if len(lines) == 1 and not lines[0].deleted:
        line = lines[0]
        emoji = _EMOJI_BY_SLUG[line.category_slug]
        suffix = _date_suffix(line.occurred_at, today=today)
        return f"✅ {emoji} {line.item} — {_amount_text(line.amount)} ₴{suffix}"

    header = f"✅ Записав {len(lines)}:"
    rows = [_row_text(line, today=today) for line in lines]
    active_total = sum((line.amount for line in lines if not line.deleted), Decimal("0"))
    return "\n".join([header, *rows, f"Разом: {_amount_text(active_total)} ₴"])


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
