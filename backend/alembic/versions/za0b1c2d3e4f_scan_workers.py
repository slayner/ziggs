"""scan_workers + scan_work_tasks: distribui scanning de batalhas pra VPS workers.

Revision ID: za0b1c2d3e4f
Revises: 4e1f91ac06bf
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "za0b1c2d3e4f"
down_revision: Union[str, tuple[str, str], None] = "4e1f91ac06bf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_workers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("region_pref", sa.String(length=16), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("total_tasks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_battles_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_missing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_task_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("worker_id", name="uq_scan_workers_worker_id"),
    )
    op.create_index("ix_scan_workers_worker_id", "scan_workers", ["worker_id"])
    op.create_index("ix_scan_workers_region_pref", "scan_workers", ["region_pref"])
    op.create_index("ix_scan_workers_last_heartbeat", "scan_workers", ["last_heartbeat"])
    op.create_index("ix_scan_workers_status", "scan_workers", ["status"])

    op.create_table(
        "scan_work_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("battle_id_start", sa.BigInteger(), nullable=False),
        sa.Column("battle_id_end", sa.BigInteger(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scan_work_tasks_region", "scan_work_tasks", ["region"])
    op.create_index("ix_scan_work_tasks_priority", "scan_work_tasks", ["priority"])
    op.create_index("ix_scan_work_tasks_status", "scan_work_tasks", ["status"])
    op.create_index("ix_scan_work_tasks_claimed_by", "scan_work_tasks", ["claimed_by"])
    op.create_index("ix_scan_work_tasks_claim_expires_at", "scan_work_tasks", ["claim_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_work_tasks_claim_expires_at", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_tasks_claimed_by", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_tasks_status", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_tasks_priority", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_tasks_region", table_name="scan_work_tasks")
    op.drop_table("scan_work_tasks")
    op.drop_index("ix_scan_workers_status", table_name="scan_workers")
    op.drop_index("ix_scan_workers_last_heartbeat", table_name="scan_workers")
    op.drop_index("ix_scan_workers_region_pref", table_name="scan_workers")
    op.drop_index("ix_scan_workers_worker_id", table_name="scan_workers")
    op.drop_table("scan_workers")