"""durable scan laps

Revision ID: zg6b7c8d9e0f
Revises: zf5a6b7c8d9e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zg6b7c8d9e0f"
down_revision: Union[str, tuple[str, str], None] = "zf5a6b7c8d9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_laps",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("feed_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("expected_pages", sa.Integer(), nullable=False),
        sa.Column("completed_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    for column in ("region", "feed_type", "status", "started_at", "completed_at"):
        op.create_index(f"ix_scan_laps_{column}", "scan_laps", [column])
    op.add_column("scan_work_tasks", sa.Column("lap_id", sa.BigInteger()))
    op.create_index("ix_scan_work_tasks_lap_id", "scan_work_tasks", ["lap_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_work_tasks_lap_id", table_name="scan_work_tasks")
    op.drop_column("scan_work_tasks", "lap_id")
    op.drop_table("scan_laps")
