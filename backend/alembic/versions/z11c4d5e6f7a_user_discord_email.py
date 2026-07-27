"""store verified Discord email for future account federation/recovery

Revision ID: z11c4d5e6f7a
Revises: z10f1a2b3c4d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "z11c4d5e6f7a"
down_revision: Union[str, None] = "z10f1a2b3c4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "email_verified")
    op.drop_column("users", "email")
