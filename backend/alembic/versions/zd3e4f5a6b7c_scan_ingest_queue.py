"""durable scan ingest queue

Revision ID: zd3e4f5a6b7c
Revises: zc2d3e4f5a6b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "zd3e4f5a6b7c"
down_revision: Union[str, tuple[str, str], None] = "zc2d3e4f5a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_scan_work_tasks_active_page", table_name="scan_work_tasks")
    op.create_index(
        "uq_scan_work_tasks_active_page",
        "scan_work_tasks",
        ["region", "feed_type", "page_offset"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'reported')"),
    )
    op.create_table(
        "scan_ingest_payloads",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("feed_type", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("task_id", name="uq_scan_ingest_payloads_task_id"),
    )
    op.create_index("ix_scan_ingest_payloads_task_id", "scan_ingest_payloads", ["task_id"])
    op.create_index("ix_scan_ingest_payloads_worker_id", "scan_ingest_payloads", ["worker_id"])
    op.create_index("ix_scan_ingest_payloads_region", "scan_ingest_payloads", ["region"])
    op.create_index("ix_scan_ingest_payloads_feed_type", "scan_ingest_payloads", ["feed_type"])
    op.create_index("ix_scan_ingest_payloads_status", "scan_ingest_payloads", ["status"])
    op.create_index("ix_scan_ingest_payloads_next_attempt_at", "scan_ingest_payloads", ["next_attempt_at"])
    op.create_index("ix_scan_ingest_payloads_created_at", "scan_ingest_payloads", ["created_at"])


def downgrade() -> None:
    op.drop_table("scan_ingest_payloads")
    op.drop_index("uq_scan_work_tasks_active_page", table_name="scan_work_tasks")
    op.create_index(
        "uq_scan_work_tasks_active_page",
        "scan_work_tasks",
        ["region", "feed_type", "page_offset"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )
