"""add opaque public links for event escalations

Revision ID: zs1a2b3c4d5e
Revises: zr8f9a0b1c2d
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union
import secrets

from alembic import op
import sqlalchemy as sa


revision: str = "zs1a2b3c4d5e"
down_revision: Union[str, None] = "zr8f9a0b1c2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("escalation_token", sa.String(length=32), nullable=True))
    bind = op.get_bind()
    for event_id in bind.execute(sa.text("SELECT id FROM events")).scalars():
        bind.execute(
            sa.text("UPDATE events SET escalation_token = :token WHERE id = :id"),
            {"token": secrets.token_urlsafe(24), "id": event_id},
        )
    op.alter_column("events", "escalation_token", nullable=False)
    op.create_index("ix_events_escalation_token", "events", ["escalation_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_events_escalation_token", table_name="events")
    op.drop_column("events", "escalation_token")
