"""ADR-0021 end to end: the model proposes a category, the owner approves it
with one tap, and the next expense of that kind is filed automatically.

A real Postgres, a fake LLM client, no Telegram, no socket — the same shape as
`test_extraction_pipeline.py`, whose helpers this reuses rather than
duplicating.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.models import ExtractionStatus, IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories, expenses, messages, users
from finbot.repo.models import Category, Expense, Message
from tests.support.fake_llm import FakeLlmClient
from tests.support.ids import stable_update_id

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_TODAY = datetime(2026, 8, 10, tzinfo=UTC).date()
_MODELS = ("openai/gpt-5.6-luna",)


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


async def _claimed_message(session: AsyncSession, raw_text: str) -> Message:
    incoming = IncomingMessage(
        telegram_update_id=stable_update_id(raw_text),
        telegram_message_id=1,
        chat_id=-1001111111111,
        telegram_user_id=444444444,
        display_name="Alice",
        kind=MessageKind.TEXT,
        raw_text=raw_text,
        file_id=None,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    await session.commit()
    assert message_id is not None
    claimed = await messages.claim_next(session, datetime.now(UTC))
    await session.commit()
    assert claimed is not None
    return claimed


async def _run(session: AsyncSession, message: Message, fixture: str) -> object:
    category_ids = await categories.by_slug(session)
    await session.commit()
    return await extract_and_store(
        session=session,
        message=message,
        llm=FakeLlmClient(_load_fixture(fixture)),
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
    )


async def _category_named(session: AsyncSession, slug: str) -> Category | None:
    return (
        await session.execute(select(Category).where(Category.name == slug))
    ).scalar_one_or_none()


async def test_a_proposal_creates_a_suggested_category_and_leaves_the_row_under_other(
    db_session: AsyncSession,
) -> None:
    """The gate is intact: nothing is filed under a category nobody approved.
    The expense stays `other`, and the proposal is recorded beside it.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")

    outcome = await _run(db_session, message, "ok_with_suggestion")

    assert outcome.status == ExtractionStatus.OK  # type: ignore[attr-defined]
    row = (await db_session.execute(select(Expense))).scalars().one()
    category_ids = await categories.by_slug(db_session)
    assert row.category_id == category_ids["other"]

    suggested = await _category_named(db_session, "osvita")
    assert suggested is not None
    assert suggested.status == "suggested"
    assert suggested.label == "Освіта"
    assert suggested.is_system is False
    assert row.suggested_category_id == suggested.id


async def test_a_suggested_category_is_invisible_until_it_is_approved(
    db_session: AsyncSession,
) -> None:
    """It must not appear in the map the pipeline writes with, nor in the
    picker — otherwise "proposed" and "approved" would be the same state and
    ADR-0005's gate would be gone rather than reduced to a tap.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")
    await _run(db_session, message, "ok_with_suggestion")

    assert "osvita" not in await categories.by_slug(db_session)
    assert "osvita" not in {view.slug for view in await categories.active_views(db_session)}


async def test_approving_it_makes_it_active_and_records_who_created_it(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_message(db_session, "курс англійської 1200")
    await _run(db_session, message, "ok_with_suggestion")
    suggested = await _category_named(db_session, "osvita")
    assert suggested is not None
    user_id = (await db_session.execute(select(Expense))).scalars().one().user_id

    changed = await categories.approve(db_session, suggested.id, created_by=user_id)
    await db_session.commit()

    assert changed
    db_session.expire_all()
    approved = await _category_named(db_session, "osvita")
    assert approved is not None
    assert approved.status == "active"
    assert approved.created_by == user_id
    assert "osvita" in await categories.by_slug(db_session)


async def test_approving_twice_is_a_no_op_success(db_session: AsyncSession) -> None:
    """What a redelivered ➕ tap sends. `False` means "nothing changed", not
    "it failed" — the handler must not answer with an error for an operation
    that already succeeded.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")
    await _run(db_session, message, "ok_with_suggestion")
    suggested = await _category_named(db_session, "osvita")
    assert suggested is not None
    # `created_by` is a real FK and `users.id` does not reset between tests
    # (tests/conftest.py) — a hardcoded 1 violates the constraint.
    user_id = (await db_session.execute(select(Expense))).scalars().one().user_id

    assert await categories.approve(db_session, suggested.id, created_by=user_id)
    assert not await categories.approve(db_session, suggested.id, created_by=user_id)


