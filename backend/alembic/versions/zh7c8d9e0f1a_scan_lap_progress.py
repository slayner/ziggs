"""persist scan lap progress timestamp

Revision ID: zh7c8d9e0f1a
Revises: zg6b7c8d9e0f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zh7c8d9e0f1a"
down_revision: Union[str, tuple[str, str], None] = "zg6b7c8d9e0f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_laps", sa.Column("last_progress_at", sa.DateTime(timezone=True)))
    op.create_index("ix_scan_laps_last_progress_at", "scan_laps", ["last_progress_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_laps_last_progress_at", table_name="scan_laps")
    op.drop_column("scan_laps", "last_progress_at")
