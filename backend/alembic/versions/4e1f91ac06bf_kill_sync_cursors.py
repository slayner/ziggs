"""kill_sync_cursors: cursor de backfill de kills (igual battle_sync_cursors).

Revision ID: 4e1f91ac06bf
Revises: a0b1c2d3e4f5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4e1f91ac06bf"
down_revision: Union[str, tuple[str, str], None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kill_sync_cursors",
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("region"),
    )


def downgrade() -> None:
    op.drop_table("kill_sync_cursors")
