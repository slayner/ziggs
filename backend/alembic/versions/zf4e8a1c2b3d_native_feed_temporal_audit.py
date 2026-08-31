"""Estado e auditoria da recuperação temporal do feed.

Revision ID: zf4e8a1c2b3d
Revises: zy2b3c4d5e6f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zf4e8a1c2b3d"
down_revision: Union[str, tuple[str, str], None] = "zy2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("native_feed_streams", sa.Column("scan_anchor_occurred_at", sa.DateTime(timezone=True)))
    op.add_column("native_feed_streams", sa.Column("scan_id", sa.String(length=36)))
    op.add_column("native_feed_streams", sa.Column("scan_resolution", sa.String(length=16)))
    op.add_column("native_feed_streams", sa.Column("scan_last_progress_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("native_feed_streams", "scan_last_progress_at")
    op.drop_column("native_feed_streams", "scan_resolution")
    op.drop_column("native_feed_streams", "scan_id")
    op.drop_column("native_feed_streams", "scan_anchor_occurred_at")
