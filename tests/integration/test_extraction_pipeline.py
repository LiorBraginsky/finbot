"""Integration tests for finbot.core.extraction.pipeline.extract_and_store:
a real Postgres, a fake LLM client, no Telegram, no socket
(docs/plans/stage-1-text-to-expense.md 2.11).

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.pipeline import extract_and_store
from finbot.core.extraction.ports import LlmError
from finbot.core.models import ExtractionStatus, IncomingMessage, MessageKind, MessageStatus
from finbot.repo import categories, messages, users
from finbot.repo.models import Expense, Extraction, Message
from tests.support.fake_llm import FakeLlmClient

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"
_TODAY = datetime(2026, 8, 10, tzinfo=UTC).date()
_MODELS = ("openai/gpt-5.6-luna", "qwen/qwen3.7-flash")


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


def _response_body(*, model: str, content: dict, cost: float | None) -> str:
    """Build a raw OpenRouter response body around an arbitrary `content`
    payload, for cases the checked-in fixtures don't already cover (specific
    `occurred_at` values).
    """
    assistant_message = {"role": "assistant", "content": json.dumps(content)}
    return json.dumps(
        {
            "id": "gen-inline",
            "model": model,
            "object": "chat.completion",
            "created": 1754800099,
            "choices": [{"index": 0, "message": assistant_message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": cost},
        }
    )


async def _claimed_message(session: AsyncSession, raw_text: str) -> Message:
    """Insert a PENDING text message and claim it, mirroring what the drain
    loop does before calling extract_and_store — `messages.attempts` must
    already be incremented for schedule_retry's backoff math to be tested.
    """
    incoming = IncomingMessage(
        telegram_update_id=hash(raw_text) & 0x7FFFFFFF,
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


async def _category_ids(session: AsyncSession) -> dict[str, int]:
    return await categories.by_slug(session)


async def test_two_items_produce_two_expenses_and_one_ok_extraction(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_message(db_session, "хліб 50, таксі 200")
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("ok_two_items"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.OK
    assert len(outcome.expense_ids) == 2

    expense_rows = (await db_session.execute(select(Expense).order_by(Expense.id))).scalars().all()
    assert [row.item for row in expense_rows] == ["хліб", "таксі"]
    assert expense_rows[0].amount == Decimal("50.00")
    assert expense_rows[1].amount == Decimal("200.00")

    extraction_rows = (await db_session.execute(select(Extraction))).scalars().all()
    assert len(extraction_rows) == 1
    row = extraction_rows[0]
    assert row.status == ExtractionStatus.OK
    assert row.cost_usd == Decimal("0.000123")
    # The fixture's "model" differs from the requested primary on purpose:
    # model_id must come from the response, never from config.
    assert row.model_id == "google/gemini-3.5-flash-lite"
    assert row.model_id != _MODELS[0]

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE


async def test_invalid_json_then_ok_repairs_within_one_round(db_session: AsyncSession) -> None:
    message = await _claimed_message(db_session, "хліб 50, таксі 200")
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("invalid_json"), _load_fixture("ok_two_items"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.OK

    extraction_rows = (
        (await db_session.execute(select(Extraction).order_by(Extraction.attempt))).scalars().all()
    )
    assert [row.attempt for row in extraction_rows] == [1, 2]
    assert [row.status for row in extraction_rows] == [
        ExtractionStatus.INVALID_JSON,
        ExtractionStatus.OK,
    ]

    assert len(llm.requests) == 2
    repair_request = llm.requests[1]
    roles = [message["role"] for message in repair_request.messages]
    assert roles[-2:] == ["assistant", "user"]
    assert "did not match the schema" in repair_request.messages[-1]["content"]


async def test_two_consecutive_invalid_json_schedules_a_retry(db_session: AsyncSession) -> None:
    message = await _claimed_message(db_session, "хліб 50, таксі 200")
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("invalid_json"), _load_fixture("invalid_json"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.INVALID_JSON
    assert outcome.expense_ids == ()

    extraction_rows = (await db_session.execute(select(Extraction))).scalars().all()
    assert len(extraction_rows) == 2
    assert {row.status for row in extraction_rows} == {ExtractionStatus.INVALID_JSON}

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert expense_rows == []

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.PENDING
    assert refreshed.attempts == 1
    assert refreshed.next_attempt_at > datetime.now(UTC)


async def test_llm_error_records_a_failed_row_with_no_cost(db_session: AsyncSession) -> None:
    message = await _claimed_message(db_session, "хліб 50")
    message_id = message.id
    category_ids = await _category_ids(db_session)
    transport_error = LlmError(
        "boom: connection reset", raw={"error": "boom", "type": "ClientError", "status": None}
    )
    llm = FakeLlmClient(transport_error)

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.FAILED

    extraction_rows = (await db_session.execute(select(Extraction))).scalars().all()
    assert len(extraction_rows) == 1
    row = extraction_rows[0]
    assert row.status == ExtractionStatus.FAILED
    assert row.cost_usd is None
    assert row.raw_response is not None
    assert row.raw_response["error"] == "boom"

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.PENDING


async def test_no_cost_fixture_still_writes_expenses_with_null_cost(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_message(db_session, "хліб 50")
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("no_cost"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.OK
    assert len(outcome.expense_ids) == 1

    extraction_rows = (await db_session.execute(select(Extraction))).scalars().all()
    assert extraction_rows[0].cost_usd is None


async def test_ok_empty_asks_for_clarification_and_still_marks_done(
    db_session: AsyncSession,
) -> None:
    message = await _claimed_message(db_session, "просто повідомлення без суми")
    message_id = message.id
    category_ids = await _category_ids(db_session)
    llm = FakeLlmClient(_load_fixture("ok_empty"))

    outcome = await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    assert outcome.status == ExtractionStatus.OK
    assert outcome.expense_ids == ()
    assert outcome.asked_for_clarification is True

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert expense_rows == []

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.DONE


async def test_occurred_at_null_resolves_to_today(db_session: AsyncSession) -> None:
    message = await _claimed_message(db_session, "хліб 50")
    category_ids = await _category_ids(db_session)
    body = _response_body(
        model="google/gemini-3.5-flash-lite",
        content={
            "expenses": [
                {"item": "хліб", "amount": 50, "category": "groceries", "occurred_at": None}
            ]
        },
        cost=0.0001,
    )
    llm = FakeLlmClient(body)

    await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert expense_rows[0].occurred_at == _TODAY


async def test_occurred_at_in_the_future_clamps_to_today(db_session: AsyncSession) -> None:
    message = await _claimed_message(db_session, "хліб 50")
    category_ids = await _category_ids(db_session)
    body = _response_body(
        model="google/gemini-3.5-flash-lite",
        content={
            "expenses": [
                {
                    "item": "хліб",
                    "amount": 50,
                    "category": "groceries",
                    "occurred_at": "2026-08-11",
                }
            ]
        },
        cost=0.0001,
    )
    llm = FakeLlmClient(body)

    await extract_and_store(
        session=db_session,
        message=message,
        llm=llm,
        catalog=CATALOG,
        category_ids=category_ids,
        today=_TODAY,
        models=_MODELS,
        max_attempts=2,
        max_message_attempts=5,
    )

    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert expense_rows[0].occurred_at == _TODAY
