"""adaptive recent scan depth

Revision ID: zf5a6b7c8d9e
Revises: ze4f5a6b7c8d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zf5a6b7c8d9e"
down_revision: Union[str, tuple[str, str], None] = "ze4f5a6b7c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_stream_states", sa.Column(
        "recent_pages", sa.Integer(), nullable=False, server_default="8"
    ))


def downgrade() -> None:
    op.drop_column("scan_stream_states", "recent_pages")
