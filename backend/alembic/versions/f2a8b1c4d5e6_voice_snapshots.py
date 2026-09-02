"""voice_snapshots: contagem de snapshots de voz p/ VOICE_PERCENT

Revision ID: f2a8b1c4d5e6
Revises: e1f5a7b9c3d4
Create Date: 2026-07-04 00:00:00.000000

Adiciona `events.total_snapshots` (denominador) e
`event_participants.snapshots_present` (numerador por jogador). O freeze em
IN_PROGRESS→DEFINITION calcula `base_percent = round(present*100/total)` e
aplica o desconto de trial em `percent`. Ver `app/services/events.py`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a8b1c4d5e6'
down_revision: Union[str, None] = 'e1f5a7b9c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column('total_snapshots', sa.Integer(),
                               nullable=False, server_default='0'))
    with op.batch_alter_table('event_participants') as b:
        b.add_column(sa.Column('snapshots_present', sa.Integer(),
                               nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('event_participants') as b:
        b.drop_column('snapshots_present')
    with op.batch_alter_table('events') as b:
        b.drop_column('total_snapshots')