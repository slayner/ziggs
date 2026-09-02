"""event_embed_dirty: outbox do embed por evento

Revision ID: g3b9c2d5e6f7
Revises: f2a8b1c4d5e6
Create Date: 2026-07-04 00:00:00.000000

Adiciona `events.event_embed_dirty` — flag outbox para o bot-v2 recriar o
embed 📑 EVENTO #N da thread quando uma mutação (transição, tipo, passo,
participante, morte) muda o conteúdo. Limpo via /bot/events/.../embed-synced.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g3b9c2d5e6f7'
down_revision: Union[str, None] = 'f2a8b1c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column('event_embed_dirty', sa.Boolean(),
                               nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.drop_column('event_embed_dirty')