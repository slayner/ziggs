"""append-only scan incident history

Revision ID: zm2b3c4d5e6f
Revises: zl1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "zm2b3c4d5e6f"
down_revision: Union[str, tuple[str, str], None] = "zl1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_incidents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("worker_id", sa.String(64)),
        sa.Column("region", sa.String(16)),
        sa.Column("feed_type", sa.String(16)),
        sa.Column("task_id", sa.BigInteger()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("event", "actor", "worker_id", "region", "feed_type", "task_id", "created_at"):
        op.create_index(f"ix_scan_incidents_{column}", "scan_incidents", [column])


def downgrade() -> None:
    op.drop_table("scan_incidents")
