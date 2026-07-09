"""lootlog_thread outbox: dirty + archived flags (espelho do regear_thread)

Revision ID: d0a1b2c3e4f5
Revises: c2a8e6f1b7d4, f6d7bb26a375
Create Date: 2026-07-07 02:00:00.000000

Merge das duas heads pré-existentes (alliance_support + event_thread_archived)
+ adiciona `lootlog_thread_dirty`/`lootlog_thread_archived` (espelho do
regear_thread). `lootlog_thread_dirty` acende quando o evento entra em
IN_PROGRESS (o bot cria a thread de log no canal dedicado e limpa via
/bot/events/.../lootlog-thread-synced). `lootlog_thread_archived` separa o estado
de archive do `lootlog_thread_id` (que fica setado) pra tirar o evento terminal
da fila de arquivamento do loop. `lootlog_thread_id` já existia.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0a1b2c3e4f5'
down_revision: Union[str, Sequence[str], None] = ('c2a8e6f1b7d4', 'f6d7bb26a375')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column(
            'lootlog_thread_dirty', sa.Boolean(),
            nullable=False, server_default=sa.text('0')
        ))
        b.add_column(sa.Column(
            'lootlog_thread_archived', sa.Boolean(),
            nullable=False, server_default=sa.text('0')
        ))


def downgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.drop_column('lootlog_thread_archived')
        b.drop_column('lootlog_thread_dirty')