"""stage2 voice

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Voice only — Telegram reports it on the incoming update itself
    # (`message.voice.duration`), so it is captured once at receipt time
    # rather than re-derived later, when only this row is left
    # (core.models.IncomingMessage.duration_seconds).
    op.add_column(
        "messages",
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "duration_seconds")