async def test_once_approved_the_next_expense_is_filed_under_it_with_no_tap(
    db_session: AsyncSession,
) -> None:
    """The point of the whole mechanism: "on the fly" has to mean *once*. The
    second charge of the same kind lands in «Освіта» by itself, with nothing
    pending — which is why `slugify_category` has to be deterministic.
    """
    first = await _claimed_message(db_session, "курс англійської 1200")
    await _run(db_session, first, "ok_with_suggestion")
    suggested = await _category_named(db_session, "osvita")
    assert suggested is not None
    user_id = (await db_session.execute(select(Expense))).scalars().one().user_id
    await categories.approve(db_session, suggested.id, created_by=user_id)
    await db_session.commit()

    second = await _claimed_message(db_session, "курс англійської 1200 знову")
    outcome = await _run(db_session, second, "ok_with_suggestion")

    (new_id,) = outcome.expense_ids  # type: ignore[attr-defined]
    written = await db_session.get(Expense, new_id)
    assert written is not None
    assert written.category_id == suggested.id
    assert written.suggested_category_id is None


async def test_a_second_proposal_reuses_the_pending_row_instead_of_creating_another(
    db_session: AsyncSession,
) -> None:
    """Ten charges before anyone taps ➕ must produce one button, not ten
    identical categories.
    """
    for text in ("курс англійської 1200", "курс англійської 1200 ще", "курс 1200 знов"):
        message = await _claimed_message(db_session, text)
        await _run(db_session, message, "ok_with_suggestion")

    rows = (
        (await db_session.execute(select(Category).where(Category.name == "osvita")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_a_proposal_on_a_row_the_model_already_categorised_is_ignored(
    db_session: AsyncSession,
) -> None:
    """The prompt forbids it; the pipeline enforces it. Honouring a proposal
    beside a confident `groceries` would let the model quietly reclassify a
    row it had already filed.
    """
    message = await _claimed_message(db_session, "хліб 50")

    await _run(db_session, message, "ok_suggestion_on_a_confident_row")

    row = (await db_session.execute(select(Expense))).scalars().one()
    category_ids = await categories.by_slug(db_session)
    assert row.category_id == category_ids["groceries"]
    assert row.suggested_category_id is None
    assert await _category_named(db_session, "pekarnia") is None


async def test_a_proposal_that_slugifies_onto_a_seeded_category_creates_nothing(
    db_session: AsyncSession,
) -> None:
    """ "Cash" slugifies to `cash`, which is already a seeded category. That is
    a rewording, not a new category, and must not produce a row — nor offer a
    ➕ button for something that already exists.
    """
    message = await _claimed_message(db_session, "щось 10")

    await _run(db_session, message, "ok_suggestion_rewording_a_seeded_slug")

    row = (await db_session.execute(select(Expense))).scalars().one()
    assert row.suggested_category_id is None
    cash = await _category_named(db_session, "cash")
    assert cash is not None
    assert cash.is_system is True
    assert cash.status == "active"


async def test_the_confirmation_view_carries_the_pending_proposal(
    db_session: AsyncSession,
) -> None:
    """What the ✏️ picker builds its ➕ row from. Without the label on the
    view there is nothing to put on the button.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")
    message_id = message.id
    await _run(db_session, message, "ok_with_suggestion")

    view = (await expenses.siblings(db_session, message_id))[0]

    assert view.suggested_label == "Освіта"
    assert view.suggested_category_id is not None
    assert view.category_slug == "other"
    assert view.category_label == "Інше"


async def test_a_message_with_a_proposal_still_completes_normally(
    db_session: AsyncSession,
) -> None:
    """The suggestion path runs inside the same transaction as the expense
    write. A failure to flush the new `categories` row would leave the message
    claimed forever — this pins that the round still ends `done`.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")
    message_id = message.id

    await _run(db_session, message, "ok_with_suggestion")

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE


async def test_the_expense_amount_is_untouched_by_the_suggestion_path(
    db_session: AsyncSession,
) -> None:
    """Stated separately because the suggestion path sits between the draft and
    the insert: whatever it does to the category, it must not reach the money.
    """
    message = await _claimed_message(db_session, "курс англійської 1200")

    await _run(db_session, message, "ok_with_suggestion")

    row = (await db_session.execute(select(Expense))).scalars().one()
    assert row.amount == Decimal("1200.00")
    assert row.amount_uah == Decimal("1200.00")
