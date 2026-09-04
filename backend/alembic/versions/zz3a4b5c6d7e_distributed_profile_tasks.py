"""tarefas VPS para perfis e afinidade de batalha.

Revision ID: zz3a4b5c6d7e
Revises: zz2a3b4c5d6e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zz3a4b5c6d7e"
down_revision: Union[str, None] = "zz2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("scan_work_tasks", "feed_type", type_=sa.String(length=32))
    op.add_column("scan_work_tasks", sa.Column("subject_id", sa.String(length=64)))
    op.add_column("scan_work_tasks", sa.Column("preferred_worker_id", sa.String(length=64)))
    op.add_column("scan_work_tasks", sa.Column("affinity_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_scan_work_subject", "scan_work_tasks", ["feed_type", "region", "subject_id"])
    op.create_index("ix_scan_work_preferred", "scan_work_tasks", ["preferred_worker_id", "affinity_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_work_preferred", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_subject", table_name="scan_work_tasks")
    op.drop_column("scan_work_tasks", "affinity_expires_at")
    op.drop_column("scan_work_tasks", "preferred_worker_id")
    op.drop_column("scan_work_tasks", "subject_id")
    op.alter_column("scan_work_tasks", "feed_type", type_=sa.String(length=16))
