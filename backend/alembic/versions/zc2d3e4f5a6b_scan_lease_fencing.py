"""scan lease fencing and active-page invariant

Revision ID: zc2d3e4f5a6b
Revises: zb1c2d3e4f5a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zc2d3e4f5a6b"
down_revision: Union[str, tuple[str, str], None] = "zb1c2d3e4f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_work_tasks", sa.Column("lease_token", sa.String(32), nullable=True))
    op.add_column(
        "scan_work_tasks",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scan_work_tasks_lease_token", "scan_work_tasks", ["lease_token"])
    op.create_index(
        "uq_scan_work_tasks_active_page",
        "scan_work_tasks",
        ["region", "feed_type", "page_offset"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )


def downgrade() -> None:
    op.drop_index("uq_scan_work_tasks_active_page", table_name="scan_work_tasks")
    op.drop_index("ix_scan_work_tasks_lease_token", table_name="scan_work_tasks")
    op.drop_column("scan_work_tasks", "attempt_count")
    op.drop_column("scan_work_tasks", "lease_token")
