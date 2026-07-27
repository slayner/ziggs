"""lock administrative event assignments.

Revision ID: z2a3b4c5d6e7f
Revises: z1a2b3c4d5e6f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z2a3b4c5d6e7f"
down_revision: Union[str, None] = "z1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("event_assignments") as batch:
        batch.add_column(sa.Column("locked", sa.Boolean(), server_default="true", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("event_assignments") as batch:
        batch.drop_column("locked")
