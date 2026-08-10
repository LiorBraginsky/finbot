"""Persistence for finbot.repo.models.Extraction — ADR-0006's provenance
table: one row per attempt, including failed ones, because how often a model
needs a repair loop is a quality metric, not noise.

None of these functions commit; the caller decides the transaction boundary.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import ExtractionStatus
from finbot.repo.models import Extraction


async def record(
    session: AsyncSession,
    *,
    message_id: int,
    model_id: str,
    prompt_version: str,
    attempt: int,
    status: ExtractionStatus,
    raw_response: Mapping[str, Any],
    cost_usd: Decimal | None,
    latency_ms: int,
) -> int:
    """Insert one `extractions` row and return its id.

    `raw_response` is stored even on failure: when there is no response, the
    caller passes the error object it built instead — "we had none" is
    itself the record CLAUDE.md rule 6 requires.
    """
    extraction = Extraction(
        message_id=message_id,
        model_id=model_id,
        prompt_version=prompt_version,
        attempt=attempt,
        status=status,
        raw_response=dict(raw_response),
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
    session.add(extraction)
    await session.flush()
    return extraction.id
