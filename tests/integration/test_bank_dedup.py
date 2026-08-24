"""Integration tests for Approach C2's database-enforced dedup guarantee
(docs/plans/stage-2_5-bank-screenshots.md, Step 2):
`uq_expenses_user_bank_txn_key` plus `repo.expenses.create_bank_row`'s keyed
insert. Every case here is a property of the unique constraint and the
`ON CONFLICT DO NOTHING` statement built against it, not of Python — see
each test's own note on what would go red if the constraint were dropped.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.models import IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories, expenses, messages, users
from finbot.repo.models import Expense, Message
from tests.support.fake_llm import FakeLlmClient

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_ANCHOR = date(2026, 8, 24)
_MODELS = ("google/gemini-3.5-flash-lite", "google/gemini-3.6-flash")


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


def _row(
    *,
    date_header: str = "Сьогодні",
    time: str | None = "14:32",
    merchant: str = "Silpo",
    amount: float = 320.50,
    kind: str = "expense",
    category: str = "groceries",
    partially_visible: bool = False,
) -> dict[str, Any]:
    return {
        "date_header": date_header,
        "time": time,
        "merchant": merchant,
        "amount": amount,
        "kind": kind,
        "category": category,
        "partially_visible": partially_visible,
    }


def _bank_response_body(*, rows: list[dict[str, Any]], model: str = _MODELS[0]) -> str:
    """Builds a raw OpenRouter body around hand-built `rows`, for the
    dedup-specific shapes (two rows sharing a key, an overlapping feed) that
    do not belong as checked-in fixtures — mirrors `test_extraction_
    pipeline._response_body`'s own reason for existing beside the checked-in
    fixtures.
    """
    content = json.dumps({"is_transaction_feed": True, "rows": rows})
    assistant_message = {"role": "assistant", "content": content}
    return json.dumps(
        {
            "id": "gen-inline-bank",
            "model": model,
            "object": "chat.completion",
            "created": 1754800500,
            "choices": [{"index": 0, "message": assistant_message}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost": 0.0019,
            },
        }
    )


async def _claimed_photo_message(session: AsyncSession, *, file_id: str) -> Message:
    """See `test_bank_pipeline._claimed_photo_message`'s own docstring for
    why the row is flipped to `pending` by hand here.
    """
    incoming = IncomingMessage(
        telegram_update_id=hash(file_id) & 0x7FFFFFFF,
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


async def _plain_message_id(session: AsyncSession, *, telegram_message_id: int) -> int:
    """A `messages` row to hang a manually-typed expense off of — any valid
    FK target. `_initial_status` would make a plain-text row `pending`,
    which would make it compete with `_claimed_photo_message`'s own claim
    for `claim_next` (oldest `pending` row first) — marked `done` by hand
    right after, since only the FK target matters here, not the row's own
    processing state.
    """
    incoming = IncomingMessage(
        telegram_update_id=1_000_000 + telegram_message_id,
        telegram_message_id=telegram_message_id,
        chat_id=-1001111111111,
        telegram_user_id=444444444,
        display_name="Alice",
        kind=MessageKind.TEXT,
        raw_text="кава 150",
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    await session.commit()
    assert message_id is not None

    await session.execute(
        update(Message).where(Message.id == message_id).values(status=MessageStatus.DONE)
    )
    await session.commit()
    return message_id


async def _category_ids(session: AsyncSession) -> dict[str, int]:
    category_ids = await categories.by_slug(session)
    await session.commit()
    return category_ids


async def _fetch_image_ok() -> str:
    return "data:image/jpeg;base64,ZmFrZS1qcGVn"


async def _run(
    session: AsyncSession, message: Message, llm: FakeLlmClient, category_ids: dict[str, int]
):
    return await extract_and_store(
        session=session,
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


async def test_same_screenshot_twice_writes_nothing_the_second_time(
    db_session: AsyncSession,
) -> None:
    """If `uq_expenses_user_bank_txn_key` were dropped, `create_bank_row`'s
    `ON CONFLICT DO NOTHING` would have nothing to conflict against — the
    second call would insert a second row, `outcome_2.expense_ids` would
    have length 1 instead of 0, and this test would fail exactly there.
    """
    category_ids = await _category_ids(db_session)

    message_1 = await _claimed_photo_message(db_session, file_id="p1")
    outcome_1 = await _run(
        db_session, message_1, FakeLlmClient(_load_fixture("bank_feed_ok")), category_ids
    )
    assert len(outcome_1.expense_ids) == 1

    message_2 = await _claimed_photo_message(db_session, file_id="p2")
    outcome_2 = await _run(
        db_session, message_2, FakeLlmClient(_load_fixture("bank_feed_ok")), category_ids
    )

    assert outcome_2.expense_ids == ()
    assert outcome_2.bank_summary is not None
    assert outcome_2.bank_summary.duplicates == 1

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 1


async def test_overlapping_screenshot_writes_only_the_new_rows(db_session: AsyncSession) -> None:
    category_ids = await _category_ids(db_session)

    message_1 = await _claimed_photo_message(db_session, file_id="p1")
    outcome_1 = await _run(
        db_session, message_1, FakeLlmClient(_load_fixture("bank_feed_ok")), category_ids
    )
    assert len(outcome_1.expense_ids) == 1

    overlapping_body = _bank_response_body(
        rows=[
            _row(merchant="Silpo", amount=320.50, time="14:32"),  # same key as message_1's row
            _row(merchant="Rozetka", amount=500.00, time="16:00", category="other"),
        ]
    )
    message_2 = await _claimed_photo_message(db_session, file_id="p2")
    outcome_2 = await _run(db_session, message_2, FakeLlmClient(overlapping_body), category_ids)

    assert len(outcome_2.expense_ids) == 1
    assert outcome_2.bank_summary is not None
    assert outcome_2.bank_summary.duplicates == 1

    expense_rows = (await db_session.execute(select(Expense).order_by(Expense.id))).scalars().all()
    assert [row.item for row in expense_rows] == ["Silpo", "Rozetka"]


async def test_two_rows_sharing_a_key_in_one_body_insert_once_and_are_counted(
    db_session: AsyncSession,
) -> None:
    """The key deliberately excludes `merchant` (Approach C2): two rows that
    differ only there still collide.
    """
    category_ids = await _category_ids(db_session)
    body = _bank_response_body(
        rows=[
            _row(merchant="Кафе А", amount=100.00, time="12:00", category="dining_out"),
            _row(merchant="Кафе Б", amount=100.00, time="12:00", category="dining_out"),
        ]
    )
    message = await _claimed_photo_message(db_session, file_id="p1")

    outcome = await _run(db_session, message, FakeLlmClient(body), category_ids)

    assert len(outcome.expense_ids) == 1
    assert outcome.bank_summary is not None
    assert outcome.bank_summary.duplicates == 1

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 1


async def test_manual_expense_same_date_and_amount_is_left_alone_and_reported(
    db_session: AsyncSession,
) -> None:
    """R7: never merged, never suppressed — both rows keep existing, and the
    reply (Step 3) is expected to name the collision from
    `outcome.bank_summary.manual_collisions`.
    """
    category_ids = await _category_ids(db_session)

    manual_message_id = await _plain_message_id(db_session, telegram_message_id=99)
    user_id = await users.get_or_create(db_session, 444444444, "Alice")
    manual_expense_id = await expenses.create(
        db_session,
        message_id=manual_message_id,
        user_id=user_id,
        category_id=category_ids["dining_out"],
        item="кава",
        amount=Decimal("150.00"),
        occurred_at=_ANCHOR,
    )
    await db_session.commit()

    body = _bank_response_body(
        rows=[_row(merchant="кава", amount=150.00, time="08:00", category="dining_out")]
    )
    message = await _claimed_photo_message(db_session, file_id="p1")
    outcome = await _run(db_session, message, FakeLlmClient(body), category_ids)

    assert len(outcome.expense_ids) == 1
    assert outcome.bank_summary is not None
    assert outcome.bank_summary.duplicates == 0
    collisions = outcome.bank_summary.manual_collisions
    assert len(collisions) == 1
    assert collisions[0].id == manual_expense_id
    assert collisions[0].item == "кава"
    assert collisions[0].amount == Decimal("150.00")
    assert collisions[0].occurred_at == _ANCHOR

    all_expenses = (await db_session.execute(select(Expense))).scalars().all()
    assert len(all_expenses) == 2
    assert {row.id for row in all_expenses} == {manual_expense_id, outcome.expense_ids[0]}


async def test_soft_deleted_bank_row_is_not_resurrected_by_a_resend(
    db_session: AsyncSession,
) -> None:
    """Pinned because it is a choice, not an accident: the unique constraint
    is not conditioned on `deleted_at`, so a 🗑'd bank row stays gone even
    when the exact same screenshot is sent again.
    """
    category_ids = await _category_ids(db_session)

    message_1 = await _claimed_photo_message(db_session, file_id="p1")
    outcome_1 = await _run(
        db_session, message_1, FakeLlmClient(_load_fixture("bank_feed_ok")), category_ids
    )
    assert len(outcome_1.expense_ids) == 1
    await expenses.soft_delete(db_session, outcome_1.expense_ids[0])
    await db_session.commit()

    message_2 = await _claimed_photo_message(db_session, file_id="p2")
    outcome_2 = await _run(
        db_session, message_2, FakeLlmClient(_load_fixture("bank_feed_ok")), category_ids
    )

    assert outcome_2.expense_ids == ()
    assert outcome_2.bank_summary is not None
    assert outcome_2.bank_summary.duplicates == 1

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert len(expense_rows) == 1
    assert expense_rows[0].deleted_at is not None
