"""The seed drift guard: `categories` is seeded by the migrations, and its
single source of truth is `finbot.core.categories.catalog.ALL_CATEGORIES`
(`docs/plans/stage-1-text-to-expense.md` → Decisions taken). A migration
edited without the catalog — or the reverse — must fail this gate, the same
role `test_schema_matches_models.py` plays for columns.

`ALL_CATEGORIES`, not `CATALOG`: the two code-assigned categories
`DERIVED_CATALOG` adds (ADR-0020) are rows in this table like any other —
`bank.FORCED_CATEGORY` resolves their slugs through `repo.categories.
by_slug` on every screenshot, so a missing seed is a `KeyError` in the
pipeline, not a cosmetic gap.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.catalog import ALL_CATEGORIES, CATALOG, DERIVED_CATALOG
from finbot.repo.models import Category


async def test_seeded_categories_match_the_catalog_exactly(db_session: AsyncSession) -> None:
    result = await db_session.execute(select(Category).order_by(Category.name))
    rows = result.scalars().all()

    seeded = [(row.name, row.emoji, row.is_system, row.status) for row in rows]
    expected = sorted(
        ((c.slug, c.emoji, True, "active") for c in ALL_CATEGORIES),
        key=lambda row: row[0],
    )

    assert seeded == expected


async def test_seeded_categories_have_fifteen_rows(db_session: AsyncSession) -> None:
    """Thirteen the model may choose from, plus the two only the code assigns.
    Both numbers are spelled out rather than derived from the constants they
    guard: a literal is what makes an accidental catalog edit fail here
    instead of quietly agreeing with itself.
    """
    result = await db_session.execute(select(Category))
    assert len(result.scalars().all()) == 15
    assert len(CATALOG) == 13
    assert len(DERIVED_CATALOG) == 2
