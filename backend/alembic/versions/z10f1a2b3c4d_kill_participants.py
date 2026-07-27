"""store kill participant snapshots for Juicy Kill embeds

Revision ID: z10f1a2b3c4d
Revises: z9e0f1a2b3c4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "z10f1a2b3c4d"
down_revision: Union[str, None] = "z9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player_kill_events", sa.Column("participants", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("player_kill_events", "participants")
