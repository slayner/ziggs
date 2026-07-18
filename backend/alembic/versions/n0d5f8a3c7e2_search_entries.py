"""search_entries: índice pré-normalizado pra busca global (WS2 — perf)

Revision ID: n0d5f8a3c7e2
Revises: m9b4d7e2a1c8
Create Date: 2026-07-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'n0d5f8a3c7e2'
down_revision: Union[str, None] = 'm9b4d7e2a1c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    big_int = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        'search_entries',
        sa.Column('id', big_int, nullable=False),
        sa.Column('entity_type', sa.String(length=16), nullable=False),
        sa.Column('entity_id', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('norm_name', sa.String(length=255), nullable=False),
        sa.Column('name_len', sa.Integer(), nullable=False),
        sa.Column('region', sa.String(length=16), nullable=True),
        sa.Column('guild_name', sa.String(length=255), nullable=True),
        sa.Column('alliance_name', sa.String(length=255), nullable=True),
        sa.Column('guild_count', sa.Integer(), nullable=True),
        sa.Column('weight', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_type', 'entity_id'),
    )
    # text_pattern_ops: no Postgres, um índice comum em coluna text/varchar só
    # acelera range/prefixo (col >= lo AND col < hi, ver search_norm.prefix_range)
    # em locale "C" — com o locale padrão (ex: en_US.UTF-8) o planner ignora o
    # índice pra esse tipo de comparação. text_pattern_ops força ordenação
    # binária nesse índice específico, sem mudar o collation da coluna/tabela.
    # Ignorado pelo SQLite (dialeto não reconhece o kwarg).
    op.create_index(
        'ix_search_entries_type_norm', 'search_entries', ['entity_type', 'norm_name'],
        postgresql_ops={'norm_name': 'text_pattern_ops'},
    )
    op.create_index('ix_search_entries_type_len', 'search_entries', ['entity_type', 'name_len'])
    # Necessária pra seção de batalhas do _search (busca por aliança) — coluna
    # já existia sem índice, full-scan em battle_guilds pra cada busca.
    op.create_index('ix_battle_guilds_alliance_id', 'battle_guilds', ['alliance_id'])


def downgrade() -> None:
    op.drop_index('ix_battle_guilds_alliance_id', table_name='battle_guilds')
    op.drop_index('ix_search_entries_type_len', table_name='search_entries')
    op.drop_index('ix_search_entries_type_norm', table_name='search_entries')
    op.drop_table('search_entries')
