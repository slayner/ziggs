"""event drafts and signup policy.

Revision ID: z1a2b3c4d5e6f
Revises: y2b3c4d5e6f7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z1a2b3c4d5e6f"
down_revision: Union[str, None] = "y2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE event_state ADD VALUE IF NOT EXISTS 'DRAFT'")

    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("signup_mode", sa.String(length=32), server_default="signup", nullable=False))
        batch.add_column(sa.Column("assignment_mode", sa.String(length=32), server_default="hybrid", nullable=False))
        batch.add_column(sa.Column("autofill_mode", sa.String(length=32), server_default="manual", nullable=False))
        batch.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))

    # Existing rows are already public; preserve that fact for the new field.
    # Every row predates DRAFT, so publish all without referencing the new enum
    # label in the same PostgreSQL transaction that added it.
    op.execute("UPDATE events SET published_at=created_at WHERE published_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_column("published_at")
        batch.drop_column("autofill_mode")
        batch.drop_column("assignment_mode")
        batch.drop_column("signup_mode")
    # PostgreSQL enum values are intentionally not removed.
