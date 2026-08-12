"""persist overlap stride per scan lap

Revision ID: zi8d9e0f1a2b
Revises: zh7c8d9e0f1a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zi8d9e0f1a2b"
down_revision: Union[str, tuple[str, str], None] = "zh7c8d9e0f1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_laps", sa.Column(
        "page_stride", sa.Integer(), nullable=False, server_default="51"
    ))


def downgrade() -> None:
    op.drop_column("scan_laps", "page_stride")
