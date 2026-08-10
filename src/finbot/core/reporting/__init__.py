"""Report DTOs — pure data, no SQL, no I/O.

CLAUDE.md rule 5 says reports are SQL, never routed through a model; this
module is only the shape the SQL result (`repo/reports.py`) takes on its way
to `adapters/telegram/render.py`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ReportLine:
    category_slug: str
    total: Decimal
    count: int


@dataclass(frozen=True)
class Report:
    period: str
    date_from: date
    date_to: date
    lines: tuple[ReportLine, ...]
    total: Decimal
