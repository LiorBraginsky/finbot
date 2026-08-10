"""Unit tests for finbot.adapters.telegram.render. No Docker, no network."""

from datetime import date
from decimal import Decimal

import pytest

from finbot.adapters.telegram.render import (
    CATEGORY_LABELS,
    EMPTY_REPORT_REPLY,
    ConfirmationLine,
    render_confirmation,
    render_report,
    transcript_line,
    voice_too_long_reply,
)
from finbot.core.categories.catalog import SLUGS
from finbot.core.reporting import Report, ReportLine

_TODAY = date(2026, 8, 10)


def test_category_labels_cover_exactly_the_catalog_slugs() -> None:
    assert set(CATEGORY_LABELS) == SLUGS


def test_two_expenses_render_as_one_numbered_confirmation_with_a_total() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="хліб",
            amount=Decimal("50.00"),
            category_slug="groceries",
            occurred_at=_TODAY,
        ),
        ConfirmationLine(
            index=2,
            expense_id=20,
            item="таксі",
            amount=Decimal("200.00"),
            category_slug="transport",
            occurred_at=_TODAY,
        ),
    ]

    rendered = render_confirmation(lines, today=_TODAY)

    assert rendered == (
        "✅ Записав 2:\n1. 🛒 хліб — 50.00 ₴\n2. 🚕 таксі — 200.00 ₴\nРазом: 250.00 ₴"
    )


def test_a_single_expense_renders_with_no_number_and_no_total() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="хліб",
            amount=Decimal("50.00"),
            category_slug="groceries",
            occurred_at=_TODAY,
        )
    ]

    assert render_confirmation(lines, today=_TODAY) == "✅ 🛒 хліб — 50.00 ₴"


def test_an_occurred_at_other_than_today_gets_a_date_suffix() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="таксі",
            amount=Decimal("200.00"),
            category_slug="transport",
            occurred_at=date(2026, 8, 9),
        )
    ]

    assert render_confirmation(lines, today=_TODAY) == "✅ 🚕 таксі — 200.00 ₴ (09.08)"


def test_a_deleted_row_keeps_its_place_but_loses_its_number_and_is_marked() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="хліб",
            amount=Decimal("50.00"),
            category_slug="groceries",
            occurred_at=_TODAY,
        ),
        ConfirmationLine(
            index=2,
            expense_id=20,
            item="таксі",
            amount=Decimal("200.00"),
            category_slug="transport",
            occurred_at=_TODAY,
            deleted=True,
        ),
    ]

    rendered = render_confirmation(lines, today=_TODAY)

    assert rendered == (
        "✅ Записав 2:\n1. 🛒 хліб — 50.00 ₴\n✖️ 🚕 таксі — 200.00 ₴ (видалено)\nРазом: 50.00 ₴"
    )


def test_render_confirmation_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one line"):
        render_confirmation([], today=_TODAY)


def test_transcript_line_wraps_in_microphone_and_guillemets() -> None:
    assert transcript_line("хліб пʼятдесят") == "🎤 «хліб пʼятдесят»"


def test_transcript_line_leaves_a_short_transcript_untouched() -> None:
    short = "х" * 500
    assert transcript_line(short) == f"🎤 «{short}»"


def test_transcript_line_truncates_a_long_transcript_with_an_ellipsis() -> None:
    long_transcript = "х" * 600

    rendered = transcript_line(long_transcript)

    assert rendered == f"🎤 «{'х' * 500}…»"
    assert len(rendered) < len(f"🎤 «{long_transcript}»")


def test_render_confirmation_shows_the_transcript_above_a_single_expense() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="хліб",
            amount=Decimal("50.00"),
            category_slug="groceries",
            occurred_at=_TODAY,
        )
    ]

    rendered = render_confirmation(lines, today=_TODAY, transcript="хліб пʼятдесят")

    assert rendered == "🎤 «хліб пʼятдесят»\n✅ 🛒 хліб — 50.00 ₴"


def test_render_confirmation_shows_the_transcript_above_a_numbered_list() -> None:
    lines = [
        ConfirmationLine(
            index=1,
            expense_id=10,
            item="хліб",
            amount=Decimal("50.00"),
            category_slug="groceries",
            occurred_at=_TODAY,
        ),
        ConfirmationLine(
            index=2,
            expense_id=20,
            item="таксі",
            amount=Decimal("200.00"),
            category_slug="transport",
            occurred_at=_TODAY,
        ),
    ]

    rendered = render_confirmation(lines, today=_TODAY, transcript="хліб пʼятдесят і таксі двісті")

    assert rendered == (
        "🎤 «хліб пʼятдесят і таксі двісті»\n"
        "✅ Записав 2:\n1. 🛒 хліб — 50.00 ₴\n2. 🚕 таксі — 200.00 ₴\nРазом: 250.00 ₴"
    )


def test_voice_too_long_reply_names_the_configured_limit() -> None:
    assert "120" in voice_too_long_reply(120)
    assert "45" in voice_too_long_reply(45)


def test_empty_report_returns_the_empty_state_text() -> None:
    report = Report(period="day", date_from=_TODAY, date_to=_TODAY, lines=(), total=Decimal("0"))
    assert render_report(report) == EMPTY_REPORT_REPLY


def test_report_with_lines_renders_category_totals_and_a_grand_total() -> None:
    report = Report(
        period="week",
        date_from=date(2026, 8, 10),
        date_to=date(2026, 8, 12),
        lines=(
            ReportLine(category_slug="groceries", total=Decimal("120.00"), count=2),
            ReportLine(category_slug="transport", total=Decimal("80.00"), count=1),
        ),
        total=Decimal("200.00"),
    )

    rendered = render_report(report)

    assert rendered == (
        "📊 10.08–12.08:\n🛒 Продукти — 120.00 ₴ (2)\n🚕 Транспорт — 80.00 ₴ (1)\nРазом: 200.00 ₴"
    )
