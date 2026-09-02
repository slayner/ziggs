"""adiciona prioridade à fila de warm de perfis.

Revision ID: zy6d7e8f9a0b
Revises: zx5c6d7e8f9a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zy6d7e8f9a0b"
down_revision: Union[str, None] = "zx5c6d7e8f9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("albion_players", "guild_profiles", "alliance_profiles"):
        op.add_column(table, sa.Column("refresh_priority", sa.Integer(), nullable=False, server_default="0"))
        op.alter_column(table, "refresh_priority", server_default=None)


def downgrade() -> None:
    for table in ("alliance_profiles", "guild_profiles", "albion_players"):
        op.drop_column(table, "refresh_priority")
