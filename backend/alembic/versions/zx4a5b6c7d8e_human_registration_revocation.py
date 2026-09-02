"""preserve human Discord registration revocations

Revision ID: zx4a5b6c7d8e
Revises: zw3a4b5c6d7f
"""
from alembic import op
import sqlalchemy as sa


revision = "zx4a5b6c7d8e"
down_revision = "zw3a4b5c6d7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_registrations", sa.Column("human_revoked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_registrations", "human_revoked_at")
