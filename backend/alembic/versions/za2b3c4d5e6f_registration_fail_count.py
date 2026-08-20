"""registration_checker: fail_count + last_fail_at before revoking

Revision ID: za2b3c4d5e6f
Revises: zz1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "za2b3c4d5e6f"
down_revision: Union[str, None] = "zz1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_registrations",
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bot_registrations",
        sa.Column("last_fail_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_registrations", "last_fail_at")
    op.drop_column("bot_registrations", "fail_count")