"""Persistence for finbot.repo.models.Correction — ADR-0006/0007's labelling
pipeline: every ✏️/🗑 tap produces a before/after snapshot, two seconds after
the event, while the correction is still reliable.

None of these functions commit; the caller decides the transaction boundary.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.repo.models import Correction


async def record(
    session: AsyncSession,
    *,
    expense_id: int,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    corrected_by: int,
) -> int:
    """Insert one `corrections` row and return its id.

    `before`/`after` are snapshots of the changed fields only, not the whole
    row — a category correction carries `category_id`, a deletion carries
    `deleted_at`. `corrected_by` is the tapper (`sender_of`), never
    `callback_query.message.from_user`, which is the bot.
    """
    correction = Correction(
        expense_id=expense_id,
        before=dict(before),
        after=dict(after),
        corrected_by=corrected_by,
    )
    session.add(correction)
    await session.flush()
    return correction.id
