"""scan_work_tasks: muda de gap scan para feed polling

Revision ID: zb1c2d3e4f5a
Revises: za0b1c2d3e4f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zb1c2d3e4f5a"
down_revision: Union[str, tuple[str, str], None] = "za0b1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("scan_work_tasks")

    op.create_table(
        "scan_work_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(16), nullable=False, index=True),
        sa.Column("feed_type", sa.String(16), nullable=False, index=True),
        sa.Column("page_offset", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("claimed_by", sa.String(64), index=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), index=True),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column("scan_workers", sa.Column("total_kills_found", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("scan_workers", "total_kills_found")
    op.drop_table("scan_work_tasks")

    op.create_table(
        "scan_work_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(16), nullable=False, index=True),
        sa.Column("battle_id_start", sa.BigInteger(), nullable=False),
        sa.Column("battle_id_end", sa.BigInteger(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("claimed_by", sa.String(64), index=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), index=True),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    )