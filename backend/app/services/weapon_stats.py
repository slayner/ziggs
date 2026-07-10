"""Reconstrói periodicamente app.models.players.PlayerWeaponStat — contadores
BRUTOS (aparições, elegibilidade, assists, cura) por (jogador, arma), all-time,
todas as regiões. Fonte do ranking "maior pontuador com uma arma" do
Highscores (window=alltime) e do destaque de armas do perfil de jogador.

A maioria dos campos é contagem pura — multiplicar por uma constante nova
acontece na LEITURA, não exige rebuild. pierce_points/healer_points são
exceção (floor por luta não é distributivo, ver docstring de
PlayerWeaponStat) — mudar ASSISTS_PER_POINT/HEALING_PER_POINT exige rodar
este rebuild de novo pra refletir. Reconstrói do zero a cada ciclo (trunca +
insere) em vez de fazer diff incremental: mais simples e correto mesmo com
batalhas que são reprocessadas (ver DEEP_REPROCESS_WINDOW em
battle_tracker.py, que reescreve BattleParticipant de uma luta recente)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.battles import (
    ASSISTS_PER_POINT, HEALING_PER_POINT,
    _wbase, _weapon_function_map, eligible_guild_battles_subquery, lethal_with_healing_filter,
)
from app.db import SessionLocal
from app.models.battles import Battle, BattleGuild, BattleParticipant
from app.models.players import AlbionPlayer, PlayerKillEvent, PlayerWeaponStat

log = logging.getLogger(__name__)

REBUILD_INTERVAL = 3600  # 1h — rebuild varre todas as batalhas, não precisa ser mais frequente que isso


def _empty_counters() -> dict[str, int]:
    return {
        "kills": 0, "appearances": 0, "eligible_appearances": 0,
        "pierce_points": 0, "healer_points": 0,
        "zero_death_eligible_fights": 0, "tank_ok_fights": 0,
    }


def rebuild_player_weapon_stats(db: Session) -> int:
    weapon_fn = _weapon_function_map(db)
    stats: dict[tuple[str, str], dict[str, int]] = {}

    # Kills vêm do ledger de kills (PlayerKillEvent) — cobre qualquer kill
    # com fama, incluindo 1v1 fora de batalha registrada, não só BattleParticipant.
    for albion_id, equip in db.execute(
        select(AlbionPlayer.albion_id, PlayerKillEvent.killer_equipment)
        .join(AlbionPlayer, AlbionPlayer.id == PlayerKillEvent.killer_player_id)
        .where(PlayerKillEvent.fame > 0)
    ):
        wb = _wbase(((equip or {}).get("MainHand") or {}).get("Type"))
        if not wb:
            continue
        stats.setdefault((albion_id, wb), _empty_counters())["kills"] += 1

    # Elegibilidade/assists/cura vêm de toda BattleParticipant deep-processada
    # — sem filtro de região/janela (tabela é all-time, região é filtrada na
    # leitura via AlbionPlayer.region, que é fixo por jogador).
    lethal_healing_ids = set(db.scalars(select(Battle.id).where(*lethal_with_healing_filter())).all())

    eligible_sq = eligible_guild_battles_subquery()
    guild_player_count = {
        (bid, gid): cnt for bid, gid, cnt in db.execute(
            select(eligible_sq.c.battle_id, eligible_sq.c.guild_id, eligible_sq.c.player_count)
        )
    }
    deaths_by_battle: dict[int, dict[str, int]] = {}
    for bid, gid, deaths in db.execute(
        select(BattleGuild.battle_id, BattleGuild.albion_guild_id, BattleGuild.deaths)
    ):
        deaths_by_battle.setdefault(bid, {})[gid] = deaths

    bp_rows = db.execute(
        select(
            BattleParticipant.battle_id, BattleParticipant.albion_player_id, BattleParticipant.guild_id,
            BattleParticipant.equipment, BattleParticipant.assists, BattleParticipant.healing_done,
            BattleParticipant.deaths,
        ).where(BattleParticipant.equipment.isnot(None))
    )
    for battle_id, albion_player_id, guild_id, equipment, assists, healing_done, deaths in bp_rows:
        if not equipment:
            continue  # equipment.isnot(None) não pega JSON null nem lista vazia
        wb = _wbase((equipment[0] or {}).get("weapon"))
        if not wb:
            continue
        row = stats.setdefault((albion_player_id, wb), _empty_counters())
        row["appearances"] += 1

        # pierce/healer: floor POR LUTA, somado (não dá pra reconstruir de uma
        # soma agregada depois — ver docstring de PlayerWeaponStat). Calculado
        # já aqui, na hora do rebuild, com a função/constantes atuais.
        role = weapon_fn.get(wb, "dps")
        if role == "pierce":
            row["pierce_points"] += assists // ASSISTS_PER_POINT
        elif role == "healer":
            row["healer_points"] += int(healing_done // HEALING_PER_POINT)

        eligible = battle_id in lethal_healing_ids and (battle_id, guild_id) in guild_player_count
        if not eligible:
            continue
        row["eligible_appearances"] += 1
        if deaths != 0:
            continue
        row["zero_death_eligible_fights"] += 1

        deaths_map = deaths_by_battle.get(battle_id, {})
        own_deaths = deaths_map.get(guild_id, 0)
        other_deaths = sum(d for g, d in deaths_map.items() if g != guild_id)
        if own_deaths <= other_deaths:
            row["tank_ok_fights"] += 1

    now = datetime.now(timezone.utc)
    db.query(PlayerWeaponStat).delete()
    if stats:
        db.bulk_insert_mappings(PlayerWeaponStat, [
            {"albion_player_id": pid, "weapon_base": wb, "synced_at": now, **counters}
            for (pid, wb), counters in stats.items()
        ])
    db.commit()
    return len(stats)


def _rebuild_sync() -> int:
    db = SessionLocal()
    try:
        return rebuild_player_weapon_stats(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_forever() -> None:
    # Roda em thread (asyncio.to_thread) — diferente do resto dos serviços
    # (que intercalam awaits de rede entre queries curtas), esse rebuild é
    # ~30s de SQL síncrono sem nenhum await no meio; rodando direto no loop
    # travaria todo o resto da API (handlers de request, outros serviços)
    # pela duração inteira do rebuild.
    # ponytail: o laço de agregação em Python (não a query em si) segura o
    # GIL a maior parte do tempo, então mesmo em thread o loop principal
    # ainda sofre gaps de até ~750ms durante o rebuild (medido) — bem melhor
    # que os 15-18s síncronos de ANTES desta tabela existir (toda request
    # all-time escaneava ao vivo), mas não é zero. Upgrade se isso incomodar:
    # mover a agregação pra GROUP BY no banco em vez de dict em Python.
    log.info("weapon_stats: iniciando (intervalo=%ds)", REBUILD_INTERVAL)
    # ponytail: folga antes da 1ª rodada — o rebuild disparando direto no boot
    # (junto com todos os outros serviços acordando) era a maior fonte dos
    # gaps de GIL logo na janela em que o bot-v2 conecta e faz o catch-up
    # (timeouts de 5s em regear/lootlog/embed-work). Intervalo é 1h; começar
    # 5min depois não muda nada do resultado.
    await asyncio.sleep(300)
    while True:
        try:
            n = await asyncio.to_thread(_rebuild_sync)
            log.info("weapon_stats: %d (jogador, arma) recalculados", n)
        except Exception as e:
            log.error("weapon_stats: erro: %s", e)
        await asyncio.sleep(REBUILD_INTERVAL)
