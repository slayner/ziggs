"""purge batalhas com < 10 jogadores (DEEP_PROCESS_MIN_PLAYERS subiu de 0 pra 10).

Revision ID: z7c8d9e0f1a2
Revises: z6b7c8d9e0f1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z7c8d9e0f1a2"
down_revision: Union[str, None] = "z6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batalhas com < 10 jogadores não são mais armazenadas (ver
    # DEEP_PROCESS_MIN_PLAYERS em battle_tracker.py).
    #
    # ANTES de deletar: insere BattleIdProbe 'missing' pra cada albion_id
    # purgado que ainda não tem probe. Sem isso, o sweeper re-sonda cada ID
    # purgado uma vez (acha batalha pequena → upsert devolve None → grava
    # probe → nunca mais) — uma sondagem desperdiçada por ID. Com o probe
    # já gravado, o sweeper pula o ID direto.
    #
    # A PK do probe é global (só albion_id, sem região) — um probe 'missing'
    # pra um ID impede re-sondagem em TODAS as regiões. Isso é o MESMO
    # comportamento já documentado no sweeper (exclusão global por albion_id,
    # ver comment em generate_candidates). Se o mesmo número existe como
    # batalha legítima em outra região, ela já está no banco e o sweeper não
    # a re-sonda de qualquer forma (está no conjunto `known` daquela região).
    op.execute(
        "INSERT INTO battle_id_probes (albion_id, status, region, probed_at) "
        "SELECT b.albion_id, 'missing', b.region, CURRENT_TIMESTAMP "
        "FROM battles b "
        "WHERE b.players_total < 10 "
        "  AND b.albion_id NOT IN (SELECT albion_id FROM battle_id_probes)"
    )
    # Probes existentes que apontavam pra batalhas purgadas: viram 'missing'
    # antes do DELETE. Depois dele a FK já seria NULL e perderíamos a relação
    # necessária para encontrá-los.
    op.execute(
        "UPDATE battle_id_probes SET status='missing', battle_id=NULL "
        "WHERE battle_id IN (SELECT id FROM battles WHERE players_total < 10)"
    )
    # Explicit deletes also make this migration safe on existing SQLite
    # databases that were created before foreign_keys was enabled.
    for table in (
        "battle_kill_events", "battle_guilds", "battle_participants",
        "battle_group_members", "battle_sides",
    ):
        op.execute(
            f"DELETE FROM {table} WHERE battle_id IN "
            "(SELECT id FROM battles WHERE players_total < 10)"
        )
    op.execute("DELETE FROM battles WHERE players_total < 10")


def downgrade() -> None:
    # Sem downgrade — as batalhas pequenas foram purgadas de propósito e não
    # dá pra recriá-las sem re-sondear a API do Albion (que é o que o
    # battle_sweeper faz, mas só pra buracos/futuro, não passado).
    pass
