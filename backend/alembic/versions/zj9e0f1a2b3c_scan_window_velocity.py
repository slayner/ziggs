"""scan stream window velocity

Revision ID: zj9e0f1a2b3c
Revises: zi8d9e0f1a2b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zj9e0f1a2b3c"
down_revision: Union[str, tuple[str, str], None] = "zi8d9e0f1a2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_stream_states", sa.Column("window_items_per_min", sa.Float()))
    op.add_column("scan_stream_states", sa.Column("last_head_at", sa.DateTime(timezone=True)))
    op.create_index("ix_scan_stream_states_last_head_at", "scan_stream_states", ["last_head_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_stream_states_last_head_at", table_name="scan_stream_states")
    op.drop_column("scan_stream_states", "last_head_at")
    op.drop_column("scan_stream_states", "window_items_per_min")
