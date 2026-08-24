"""Command and callback handlers.

Persistence for plain text already happened in middleware by the time these
run — see `middlewares.py`. Plain expense text has **no handler here**: the
inbox middleware persists it and `runner.py`'s drain loop replies; aiogram
logging it "not handled" at this layer is correct and expected. Voice is the
same, from Stage 2, and photo is the same again from Stage 2.5
(docs/roadmap.md): a voice message or a bank screenshot becomes a PENDING
row just like text, and the drain loop is what eventually replies — see
`repo.messages._initial_status` and `core.extraction.pipeline`. There is
**no `@router.message(F.photo)` handler here any more**
(docs/plans/stage-2_5-bank-screenshots.md, Reality check #1): it used to
answer a photo inline with `UNSUPPORTED_MODALITY_REPLY`, and removing it is
the other half of the same commit that flips `_initial_status`'s PHOTO
branch to PENDING — leaving either change without the other would send two
contradictory answers to one screenshot.

Everything registered here answers **inline**: fast, no LLM, no `messages`
row. A tap on ✏️/🗑/a category button is not written to `messages` — that
table is "what arrived to be turned into expenses" (ADR-0006), and the
eval/training set is built from `messages` + `expenses` + `corrections`
(ADR-0009); a tap there would be noise with no `kind` value of its own. The
record of a tap is the `corrections` row it produces.

Every callback handler always answers the query, including on its failure
path (wrapped in `try/except Exception`, replying `CALLBACK_FAILURE_REPLY`)
— otherwise the tapping client spins for 30 seconds — and this is also the
"a handler bug must not block the queue" lane: the exception is logged and
swallowed here, well before `polling.run_polling` ever sees it, so a repeat
tap costs the user nothing.

`build_router` is a factory, not a module-level singleton: an aiogram
`Router` can only ever be attached to one `Dispatcher` (attaching it twice
raises `RuntimeError`), and `build_dispatcher` (see main.py) is called once
per test as well as once per process — each call needs its own instance.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    MaybeInaccessibleMessageUnion,
    Message,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.adapters.telegram.callbacks import ExpenseAction, MessageAction, SetCategory
from finbot.adapters.telegram.keyboards import category_keyboard, confirmation_keyboard
from finbot.adapters.telegram.render import (
    CALLBACK_FAILURE_REPLY,
    HELP_TEXT,
    ConfirmationLine,
    render_confirmation,
    render_report,
)
from finbot.core.categories.catalog import CATALOG
from finbot.core.models import MessageKind
from finbot.core.reporting.periods import Period
from finbot.core.reporting.periods import resolve as resolve_period
from finbot.repo import categories, corrections, expenses, messages, reports, users
from finbot.repo.expenses import ExpenseView

logger = logging.getLogger(__name__)


async def _corrector_id(session: AsyncSession, sender: User) -> int:
    """`corrections.corrected_by` is a FK to `users.id` (the internal PK,
    same as `expenses.user_id`) — never the Telegram user id `sender.id`
    carries. `AllowlistMiddleware` only proves `sender` is allowed; it does
    not guarantee a `users` row already exists for them (a household member
    could tap ✏️/🗑 before ever sending a plain-text message), so this
    resolves-or-creates exactly like `PersistMessageMiddleware` does for
    message senders.
    """
    return await users.get_or_create(session, sender.id, sender.full_name)


def _to_lines(views: Sequence[ExpenseView]) -> list[ConfirmationLine]:
    """`ExpenseView`s, in `message_id` order, numbered by that fixed order —
    see `ConfirmationLine`'s docstring on why the number never changes once
    assigned.
    """
    return [
        ConfirmationLine(
            index=index,
            expense_id=view.id,
            item=view.item,
            amount=view.amount,
            category_slug=view.category_slug,
            occurred_at=view.occurred_at,
            deleted=view.deleted,
        )
        for index, view in enumerate(views, start=1)
    ]


def _message_ref(query: CallbackQuery) -> MaybeInaccessibleMessageUnion:
    """`callback_query.message`, never narrowed to `Message` — it may be an
    `InaccessibleMessage`, and both carry `chat` and `message_id`, which is
    all an edit-by-id needs (docs/plans/stage-1-text-to-expense.md 3.6).
    """
    if query.message is None:
        msg = "callback_query has no message to edit (inline-mode query?)"
        raise ValueError(msg)
    return query.message


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    """A redelivered tap re-sends an edit identical to the one already on
    screen; Telegram answers 400 *"message is not modified"* for that,
    which is success from this bot's point of view — the state the tap
    wanted is already there. Anything else raised as `TelegramBadRequest`
    is a real failure and must not be swallowed here.
    """
    return "message is not modified" in exc.message


async def _rerender_group(
    *, bot: Bot, query: CallbackQuery, session: AsyncSession, message_id: int, tz: ZoneInfo
) -> None:
    """Re-renders the whole confirmation for `message_id`'s siblings after a
    ✏️/🗑/🗑-all tap changed one of them — the numbering and the still-active
    buttons both come from the same `ExpenseView` list, so they cannot
    disagree with each other.

    Reads `messages.kind` for `message_id` to decide whether the `🗑
    Видалити все` row belongs on the re-rendered keyboard
    (docs/plans/stage-2_5-bank-screenshots.md, Approach D2): a bank
    screenshot keeps it on every re-render, including past the
    `MAX_CONFIRMATION_ROWS` cap, so a batch of real money stays one-tap
    undoable for its whole life — not only on the first send. Text and voice
    never had a source row here to ask; `source is None` cannot happen in
    practice (an expense's `message_id` FK guarantees one), but is treated
    the same as "not a photo" rather than raised, since this function's job
    is rendering, not validating that invariant.
    """
    views = await expenses.siblings(session, message_id)
    lines = _to_lines(views)
    today = datetime.now(tz=tz).date()
    ref = _message_ref(query)
    source = await messages.get(session, message_id)
    delete_all_message_id = (
        message_id if source is not None and source.kind == MessageKind.PHOTO else None
    )
    try:
        await bot.edit_message_text(
            chat_id=ref.chat.id,
            message_id=ref.message_id,
            text=render_confirmation(lines, today=today),
            reply_markup=confirmation_keyboard(lines, delete_all_message_id=delete_all_message_id),
        )
    except TelegramBadRequest as exc:
        # A redelivered tap (e.g. a second identical 🗑 or category pick)
        # re-renders the exact same text and keyboard as last time — not a
        # failure, so it must not answer CALLBACK_FAILURE_REPLY for an
        # operation that already succeeded.
        if not _is_not_modified(exc):
            raise


def build_router(tz: ZoneInfo) -> Router:
    router = Router(name="commands")

    @router.message(Command("ping"))
    async def ping(message: Message) -> None:
        await message.answer("pong")

    @router.message(Command("day", "week", "month"))
    async def report(message: Message, session: AsyncSession, command: CommandObject) -> None:
        period = cast(Period, command.command)
        today = datetime.now(tz=tz).date()
        date_from, date_to = resolve_period(period, today)
        result = await reports.summary(session, period=period, date_from=date_from, date_to=date_to)
        await message.answer(render_report(result))

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(HELP_TEXT)

    # Last message handler, deliberately: aiogram tries a router's message
    # handlers in registration order and stops at the first whose filter
    # matches, so anything specific — /ping, /day|week|month, /help — must
    # be registered above this or it would never be reached. This is the
    # fix for a `/command` Telegram sent to no handler at all going
    # completely unanswered (docs/roadmap.md's Stage 1 hardening); plain
    # expense text never reaches here because F.text.startswith("/")
    # excludes it, leaving that text with no handler on purpose (see this
    # module's docstring).
    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.callback_query(ExpenseAction.filter(F.action == "del"))
    async def delete_expense(
        query: CallbackQuery,
        callback_data: ExpenseAction,
        session: AsyncSession,
        bot: Bot,
        sender: User,
    ) -> None:
        try:
            expense = await expenses.get(session, callback_data.expense_id)
            if expense is None:
                await query.answer(CALLBACK_FAILURE_REPLY)
                return

            if expense.deleted_at is None:
                # Not yet deleted: this is the tap that does it.
                corrected_by = await _corrector_id(session, sender)
                await corrections.record(
                    session,
                    expense_id=expense.id,
                    before={"deleted_at": None},
                    after={"deleted_at": "now"},
                    corrected_by=corrected_by,
                )
                await expenses.soft_delete(session, expense.id)
                await session.commit()
            # else: already deleted — a redelivered tap. Idempotent: no new
            # corrections row, but still re-rendered and still answered.

            await _rerender_group(
                bot=bot, query=query, session=session, message_id=expense.message_id, tz=tz
            )
            await query.answer("Видалив")
        except Exception:
            logger.exception("delete callback failed for expense_id=%s", callback_data.expense_id)
            await query.answer(CALLBACK_FAILURE_REPLY)

    @router.callback_query(ExpenseAction.filter(F.action == "edit"))
    async def start_edit(
        query: CallbackQuery, callback_data: ExpenseAction, session: AsyncSession, bot: Bot
    ) -> None:
        try:
            category_ids = await categories.by_slug(session)
            keyboard: InlineKeyboardMarkup = category_keyboard(
                callback_data.expense_id, CATALOG, category_ids
            )
            ref = _message_ref(query)
            try:
                await bot.edit_message_reply_markup(
                    chat_id=ref.chat.id, message_id=ref.message_id, reply_markup=keyboard
                )
            except TelegramBadRequest as exc:
                # A redelivered ✏️ tap: the category keyboard is already on
                # screen from the first tap, so this one is a no-op success,
                # not a failure.
                if not _is_not_modified(exc):
                    raise
            await query.answer()
        except Exception:
            logger.exception("edit callback failed for expense_id=%s", callback_data.expense_id)
            await query.answer(CALLBACK_FAILURE_REPLY)

    @router.callback_query(ExpenseAction.filter(F.action == "back"))
    async def cancel_edit(
        query: CallbackQuery, callback_data: ExpenseAction, session: AsyncSession, bot: Bot
    ) -> None:
        try:
            expense = await expenses.get(session, callback_data.expense_id)
            if expense is None:
                await query.answer(CALLBACK_FAILURE_REPLY)
                return
            await _rerender_group(
                bot=bot, query=query, session=session, message_id=expense.message_id, tz=tz
            )
            await query.answer()
        except Exception:
            logger.exception("back callback failed for expense_id=%s", callback_data.expense_id)
            await query.answer(CALLBACK_FAILURE_REPLY)

    @router.callback_query(SetCategory.filter())
    async def set_category(
        query: CallbackQuery,
        callback_data: SetCategory,
        session: AsyncSession,
        bot: Bot,
        sender: User,
    ) -> None:
        try:
            expense = await expenses.get(session, callback_data.expense_id)
            if expense is None:
                await query.answer(CALLBACK_FAILURE_REPLY)
                return

            if expense.category_id != callback_data.category_id:
                corrected_by = await _corrector_id(session, sender)
                await corrections.record(
                    session,
                    expense_id=expense.id,
                    before={"category_id": expense.category_id},
                    after={"category_id": callback_data.category_id},
                    corrected_by=corrected_by,
                )
                await expenses.set_category(session, expense.id, callback_data.category_id)
                await session.commit()
            # else: redelivered tap setting the same category — a no-op.

            await _rerender_group(
                bot=bot, query=query, session=session, message_id=expense.message_id, tz=tz
            )
            await query.answer("Готово")
        except Exception:
            logger.exception(
                "set-category callback failed for expense_id=%s", callback_data.expense_id
            )
            await query.answer(CALLBACK_FAILURE_REPLY)

    @router.callback_query(MessageAction.filter(F.action == "delall"))
    async def delete_all(
        query: CallbackQuery,
        callback_data: MessageAction,
        session: AsyncSession,
        bot: Bot,
        sender: User,
    ) -> None:
        """The `🗑 Видалити все` row `confirmation_keyboard` appends on the
        bank path (docs/plans/stage-2_5-bank-screenshots.md, Approach D2,
        R9): soft-deletes every not-yet-deleted sibling of
        `callback_data.message_id` and records one `corrections` row per
        row it actually deletes — `_corrector_id`, exactly like
        `delete_expense`/`set_category` above, never `sender.id` directly
        (that FK trap: `corrections.corrected_by` points at `users.id`, not
        a raw Telegram id). Idempotent under a redelivered tap: a sibling
        already deleted contributes no correction and no soft-delete, the
        same guard `expenses.soft_delete` itself applies.
        """
        try:
            siblings = await expenses.siblings(session, callback_data.message_id)
            active = [view for view in siblings if not view.deleted]
            if active:
                corrected_by = await _corrector_id(session, sender)
                for view in active:
                    await corrections.record(
                        session,
                        expense_id=view.id,
                        before={"deleted_at": None},
                        after={"deleted_at": "now"},
                        corrected_by=corrected_by,
                    )
                    await expenses.soft_delete(session, view.id)
                await session.commit()
            # else: every sibling is already deleted — a redelivered tap.

            await _rerender_group(
                bot=bot, query=query, session=session, message_id=callback_data.message_id, tz=tz
            )
            await query.answer("Видалив усе")
        except Exception:
            logger.exception(
                "delete-all callback failed for message_id=%s", callback_data.message_id
            )
            await query.answer(CALLBACK_FAILURE_REPLY)

    return router
