"""persist regear recognition evidence.

Revision ID: z4a5b6c7d8e9f
Revises: z3a4b5c6d7e8f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z4a5b6c7d8e9f"
down_revision: Union[str, None] = "z3a4b5c6d7e8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("regear_requests") as batch:
        batch.add_column(sa.Column("recognition_method", sa.String(length=16), server_default="manual", nullable=False))
        batch.add_column(sa.Column("recognition_confidence", sa.String(length=16), server_default="low", nullable=False))
        batch.add_column(sa.Column("recognition_candidates", sa.JSON(), server_default="[]", nullable=False))
        batch.add_column(sa.Column("recognition_window_match", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("recognition_fallback_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("regear_requests") as batch:
        batch.drop_column("recognition_fallback_reason")
        batch.drop_column("recognition_window_match")
        batch.drop_column("recognition_candidates")
        batch.drop_column("recognition_confidence")
        batch.drop_column("recognition_method")
