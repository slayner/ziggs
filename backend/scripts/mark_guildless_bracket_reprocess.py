"""
Marca pra reprocessamento (Battle.reprocess_reason = "guildless_bracket")
toda batalha onde jogadores SEM guilda foram indevidamente agrupados numa
facção só pelo bug antigo de faction_key (ver app/services/battle_sides.py)
— toda batalha deep-processada ANTES da correção tem TODOS os participantes
sem guilda no MESMO side_id, por construção (a faction_key antiga sempre
devolvia "g:sem_guilda" pra qualquer um sem guilda/aliança).

Só MARCA (UPDATE em massa, metadado, sem chamada de rede nenhuma) — quem
reprocessa de verdade, aos poucos e sem disputar lock com tráfego real, é
app/services/battle_reprocessor.py (já registrado no lifespan do servidor).

    python -m scripts.mark_guildless_bracket_reprocess
"""
import sys

sys.path.insert(0, ".")

from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models.battles import Battle, BattleParticipant, ReprocessCampaign

REASON = "guildless_bracket"
CHUNK = 500  # Battle.id.in_(lista enorme) estoura o limite de parâmetros do SQLite


def _candidate_ids(db) -> list[int]:
    rows = db.execute(
        select(BattleParticipant.battle_id, func.count(func.distinct(BattleParticipant.side_id)))
        .where(BattleParticipant.guild_id.is_(None))
        .group_by(BattleParticipant.battle_id)
        .having(func.count(func.distinct(BattleParticipant.side_id)) == 1)
    ).all()
    return [bid for bid, _ in rows]


db = SessionLocal()
ids = _candidate_ids(db)
marked = 0
for i in range(0, len(ids), CHUNK):
    chunk = ids[i:i + CHUNK]
    result = db.execute(
        update(Battle).where(Battle.id.in_(chunk), Battle.reprocess_reason.is_(None)).values(reprocess_reason=REASON)
    )
    marked += result.rowcount
campaign = db.get(ReprocessCampaign, REASON) or ReprocessCampaign(reason=REASON, total=0)
campaign.total += marked  # só cresce pelo que foi marcado AGORA (já marcado antes não conta de novo)
db.add(campaign)
db.commit()
db.close()
print(f"{len(ids)} candidatas, {marked} marcadas com reprocess_reason='{REASON}' (resto já estava marcado).")
