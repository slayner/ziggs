"""participant_is_valid: regular vs irregular

Revision ID: i5e1f7a2b8c3
Revises: h4d0e6b8f9a1
Create Date: 2026-07-05 00:00:00.000000

Adiciona `event_participants.is_valid` (nullable). NULL = derivado (válido sse
o user tem EventSignup no evento); True/False = override do admin (drag entre
as colunas Irregulares/Válidos). Irregular no finalize: sem split/attendance.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i5e1f7a2b8c3'
down_revision: Union[str, None] = 'i5e1a8b2c4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('event_participants') as b:
        b.add_column(sa.Column('is_valid', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('event_participants') as b:
        b.drop_column('is_valid')
