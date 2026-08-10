"""Persistence for finbot.repo.models.Category.

Cached per pipeline run, not globally: Stage 5 lets categories be added/merged
at runtime, and a process-lifetime cache would go stale the moment that
happens.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.repo.models import Category


async def all_active(session: AsyncSession) -> list[Category]:
    """All categories with status='active', in no particular order."""
    result = await session.execute(select(Category).where(Category.status == "active"))
    return list(result.scalars().all())


async def by_slug(session: AsyncSession) -> dict[str, int]:
    """Map of active category slug (`name`) -> id."""
    categories = await all_active(session)
    return {category.name: category.id for category in categories}
