"""track missing CDN renders for background recovery

Revision ID: zr8f9a0b1c2d
Revises: zq6f7a8b9c0d
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zr8f9a0b1c2d"
down_revision: Union[str, None] = "zq6f7a8b9c0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "render_misses",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("quality", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("miss_count", sa.Integer(), nullable=False),
        sa.Column("first_missing_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("kind", "key", "quality", "size"),
    )
    op.create_index("ix_render_misses_next_retry_at", "render_misses", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_render_misses_next_retry_at", table_name="render_misses")
    op.drop_table("render_misses")
