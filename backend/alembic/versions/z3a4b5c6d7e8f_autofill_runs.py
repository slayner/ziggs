"""track autofill runs for preview/undo.

Revision ID: z3a4b5c6d7e8f
Revises: z2a3b4c5d6e7f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z3a4b5c6d7e8f"
down_revision: Union[str, None] = "z2a3b4c5d6e7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("event_assignments") as batch:
        batch.add_column(sa.Column("autofill_run_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_event_assignments_autofill_run_id", ["autofill_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("event_assignments") as batch:
        batch.drop_index("ix_event_assignments_autofill_run_id")
        batch.drop_column("autofill_run_id")
