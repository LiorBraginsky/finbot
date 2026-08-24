"""stage2_5 bank txn key

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no backfill: existing rows are all text and voice, and NULL
    # is the honest value for a column that only ever means something for a
    # bank-feed screenshot — the mirror image of 0002's lesson, where a
    # server default made every pre-existing row claimable. Here nothing
    # changes meaning for a row that already exists.
    op.add_column(
        "expenses",
        sa.Column("bank_txn_key", sa.String(length=64), nullable=True),
    )
    # Approach C2's dedup guarantee, enforced by Postgres rather than by
    # application code: a keyed insert's `ON CONFLICT DO NOTHING` against
    # this constraint is what makes re-sending the same screenshot write
    # nothing new. NULLs are distinct in Postgres, so this is silent for
    # every text/voice expense; it is not conditioned on `deleted_at`, so a
    # 🗑'd bank row is deliberately not resurrected by a re-send.
    op.create_unique_constraint(
        "uq_expenses_user_bank_txn_key", "expenses", ["user_id", "bank_txn_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_expenses_user_bank_txn_key", "expenses", type_="unique")
    op.drop_column("expenses", "bank_txn_key")
