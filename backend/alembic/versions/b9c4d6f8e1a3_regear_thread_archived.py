"""regear_thread_archived: flag p/ o bot não re-arquivar a thread a cada tick

Revision ID: b9c4d6f8e1a3
Revises: l8a3c5e7d9b2
Create Date: 2026-07-07 01:00:00.000000

Depois que o bot arquiva (lock) a thread de regear de um evento terminal, este
flag tira o evento da lista de arquivamento do loop. regear_thread_id fica
setado (o _regear_summary do review finalizado ainda precisa dele).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9c4d6f8e1a3'
down_revision: Union[str, None] = 'l8a3c5e7d9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column(
            'regear_thread_archived', sa.Boolean(),
            nullable=False, server_default=sa.text('0')
        ))


def downgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.drop_column('regear_thread_archived')