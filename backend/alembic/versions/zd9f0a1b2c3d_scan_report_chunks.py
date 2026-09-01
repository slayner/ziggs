"""chunks idempotentes para reports grandes do scanner

Revision ID: zd9f0a1b2c3d
Revises: zd8e9f0a1b2c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zd9f0a1b2c3d"
down_revision: Union[str, tuple[str, str], None] = "zd8e9f0a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_report_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("payload_chunk", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "task_id", "lease_token", "payload_sha256", "chunk_index",
            name="uq_scan_report_chunk",
        ),
    )
    for column in ("task_id", "lease_token", "payload_sha256", "created_at"):
        op.create_index(f"ix_scan_report_chunks_{column}", "scan_report_chunks", [column])


def downgrade() -> None:
    op.drop_table("scan_report_chunks")
