"""event_thread_archived: flag p/ o bot não re-arquivar a thread do embed a
cada tick

Revision ID: f6d7bb26a375
Revises: c1d2e3f4a5b6
Create Date: 2026-07-07 22:00:00.000000

Mesma ideia de regear_thread_archived, pra thread do próprio embed do evento
(📑 EVENTO #N, quando a sala de revisão está configurada) — depois que o bot
tranca (lock) a thread de um evento terminal, este flag tira o evento da
lista de arquivamento do loop.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6d7bb26a375'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column(
            'event_thread_archived', sa.Boolean(),
            nullable=False, server_default=sa.text('0')
        ))


def downgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.drop_column('event_thread_archived')
