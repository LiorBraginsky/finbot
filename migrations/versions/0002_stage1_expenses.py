"""stage1 expenses

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The thirteen categories, spelled literally: migrations never import finbot,
# so this cannot reference finbot.core.categories.catalog.CATALOG directly.
# tests/integration/test_categories_seed.py asserts the two stay equal.
_CATEGORIES: list[dict[str, object]] = [
    {"name": "groceries", "emoji": "🛒"},
    {"name": "dining_out", "emoji": "🍽"},
    {"name": "transport", "emoji": "🚕"},
    {"name": "housing", "emoji": "🏠"},
    {"name": "health", "emoji": "💊"},
    {"name": "household", "emoji": "🧴"},
    {"name": "clothing", "emoji": "👕"},
    {"name": "entertainment", "emoji": "🎬"},
    {"name": "subscriptions", "emoji": "📱"},
    {"name": "gifts", "emoji": "🎁"},
    {"name": "pets", "emoji": "🐾"},
    {"name": "hookah", "emoji": "💨"},
    {"name": "other", "emoji": "🗂"},
]


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("emoji", sa.String(length=8), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "merged_into_id", sa.BigInteger(), sa.ForeignKey("categories.id"), nullable=True
        ),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    categories_table = sa.table(
        "categories",
        sa.column("name", sa.String),
        sa.column("emoji", sa.String),
        sa.column("is_system", sa.Boolean),
        sa.column("status", sa.String),
        sa.column("created_by", sa.BigInteger),
    )
    op.bulk_insert(
        categories_table,
        [
            {
                "name": row["name"],
                "emoji": row["emoji"],
                "is_system": True,
                "status": "active",
                "created_by": None,
            }
            for row in _CATEGORIES
        ],
    )

    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "done",
                "failed",
                "skipped",
                name="message_status",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "messages",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "messages",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "messages",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    # Plain, not partial, deliberately: Alembic compares postgresql_where as
    # text and produces spurious diffs, and every addition to
    # test_schema_matches_models.py's exclusion list is a liability. At two
    # users' volume the index shape is irrelevant; not weakening the drift
    # guard is not.
    op.create_index(
        "ix_messages_status_next_attempt_at",
        "messages",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "extractions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.BigInteger(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "invalid_json",
                "failed",
                name="extraction_status",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("raw_response", postgresql.JSONB(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_extractions_message_id", "extractions", ["message_id"])

    op.create_table(
        "expenses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.BigInteger(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category_id", sa.BigInteger(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="UAH"),
        sa.Column("amount_uah", sa.Numeric(12, 2), nullable=False),
        sa.Column("fx_rate", sa.Numeric(14, 6), nullable=False, server_default="1"),
        sa.Column("fx_rate_date", sa.Date(), nullable=True),
        sa.Column("occurred_at", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bot_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_expenses_occurred_at", "expenses", ["occurred_at"])
    op.create_index("ix_expenses_message_id", "expenses", ["message_id"])

    op.create_table(
        "corrections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("expense_id", sa.BigInteger(), sa.ForeignKey("expenses.id"), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("corrected_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("corrections")

    op.drop_index("ix_expenses_message_id", table_name="expenses")
    op.drop_index("ix_expenses_occurred_at", table_name="expenses")
    op.drop_table("expenses")

    op.drop_index("ix_extractions_message_id", table_name="extractions")
    op.drop_table("extractions")

    op.drop_index("ix_messages_status_next_attempt_at", table_name="messages")
    op.drop_column("messages", "last_error")
    op.drop_column("messages", "next_attempt_at")
    op.drop_column("messages", "attempts")
    op.drop_column("messages", "status")

    op.drop_table("categories")
