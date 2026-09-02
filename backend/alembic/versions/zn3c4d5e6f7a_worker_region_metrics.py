"""learned worker region affinity

Revision ID: zn3c4d5e6f7a
Revises: zm2b3c4d5e6f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zn3c4d5e6f7a"
down_revision: Union[str, tuple[str, str], None] = "zm2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_worker_region_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ewma_latency_ms", sa.Float()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("worker_id", "region", name="uq_scan_worker_region_metric"),
    )
    for column in ("worker_id", "region", "last_seen_at"):
        op.create_index(f"ix_scan_worker_region_metrics_{column}", "scan_worker_region_metrics", [column])


def downgrade() -> None:
    op.drop_table("scan_worker_region_metrics")
