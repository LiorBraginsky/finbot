"""cash and transfers categories

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled literally, never imported from `finbot.core.categories.catalog`:
# migrations must keep working against a checkout whose application code has
# moved on (same rule as 0002's thirteen). The drift guard is
# `tests/integration/test_categories_seed.py`, which asserts the seeded rows
# equal `ALL_CATEGORIES`.
#
# `is_system=True`: these two are not owner-created categories, they are part
# of the taxonomy the code itself depends on — `bank.FORCED_CATEGORY` looks
# their slugs up by name on every screenshot, so a merge or a rename would
# break extraction rather than just reorganise a report.
_DERIVED = (
    ("cash", "💵"),
    ("transfers", "↔️"),
)


def upgrade() -> None:
    categories = sa.table(
        "categories",
        sa.column("name", sa.String),
        sa.column("emoji", sa.String),
        sa.column("is_system", sa.Boolean),
        sa.column("status", sa.String),
    )
    op.bulk_insert(
        categories,
        [
            {"name": name, "emoji": emoji, "is_system": True, "status": "active"}
            for name, emoji in _DERIVED
        ],
    )


def downgrade() -> None:
    # Only the rows themselves. Any `expenses` row filed under one of these
    # would violate its FK, so this deliberately fails loudly rather than
    # cascading — losing real recorded money to a schema rollback is worse
    # than a failed downgrade.
    op.execute(
        sa.text("DELETE FROM categories WHERE name IN :names").bindparams(
            sa.bindparam("names", value=tuple(name for name, _ in _DERIVED), expanding=True)
        )
    )
