"""Inbox de recuperação e fronteiras das streams do feed nativo.

Revision ID: zr1a2b3c4d5e
Revises: h8b1c4d7e2f5, zz2a3b4c5d6e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "zr1a2b3c4d5e"
down_revision: Union[str, tuple[str, str], None] = ("h8b1c4d7e2f5", "zz2a3b4c5d6e")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "native_feed_streams",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("completed_head_source_id", sa.String(length=64)),
        sa.Column("captured_head_source_id", sa.String(length=64)),
        sa.Column("scan_anchor_source_id", sa.String(length=64)),
        sa.Column("scan_head_source_id", sa.String(length=64)),
        sa.Column("next_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scan_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scan_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scan_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_at", sa.DateTime(timezone=True)),
        sa.Column("blocked_at", sa.DateTime(timezone=True)),
        sa.Column("blocked_reason", sa.Text()),
        sa.PrimaryKeyConstraint("kind", "region"),
    )
    op.create_table(
        "native_feed_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("discovered_by", sa.String(length=64)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kind", "region", "source_id", name="uq_native_feed_item_source"),
    )
    op.create_index(
        "ix_native_feed_items_apply", "native_feed_items",
        ["kind", "region", "status", "occurred_at", "source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_native_feed_items_apply", table_name="native_feed_items")
    op.drop_table("native_feed_items")
    op.drop_table("native_feed_streams")
