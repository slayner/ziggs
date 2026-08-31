"""player stats verification timestamp

Revision ID: h8b1c4d7e2f5
Revises: f13ee9cd3757
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h8b1c4d7e2f5"
down_revision: Union[str, None] = "f13ee9cd3757"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("albion_players")}
    if "stats_updated_at" not in columns:
        op.add_column(
            "albion_players",
            sa.Column("stats_updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("albion_players")}
    if "stats_updated_at" in columns:
        op.drop_column("albion_players", "stats_updated_at")
