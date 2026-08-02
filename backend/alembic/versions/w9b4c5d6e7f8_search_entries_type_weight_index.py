"""search_entries (entity_type, weight) index — passe de substring da busca

A busca global roda 3 passes por tipo de entidade; o de SUBSTRING
(`norm_name LIKE '%x%' ORDER BY weight DESC LIMIT k`) não é sargável (wildcard
à esquerda) e, sem este índice, varria as ~87k linhas de jogador aplicando o
LIKE e depois ordenava num temp b-tree — ~200ms POR TECLA em nomes que só
casam no meio (ex.: "sight", "requiem").

Com (entity_type, weight) o banco caminha em ordem de weight (backward index
scan pro DESC) e para no LIMIT: nome de peso alto resolve em ~1ms, pior caso
(poucos matches, caminha o grupo todo) ~46ms — mas sem o sort. Beneficia
também o passe fuzzy e o /players/search (mesmo padrão sobre SearchEntry).

Revision ID: w9b4c5d6e7f8
Revises: u7f2a1b3c4d5
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'w9b4c5d6e7f8'
down_revision: Union[str, None] = 'u7f2a1b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_search_entries_type_weight', 'search_entries', ['entity_type', 'weight'])


def downgrade() -> None:
    op.drop_index('ix_search_entries_type_weight', table_name='search_entries')
