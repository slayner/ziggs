"""tentativas hedge e estado de rate limit por região.

Revision ID: zz4a5b6c7d8e
Revises: zz3a4b5c6d7e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zz4a5b6c7d8e"
down_revision: Union[str, None] = "zz3a4b5c6d7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_work_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("lease_token", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="claimed"),
        sa.Column("is_hedge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("report_status_code", sa.Integer()),
        sa.UniqueConstraint("lease_token", name="uq_scan_work_attempt_lease"),
    )
    op.create_index("ix_scan_work_attempts_task_id", "scan_work_attempts", ["task_id"])
    op.create_index("ix_scan_work_attempts_worker_id", "scan_work_attempts", ["worker_id"])
    op.create_index("ix_scan_work_attempts_lease_token", "scan_work_attempts", ["lease_token"])
    op.create_index("ix_scan_work_attempts_status", "scan_work_attempts", ["status"])
    op.create_index("ix_scan_work_attempts_claimed_at", "scan_work_attempts", ["claimed_at"])
    op.create_index("ix_scan_work_attempts_expires_at", "scan_work_attempts", ["expires_at"])
    op.create_index("ix_scan_work_attempts_completed_at", "scan_work_attempts", ["completed_at"])
    op.execute("""
        INSERT INTO scan_work_attempts
            (task_id, worker_id, lease_token, status, is_hedge, claimed_at, expires_at)
        SELECT id, claimed_by, lease_token, 'claimed', false, claimed_at, claim_expires_at
        FROM scan_work_tasks
        WHERE status = 'claimed'
          AND claimed_by IS NOT NULL
          AND lease_token IS NOT NULL
          AND claimed_at IS NOT NULL
          AND claim_expires_at IS NOT NULL
    """)
    op.create_table(
        "scan_host_rate_states",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(length=16), nullable=False, unique=True),
        sa.Column("backoff_until", sa.DateTime(timezone=True)),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("consecutive_rate_limits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scan_host_rate_states_region", "scan_host_rate_states", ["region"])
    op.create_index("ix_scan_host_rate_states_backoff_until", "scan_host_rate_states", ["backoff_until"])
    op.create_index("ix_scan_host_rate_states_updated_at", "scan_host_rate_states", ["updated_at"])


def downgrade() -> None:
    op.drop_table("scan_host_rate_states")
    op.drop_table("scan_work_attempts")
