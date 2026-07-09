"""battle_id_probes: memória de sondagem do battle_sweeper

Revision ID: f1a9c3e7b5d2
Revises: d0c1f2a3b4e5
Create Date: 2026-07-03 00:00:00.000000

Uma linha por albion_id sondado no endpoint de detalhe (fora da janela de
offset 10000 da listagem). status='found' (achado em >=1 região, light-
capturado) | 'missing' (404 nos 3 hosts). Sem esta tabela o sweeper re-sondaria
todos os buracos a cada ciclo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a9c3e7b5d2'
down_revision: Union[str, None] = 'd0c1f2a3b4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'battle_id_probes',
        sa.Column('albion_id', sa.String(64), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('region', sa.String(16), nullable=True),
        sa.Column('probed_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('battle_id', sa.Integer(),
                  sa.ForeignKey('battles.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_battle_id_probes_status', 'battle_id_probes', ['status'])


def downgrade() -> None:
    op.drop_index('ix_battle_id_probes_status', table_name='battle_id_probes')
    op.drop_table('battle_id_probes')