"""event_attendance: Event.attendance (decimal, default 1, per-event not per-participant)

Revision ID: c1d2e3f4a5b6
Revises: b9c4d6f8e1a3
Create Date: 2026-07-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b9c4d6f8e1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'events',
        sa.Column('attendance', sa.Float(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('events', 'attendance')
