"""Telegram-facing text. `parse_mode=None` throughout, deliberately: `item`
originates from a model reading user input, and with no parse mode there is
nothing to escape and no formatting-injection surface for it to exploit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finbot.core.categories.catalog import ALL_CATEGORIES, SLUGS
from finbot.core.extraction.bank import SKIPPED_KINDS
from finbot.core.extraction.pipeline import BankSummary
from finbot.core.extraction.schema import BankRowKind
from finbot.core.reporting import Report

# Ukrainian labels, presentation only — the stable identifier is the slug
# (finbot.core.categories.catalog). Asserted below to cover exactly SLUGS —
# every category in the database, model-choosable or code-assigned — so a
# further category cannot ship label-less.
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
    "cash": "Готівка",
    "transfers": "Перекази",
}

if set(CATEGORY_LABELS) != SLUGS:
    msg = "CATEGORY_LABELS must cover exactly the catalog's slugs"
    raise AssertionError(msg)

# ALL_CATEGORIES, not CATALOG: a cash-withdrawal row is filed under a
# code-assigned slug (ADR-0020) and `render_confirmation` looks its emoji up
# here, so a map built from the model-choosable subset alone would raise
# `KeyError` while rendering a real screenshot's confirmation.
_EMOJI_BY_SLUG: dict[str, str] = {category.slug: category.emoji for category in ALL_CATEGORIES}

NO_EXPENSE_REPLY = (
    "Не зрозумів, що саме витрачено. Напиши, будь ласка, що і скільки — "
    "наприклад: «хліб 50, таксі 200»."
)
# UNSUPPORTED_MODALITY_REPLY is gone (docs/plans/stage-2_5-bank-screenshots.md,
# Reality check #1): a photo is no longer unsupported, and
# `handlers.py`'s `@router.message(F.photo)` — the only sender of this
# reply — is removed in the same commit that flips `repo.messages.
# _initial_status`'s PHOTO branch to PENDING. Keeping the constant around
# unused would be a dead string with no caller to catch drifting from it.
# Sent when a voice message arrives while `MODEL_VOICE` is unset
# (core.extraction.pipeline, docs/roadmap.md Stage 2) — the owner sets it
# after running the voice eval.
VOICE_NOT_CONFIGURED_REPLY = (
    "Голосові повідомлення ще не налаштовані. Напиши, будь ласка, текстом — "
    "наприклад: «хліб 50, таксі 200»."
)
# Mirrors VOICE_NOT_CONFIGURED_REPLY: sent when a photo arrives while
# `MODEL_VISION` is unset (core.extraction.pipeline.VISION_NOT_CONFIGURED_
# ERROR, docs/plans/stage-2_5-bank-screenshots.md R10) — the owner sets
# `MODEL_VISION` after running the bank-screenshot eval.
VISION_NOT_CONFIGURED_REPLY = (
    "Розпізнавання скріншотів ще не налаштоване. Напиши, будь ласка, текстом — "
    "наприклад: «хліб 50, таксі 200»."
)
# `render_bank_note` falls back to this single line instead of the usual
# multi-line note (Approach E's `is_transaction_feed` guard) whenever
# `BankSummary` carries nothing to report at all: no row was written, no row
# was skipped for a named reason, nothing was already recorded, and no
# manual collision was found. That is exactly what a photographed receipt,
# a screenshot of something else entirely, or a genuinely empty feed all
# produce — from the reply's point of view there is nothing to tell them
# apart, so no attempt is made to.
NOT_A_BANK_FEED_REPLY = (
    "Не схоже на виписку банку — нічого не записав. Якщо це вона, спробуй, "
    "будь ласка, чіткіший скріншот."
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
    "Скріншот виписки банку теж підійде — я запишу витрати з нього і покажу, "
    "що саме пропустив (скарбничка, переказ на свою картку, надходження).\n\n"
    "Знята готівка і переказ комусь ідуть у «Готівка» і «Перекази» — стрічка не "
    "каже, куди ці гроші пішли, тому ✏️ перекладе їх у потрібну категорію.\n\n"
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


# Presentation order and Ukrainian labels for `BankPlan.skipped_by_kind`
# (Approach D2's note) — deliberately not `BankRowKind`'s own declaration
# order: "money that's still ours" (savings, own_transfer) reads before
# "money that arrived" (income). `transfer_out` and `cash_withdrawal` are
# absent because they are no longer skipped — ADR-0020 writes them.
_SKIP_LABELS: dict[BankRowKind, str] = {
    BankRowKind.SAVINGS: "скарбничка",
    BankRowKind.OWN_TRANSFER: "переказ собі",
    BankRowKind.INCOME: "надходження",
}
_SKIP_ORDER: tuple[BankRowKind, ...] = (
    BankRowKind.SAVINGS,
    BankRowKind.OWN_TRANSFER,
    BankRowKind.INCOME,
)

# `_SKIP_LABELS[kind]` is an unguarded subscript inside `render_bank_note`, so
# a kind that becomes skipped without gaining a label here raises `KeyError`
# mid-reply — on a real screenshot, in production, after the money was already
# written. Both directions are checked: a label for a kind that is no longer
# skipped is dead presentation that would never print.
if set(_SKIP_LABELS) != set(SKIPPED_KINDS):
    msg = "_SKIP_LABELS must cover exactly bank.SKIPPED_KINDS"
    raise AssertionError(msg)
if set(_SKIP_ORDER) != set(_SKIP_LABELS):
    msg = "_SKIP_ORDER must cover exactly _SKIP_LABELS"
    raise AssertionError(msg)

# The plan's own rule (## Chosen approach): warnings capped at five, with a
# trailing "and N more" line past that — a worst-case 20-row feed would
# otherwise print one ⚠️ line per manual collision and risk Telegram's
# 4096-character limit the same way an unbounded voice transcript could (see
# `transcript_line`).
_MAX_COLLISION_WARNINGS = 5

# Same reasoning as _MAX_COLLISION_WARNINGS, applied to the duplicate list:
# re-sending a month-long feed can suppress dozens of rows, and naming every
# one of them would push a single note past Telegram's 4096-character limit.
_MAX_DUPLICATE_LINES = 5


def render_bank_note(summary: BankSummary, *, anchor: date, written: int) -> str:
    """The note a bank screenshot always gets first (Approach D2), sent as
    its own message and never edited again — unlike the confirmation that
    may follow, which `handlers._rerender_group` rebuilds from scratch on
    every ✏️/🗑 tap and therefore cannot carry anything modality-specific
    (Reality check #2). `anchor` is `message.created_at` in the household's
    timezone (Approach B): the one thing every relative date header in this
    screenshot was resolved against, always stated so a wrong guess is
    visible rather than silent (R5, R8).

    Falls back to `NOT_A_BANK_FEED_REPLY` when `summary` has nothing to
    report at all — see that constant's own docstring for why that reading
    covers both a genuinely empty feed and `is_transaction_feed: false`
    without needing to tell them apart.

    **`written` is a parameter, never `len(summary.plan.writes)`.** It was
    the latter once, and that was a live bug: `plan.writes` is what the
    classifier *planned*, while the confirmation message listing the rows is
    built from `ExtractionOutcome.expense_ids` — what the keyed insert
    *actually* wrote. On a re-sent screenshot every planned write is
    rejected as a duplicate, so the note promised "Записав: 2 (нижче)" and
    `runner._send_bank_reply` then returned without sending anything below
    it. The promise and the payload have to come from the same number, and
    the only number that is a fact is the one the caller passes here.
    """
    plan = summary.plan
    skip_parts = [
        f"{_SKIP_LABELS[kind]} {plan.skipped_by_kind[kind]}"
        for kind in _SKIP_ORDER
        if plan.skipped_by_kind.get(kind)
    ]
    has_anything_to_report = (
        written
        or skip_parts
        or plan.cut_off
        or plan.unresolved_date
        or plan.bad_amount
        or plan.unclassified
        or summary.duplicates
        or summary.manual_collisions
    )
    if not has_anything_to_report:
        return NOT_A_BANK_FEED_REPLY

    lines = [f"🧾 Скріншот за {anchor:%d.%m} — дати рахував від цього дня."]
    if written:
        lines.append(f"Записав: {written} (нижче).")
    if skip_parts:
        lines.append(f"Пропустив: {', '.join(skip_parts)}.")
    if plan.cut_off:
        lines.append(f"Обрізано на краю: {plan.cut_off} — не вгадував.")
    if summary.duplicates:
        lines.append(f"Вже було: {len(summary.duplicates)}.")
        shown_duplicates = summary.duplicates[:_MAX_DUPLICATE_LINES]
        lines.extend(
            f"  · {draft.item} — {_amount_text(draft.amount)} ₴ за {draft.occurred_at:%d.%m}"
            if draft.occurred_at is not None
            else f"  · {draft.item} — {_amount_text(draft.amount)} ₴"
            for draft in shown_duplicates
        )
        remaining_duplicates = len(summary.duplicates) - len(shown_duplicates)
        if remaining_duplicates > 0:
            lines.append(f"  · …і ще {remaining_duplicates}.")
    if plan.unresolved_date:
        lines.append(f"Не зрозумів дату: {plan.unresolved_date}.")
    if plan.bad_amount:
        lines.append(f"Не розібрав суму: {plan.bad_amount}.")
    if plan.unclassified:
        lines.append(f"Не визначив тип: {plan.unclassified}.")

    shown_collisions = summary.manual_collisions[:_MAX_COLLISION_WARNINGS]
    lines.extend(
        f"⚠️ Можливий дубль: «{collision.item}» {_amount_text(collision.amount)} за "
        f"{collision.occurred_at:%d.%m} уже записано вручну."
        for collision in shown_collisions
    )
    remaining_collisions = len(summary.manual_collisions) - len(shown_collisions)
    if remaining_collisions > 0:
        lines.append(f"…і ще {remaining_collisions}.")

    return "\n".join(lines)


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
