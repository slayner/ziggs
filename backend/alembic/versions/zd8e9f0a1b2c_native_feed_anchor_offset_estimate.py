"""Estimativa durável do offset da âncora nativa.

Revision ID: zd8e9f0a1b2c
Revises: zc7d8e9f0a1b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zd8e9f0a1b2c"
down_revision: Union[str, tuple[str, str], None] = "zc7d8e9f0a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("native_feed_streams", sa.Column("anchor_offset_estimate", sa.Integer()))
    op.add_column("native_feed_streams", sa.Column("anchor_offset_observed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("native_feed_streams", "anchor_offset_observed_at")
    op.drop_column("native_feed_streams", "anchor_offset_estimate")
