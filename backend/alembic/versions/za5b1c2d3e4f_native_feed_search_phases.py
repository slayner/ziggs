"""Fases de scan e busca exponencial/binária do feed nativo.

Revision ID: za5b1c2d3e4f
Revises: zf4e8a1c2b3d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "za5b1c2d3e4f"
down_revision: Union[str, tuple[str, str], None] = "zf4e8a1c2b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("native_feed_streams", sa.Column("scan_phase", sa.String(length=16)))
    op.add_column("native_feed_streams", sa.Column("search_low_offset", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("native_feed_streams", sa.Column("search_high_offset", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("native_feed_streams", "search_high_offset")
    op.drop_column("native_feed_streams", "search_low_offset")
    op.drop_column("native_feed_streams", "scan_phase")