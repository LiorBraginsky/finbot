"""One SQL query per report — CLAUDE.md rule 5: reports are SQL, never routed
through a model.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.reporting import Report, ReportLine

_SUMMARY_QUERY = text(
    """
    SELECT c.name AS category_slug, SUM(e.amount_uah) AS total, COUNT(*) AS n
      FROM expenses e
      JOIN categories c ON c.id = e.category_id
     WHERE e.deleted_at IS NULL AND e.occurred_at BETWEEN :date_from AND :date_to
     GROUP BY c.name
     ORDER BY total DESC
    """
)


async def summary(session: AsyncSession, *, period: str, date_from: date, date_to: date) -> Report:
    """`Report` for `[date_from, date_to]`, soft-deleted rows excluded.

    `c.name` is the category slug (see `repo/models.py::Category.name`) —
    the emoji and the Ukrainian label are presentation and are looked up
    from the slug in `adapters/telegram/render.py`, never carried by this
    query.
    """
    result = await session.execute(_SUMMARY_QUERY, {"date_from": date_from, "date_to": date_to})
    lines = tuple(
        ReportLine(category_slug=row.category_slug, total=row.total, count=row.n) for row in result
    )
    total = sum((line.total for line in lines), Decimal("0"))
    return Report(period=period, date_from=date_from, date_to=date_to, lines=lines, total=total)
