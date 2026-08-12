"""scan stream operational controls

Revision ID: zl1a2b3c4d5e
Revises: zk0f1a2b3c4d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zl1a2b3c4d5e"
down_revision: Union[str, tuple[str, str], None] = "zk0f1a2b3c4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_stream_states", sa.Column(
        "paused", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.create_index("ix_scan_stream_states_paused", "scan_stream_states", ["paused"])


def downgrade() -> None:
    op.drop_index("ix_scan_stream_states_paused", table_name="scan_stream_states")
    op.drop_column("scan_stream_states", "paused")
