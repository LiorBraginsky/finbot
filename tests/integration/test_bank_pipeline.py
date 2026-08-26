"""Integration tests for the bank-feed (`MessageKind.PHOTO`) path of
`finbot.core.extraction.pipeline.extract_and_store` (docs/plans/
stage-2_5-bank-screenshots.md, Step 2): a real Postgres, a fake LLM client,
no Telegram, no socket. Mirrors `tests/integration/test_extraction_pipeline.
py`'s voice section in structure.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.extraction.schema import BankRowKind
from finbot.core.models import ExtractionStatus, IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories, messages, users
from finbot.repo.models import Expense, Extraction, Message
from tests.support.fake_llm import FakeLlmClient
from tests.support.ids import stable_update_id

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_ANCHOR = date(2026, 8, 24)
_MODELS = ("google/gemini-3.5-flash-lite", "google/gemini-3.6-flash")


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


async def _claimed_photo_message(session: AsyncSession, *, file_id: str = "photo-1") -> Message:
    """Mirrors `test_extraction_pipeline._claimed_voice_message`, for a
    photo update instead: `raw_text` starts `None` (no caption), and there
    is no `duration_seconds` — a photo carries none.

    `repo.messages._initial_status` keeps a photo `skipped` — deliberately
    unchanged in this step (docs/plans/stage-2_5-bank-screenshots.md, Step
    2: "photos stay skipped, so the running bot is unaffected"), so
    `claim_next` alone would never see one. This helper flips the row to
    `pending` itself, purely to exercise `extract_and_store`'s own PHOTO
    routing directly — the same way `_claimed_voice_message` exercises the
    VOICE routing without going through the full drain loop — and does not
    touch `_initial_status` itself.
    """
    incoming = IncomingMessage(
        telegram_update_id=stable_update_id(file_id),
        telegram_message_id=1,
        chat_id=-1001111111111,
        telegram_user_id=444444444,
        display_name="Alice",
        kind=MessageKind.PHOTO,
        raw_text=None,
        file_id=file_id,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    await session.commit()
    assert message_id is not None

    await session.execute(
        update(Message).where(Message.id == message_id).values(status=MessageStatus.PENDING)
    )
    await session.commit()

    claimed = await messages.claim_next(session, datetime.now(UTC))
    await session.commit()
    assert claimed is not None
    return claimed


async def _category_ids(session: AsyncSession) -> dict[str, int]:
    category_ids = await categories.by_slug(session)
    await session.commit()
    return category_ids


async def _fetch_image_ok() -> str:
    return "data:image/jpeg;base64,ZmFrZS1qcGVn"


async def test_expense_row_written_with_the_right_amount_date_and_category(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_photo_message(db_session)
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("bank_feed_ok"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_fetch_image_ok,
        anchor_date=_ANCHOR,
    )

    assert outcome.status == ExtractionStatus.OK
    assert len(outcome.expense_ids) == 1

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 1
    row = expense_rows[0]
    assert row.item == "Silpo"
    assert row.amount == Decimal("320.50")
    assert row.occurred_at == _ANCHOR
    assert row.category_id == category_ids["groceries"]
    assert row.bank_txn_key == f"{_ANCHOR.isoformat()}|14:32|320.50"

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE
    # No transcript for a photo: the pixels are not archived, and the rows
    # are already in extractions.raw_response (ADR-0009).
    assert refreshed.raw_text is None


async def test_non_expense_rows_are_absent_from_expenses_and_present_in_raw_response(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_photo_message(db_session)
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("bank_feed_ok"))

    await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_fetch_image_ok,
        anchor_date=_ANCHOR,
    )

    # Approach A1, asserted rather than assumed: the savings jar and the
    # own-transfer row never reach `expenses` at all...
    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert [row.item for row in expense_rows] == ["Silpo"]

    # ...but the model's full answer about them still exists, verbatim, in
    # the one place rule 6 requires it.
    extraction_rows = (await db_session.execute(select(Extraction))).scalars().all()
    assert len(extraction_rows) == 1
    extraction = extraction_rows[0]
    assert extraction.status == ExtractionStatus.OK
    assert extraction.model_id == "google/gemini-3.5-flash-lite"
    assert extraction.cost_usd == Decimal("0.0019")
    raw_content = extraction.raw_response["choices"][0]["message"]["content"]
    assert "savings" in raw_content
    assert "own_transfer" in raw_content


async def test_multi_day_fixture_produces_rows_on_two_dates_in_feed_order(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_photo_message(db_session)
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("bank_multi_day"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_fetch_image_ok,
        anchor_date=_ANCHOR,
    )

    assert outcome.status == ExtractionStatus.OK
    assert len(outcome.expense_ids) == 2

    expense_rows = (await db_session.execute(select(Expense).order_by(Expense.id))).scalars().all()
    assert [row.occurred_at for row in expense_rows] == [_ANCHOR, _ANCHOR.replace(day=23)]
    assert [row.amount for row in expense_rows] == [Decimal("75.00"), Decimal("410.20")]


async def test_not_a_transaction_feed_writes_nothing_but_still_marks_done(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_photo_message(db_session)
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("bank_not_a_feed"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_fetch_image_ok,
        anchor_date=_ANCHOR,
    )

    assert outcome.status == ExtractionStatus.OK
    assert outcome.expense_ids == ()
    assert outcome.bank_summary is not None
    assert outcome.bank_summary.plan.writes == ()

    assert (await db_session.execute(select(Expense))).scalars().all() == []

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE


async def test_cash_and_transfer_out_rows_land_in_their_code_assigned_categories(
    db_session: AsyncSession,
) -> None:
    """ADR-0020, end to end through a real Postgres: the two written
    non-expense kinds reach `expenses` under `cash`/`transfers` — the FK,
    not just the slug — while `own_transfer` on the same screenshot is still
    written nowhere.

    Both directions matter. If the derived categories were missing from the
    seed, `category_ids[draft.category]` would raise `KeyError` here instead
    of writing a row; if `FORCED_CATEGORY` were ignored, both rows would land
    under whatever the fixture's `category` says (`other` and `gifts`), which
    is exactly the silent miscategorisation the forcing exists to prevent.
    """
    message = await _claimed_photo_message(db_session)
    category_ids = await _category_ids(db_session)

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=FakeLlmClient(_load_fixture("bank_cash_and_transfer")),
        catalog=CATALOG,
        category_ids=category_ids,
        today=_ANCHOR,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
        max_voice_seconds=120,
        fetch_image=_fetch_image_ok,
        anchor_date=_ANCHOR,
    )

    assert outcome.status == ExtractionStatus.OK
    assert len(outcome.expense_ids) == 2

    rows = (await db_session.execute(select(Expense).order_by(Expense.amount))).scalars().all()
    written = {(row.item, row.amount, row.category_id) for row in rows}
    assert written == {
        ("Переказ на картку 0000", Decimal("750.25"), category_ids["transfers"]),
        ("Зняття готівки в банкоматі", Decimal("2000.00"), category_ids["cash"]),
    }

    # The `own_transfer` row on the same screenshot: reported, stored nowhere.
    assert outcome.bank_summary is not None
    assert outcome.bank_summary.plan.skipped_by_kind == {BankRowKind.OWN_TRANSFER: 1}


async def test_a_cash_row_dedupes_on_a_resend_like_any_other_written_row(
    db_session: AsyncSession,
) -> None:
    """The new kinds are written through the same keyed insert, so re-sending
    the screenshot must record nothing new — a hole here would double-count
    every cash withdrawal on every overlapping screenshot.
    """
    category_ids = await _category_ids(db_session)

    for file_id in ("cash-1", "cash-2"):
        message = await _claimed_photo_message(db_session, file_id=file_id)
        outcome = await extract_and_store(
            session=db_session,
            message=message,
            llm=FakeLlmClient(_load_fixture("bank_cash_and_transfer")),
            catalog=CATALOG,
            category_ids=category_ids,
            today=_ANCHOR,
            models=_MODELS,
            max_attempts=2,
            max_message_attempts=5,
            max_voice_seconds=120,
            fetch_image=_fetch_image_ok,
            anchor_date=_ANCHOR,
        )

    assert outcome.expense_ids == ()
    assert outcome.bank_summary is not None
    assert {draft.item for draft in outcome.bank_summary.duplicates} == {
        "Зняття готівки в банкоматі",
        "Переказ на картку 0000",
    }
    assert len((await db_session.execute(select(Expense))).scalars().all()) == 2
