"""registra itens lootados para recuperação contínua de renders.

Revision ID: zv4b5c6d7e8f
Revises: zy6d7e8f9a0b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zv4b5c6d7e8f"
down_revision: Union[str, None] = "zy6d7e8f9a0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "known_looted_items",
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("key", sa.String(length=200), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("known_looted_items")
