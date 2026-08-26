"""category labels and model suggestions

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The fifteen seeded slugs and their Ukrainian labels, spelled literally for
# the same reason 0002 and 0005 spell theirs: a migration must keep working
# against a checkout whose application code has moved on. The drift guard is
# `tests/integration/test_categories_seed.py`.
#
# Before this migration the labels lived only in
# `adapters.telegram.render.CATEGORY_LABELS`, which was fine while every
# category was known at import time. An owner-created category has no entry
# there and never can, so the label becomes data.
_LABELS = {
    "groceries": "Продукти",
    "dining_out": "Кафе і доставка",
    "transport": "Транспорт",
    "housing": "Житло",
    "health": "Здоровʼя",
    "household": "Дім і побут",
    "clothing": "Одяг",
    "entertainment": "Дозвілля",
    "subscriptions": "Підписки",
    "gifts": "Подарунки і донати",
    "pets": "Тварини",
    "hookah": "Кальян",
    "other": "Інше",
    "cash": "Готівка",
    "transfers": "Перекази",
}


def upgrade() -> None:
    # Added nullable, backfilled, then made NOT NULL — the three-step dance,
    # because a `server_default` here would silently give a future
    # owner-created row an empty label instead of failing loudly.
    op.add_column("categories", sa.Column("label", sa.String(length=64), nullable=True))
    categories = sa.table(
        "categories", sa.column("name", sa.String), sa.column("label", sa.String)
    )
    for slug, label in _LABELS.items():
        op.execute(categories.update().where(categories.c.name == slug).values(label=label))
    op.alter_column("categories", "label", nullable=False)

    # `status` gains a third value, 'suggested', by convention rather than by
    # constraint: there is no CHECK on this column to widen (see 0001), and
    # adding one now would be a schema change in service of a value the code
    # already owns.
    #
    # Nullable with no backfill and no default: NULL is the honest value for
    # every expense written before the model was ever asked for a suggestion,
    # and for every expense whose category the model was sure about. Cf.
    # 0004's bank_txn_key.
    op.add_column(
        "expenses",
        sa.Column(
            "suggested_category_id",
            sa.BigInteger(),
            sa.ForeignKey("categories.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("expenses", "suggested_category_id")
    op.drop_column("categories", "label")
