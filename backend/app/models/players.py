"""Modelos de jogadores de Albion — perfis públicos acumulativos."""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, json_type, pk


class AlbionPlayer(Base):
    """Jogador de Albion visto alguma vez no Ziggs. Chave = ID do Albion.

    IDs do Albion não colidem entre regiões (servidores Americas/Europe/Asia
    são totalmente separados, cada um com seu próprio espaço de IDs) — por
    isso `albion_id` continua única globalmente, `region` é só pra saber qual
    host da API usar pra buscar mais dados desse jogador."""
    __tablename__ = "albion_players"

    id: Mapped[int] = pk()
    albion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Coluna nova (ver scripts/add_player_region.py) — linhas existentes,
    # todas vistas só pelo host Americas até essa mudança, ficam "americas".
    region: Mapped[str] = mapped_column(String(16), nullable=False, server_default="americas")

    # Snapshot mais recente — denormalizado para consulta rápida
    guild_id: Mapped[str | None] = mapped_column(String(64))
    guild_name: Mapped[str | None] = mapped_column(String(255))
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_name: Mapped[str | None] = mapped_column(String(255))
    alliance_tag: Mapped[str | None] = mapped_column(String(16))
    avatar: Mapped[str | None] = mapped_column(String(255))

    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    death_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    pve_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    crafting_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gathering_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Coleta por recurso, extraída de LifetimeStatistics.Gathering.{Wood,Hide,
    # Ore,Rock,Fiber}.Total no upsert_player (igual gathering_fame total).
    # Indexados — rankings de highscore de coleta ordenam por cada um com
    # filtro de região. 0 = blob sem esse recurso ou ainda não sincronizado.
    gather_wood: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gather_hide: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gather_ore: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gather_rock: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gather_fiber: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Pesca (FishingFame) — escalar solto no LifetimeStatistics (irmão de
    # Gathering.All), não dentro de Gathering. Ranking de highscore de pesca.
    fishing_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Blob cru de LifetimeStatistics da API do Albion — guarda o detalhe que os
    # escalares acima não cobrem (coleta por recurso Wood/Ore/..., FishingFame,
    # FarmingFame). Só preenchido quando o payload tem LifetimeStatistics
    # (perfil/feed); buscas trazem só o topo e NÃO zeram o que já havia.
    # Ver upsert_player e _synthetic_raw (routes/players.py).
    lifetime_statistics: Mapped[dict | None] = mapped_column(json_type())

    is_deleted: Mapped[bool] = mapped_column(Boolean(), nullable=False, server_default=sa.false(), default=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Instante da última consulta direta a /gameinfo/players/{id}. Diferente de
    # last_seen_at, que também muda ao receber um jogador pelo feed de kills.
    stats_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Setado por POST /players/{id}/refresh — o profile_warmer prioriza essas
    # linhas na fila e limpa o campo depois de re-sincronizar.
    refresh_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerSnapshot(Base):
    """Snapshot periódico de um jogador — permite ver histórico de guilds e fama."""
    __tablename__ = "player_snapshots"

    id: Mapped[int] = pk()
    player_id: Mapped[int] = mapped_column(
        ForeignKey("albion_players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    guild_id: Mapped[str | None] = mapped_column(String(64))
    guild_name: Mapped[str | None] = mapped_column(String(255))
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_tag: Mapped[str | None] = mapped_column(String(16))

    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    death_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    pve_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlayerKillEvent(Base):
    """Ledger de kills/mortes por jogador — alimentado pelo feed global de
    kills de cada região (ver player_tracker.py), cobre QUALQUER kill
    (1v1 incluso), diferente do battle_tracker (que só processa em
    profundidade batalhas com vários jogadores). Não depende de Battle
    existir: o link pro bracket (quando há um) é resolvido em leitura via
    `region + albion_battle_id`, sem FK rígida."""
    __tablename__ = "player_kill_events"
    __table_args__ = (
        UniqueConstraint("region", "albion_event_id"),
        Index(
            "ix_pke_juicy_queue", "region", "silver_dropped", "timestamp",
            postgresql_where=sa.text("fame > 0 AND silver_dropped IS NOT NULL"),
        ),
        Index(
            "ix_pke_juicy_unpriced_queue", "region", "timestamp",
            postgresql_where=sa.text("silver_dropped IS NULL"),
        ),
    )

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    albion_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    killer_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("albion_players.id", ondelete="SET NULL"), index=True
    )
    victim_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("albion_players.id", ondelete="SET NULL"), index=True
    )

    # numberOfParticipants do evento cru — quantos jogadores do lado do
    # matador ganharam crédito de assist. is_solo = matou sem ajuda de ninguém.
    participant_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Snapshot oficial de Participants[]: nomes/IDs dos assistentes para embeds.
    participants: Mapped[list[dict] | None] = mapped_column(json_type())
    # groupMemberCount é o tamanho do grupo do matador, independente de
    # guilda/aliança. Diferente de participant_count, não inclui terceiros que
    # contribuíram numa luta entre vários grupos.
    group_member_count: Mapped[int | None] = mapped_column(Integer)
    is_solo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Link fraco pra Battle.albion_id da mesma região — pode não existir
    # ainda (corrida) ou nunca virar um Battle "deep" (luta pequena), por
    # isso não é FK; resolvido em leitura quando precisa linkar pro bracket.
    albion_battle_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # KillArea cru da API — hoje só se viu "OPEN_WORLD" em produção, mas o
    # campo existe e pode diferenciar conteúdo no futuro, custa nada guardar.
    kill_area: Mapped[str | None] = mapped_column(String(32))

    killer_equipment: Mapped[dict | None] = mapped_column(json_type())
    victim_equipment: Mapped[dict | None] = mapped_column(json_type())
    # Usado pra calcular "prata dropada" (preço dos itens via prices.get_battle_prices).
    victim_inventory: Mapped[list[dict] | None] = mapped_column(json_type())

    # Guilda do matador/vítima NESSE momento (snapshot do evento, não o estado
    # atual) — usado pra reconstruir o histórico de guildas a partir das
    # datas reais de kill/morte (ver routes/players.py _guild_history), bem
    # mais granular que confiar só nos PlayerSnapshot esparsos.
    killer_guild_id: Mapped[str | None] = mapped_column(String(64), index=True)
    killer_guild_name: Mapped[str | None] = mapped_column(String(255))
    victim_guild_id: Mapped[str | None] = mapped_column(String(64), index=True)
    victim_guild_name: Mapped[str | None] = mapped_column(String(255))

    # Prata aproximada perdida na morte (preço dos itens equipados + carregados
    # no momento do evento). Precificada no processamento pelo worker
    # silver_dropped (services/silver_dropped.py), em vez de on-demand só ao
    # abrir o perfil — permite ranking de highscore "mais prata dropada" por
    # jogador (SUM(silver_dropped) GROUP BY victim_player_id).
    # NULL = ainda não precificado (pendente no worker); 0 = precificado e deu
    # zero (vítima sem gear, ou itens sem cotação); >0 = prata real. Usar 0 como
    # "pendente" causaria loop infinito no worker (ver migration docstring).
    silver_dropped: Mapped[int | None] = mapped_column(BigInt(), nullable=True)


class JuicyKillDelivery(Base):
    """Outbox durável de uma kill já elegível para uma guilda.

    A seleção é feita quando a kill recebe preço, para que o poll do bot nunca
    precise procurar candidatos no ledger global de eventos.
    """
    __tablename__ = "juicy_kill_deliveries"
    __table_args__ = (
        Index(
            "ix_jkd_pending_poll", "guild_id", "region", "occurred_at", "kill_id",
            postgresql_where=sa.text("state = 'pending'"),
        ),
    )

    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), primary_key=True,
    )
    kill_id: Mapped[int] = mapped_column(
        ForeignKey("player_kill_events.id", ondelete="CASCADE"), primary_key=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    fame: Mapped[int] = mapped_column(BigInt(), nullable=False)
    silver_dropped: Mapped[int] = mapped_column(BigInt(), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerWeaponStat(Base):
    """Contadores BRUTOS por (jogador, arma base) — all-time, todas as
    regiões. Reconstruída do zero periodicamente (ver
    app.services.weapon_stats.rebuild_player_weapon_stats) a partir de
    PlayerKillEvent + BattleParticipant; é a fonte do ranking "maior
    pontuador com uma arma" do Highscores e do destaque de armas do perfil
    de jogador (routes/players.py).

    Guarda os FATOS (aparições, elegibilidade) como contadores puros — somar
    e multiplicar por uma constante nova não exige rebuild, então mudar
    SUPPORT_ELIGIBLE_FIGHT_POINTS/TANK_ELIGIBLE_FIGHT_POINTS ou reclassificar
    uma arma de função (routes/battles.py) já reflete na hora, sem rebuild.

    pierce_points/healer_points são EXCEÇÃO: o sistema ao vivo (e o perfil de
    jogador, antes desta tabela existir) sempre fez "assists // 3" POR LUTA
    e somou os pontos já arredondados — não dá pra reconstruir isso a partir
    de só uma soma de assists (floor não é distributivo: floor(1/3)+floor(1/3)
    != floor(2/3)). Por isso aqui já vêm com ASSISTS_PER_POINT/HEALING_PER_POINT
    aplicados NA HORA DO REBUILD — mudar essas duas constantes específicas
    exige rodar o rebuild de novo pra refletir (ver
    app.services.weapon_stats.rebuild_player_weapon_stats)."""
    __tablename__ = "player_weapon_stats"
    __table_args__ = (UniqueConstraint("albion_player_id", "weapon_base"),)

    id: Mapped[int] = pk()
    albion_player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    weapon_base: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Kills com essa arma (PlayerKillEvent.killer_equipment) — vale 1 ponto
    # cada, pra qualquer função (dps inclusive, que não tem bônus nenhum).
    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Vezes que essa arma apareceu como build principal (equipment[0]) numa
    # luta deep-processada — independe de elegibilidade.
    appearances: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Dessas aparições, quantas foram em luta ELEGÍVEL (letal + cura
    # registrada + guilda com >15 jogadores NESSA luta — mesmo critério do
    # ranking semanal). appearances - eligible_appearances = quantas vezes
    # a arma apareceu numa luta que NÃO entrava no critério do KB.
    eligible_appearances: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Pontos de pierce/healer já com floor por luta aplicado e somados (ver
    # docstring da classe — não são soma bruta de assists/cura).
    pierce_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    healer_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Das aparições ELEGÍVEIS, quantas tiveram 0 mortes (gate de suporte) e,
    # dessas, quantas a guilda não perdeu mais que o time adversário (gate
    # extra de tank). Contagem pura — multiplicar pela constante atual na
    # leitura, não precisa de rebuild se só a constante mudar.
    zero_death_eligible_fights: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tank_ok_fights: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlayerCountSnapshot(Base):
    """Snapshot periódico (15min, ver services/player_count_snapshot.py) da
    contagem de jogadores ativos (7 dias, mesma métrica de
    routes/battles.py:active_players) por região + global — alimenta o
    gráfico histórico do dashboard. active_players sozinho só sabe "agora vs
    7 dias atrás"; esta tabela guarda os pontos intermediários pro gráfico."""
    __tablename__ = "player_count_snapshots"

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "global"|"americas"|"europe"|"asia"
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class SearchEntry(Base):
    """Índice pré-normalizado pra busca global (jogador/guilda/aliança) — evita
    varrer battle_participants/battle_guilds inteiros a cada tecla digitada
    (ver services/search_index.py). Repovoado do zero periodicamente
    (rebuild, fonte da verdade) e mantido fresco entre rebuilds pelo
    write-path (upsert_entry, chamado por battle_tracker/player_tracker)."""
    __tablename__ = "search_entries"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id"),
        Index("ix_search_entries_type_norm", "entity_type", "norm_name"),
        Index("ix_search_entries_type_len", "entity_type", "name_len"),
        # Passe de substring/fuzzy da busca (`norm_name LIKE '%x%' ORDER BY
        # weight DESC LIMIT k`): sem este índice o LIKE-wildcard varria as 87k
        # linhas de jogador e ainda ordenava num temp b-tree (~200ms/tecla).
        # Com ele o banco caminha em ordem de weight e para no LIMIT — nome
        # comum (peso alto) resolve em ~1ms; pior caso (poucos matches) ~46ms.
        Index("ix_search_entries_type_weight", "entity_type", "weight"),
    )

    id: Mapped[int] = pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # player|guild|alliance
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    norm_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_len: Mapped[int] = mapped_column(Integer, nullable=False)

    region: Mapped[str | None] = mapped_column(String(16))          # players
    guild_name: Mapped[str | None] = mapped_column(String(255))     # players/guilds
    alliance_name: Mapped[str | None] = mapped_column(String(255))  # players/guilds
    guild_count: Mapped[int | None] = mapped_column(Integer)        # alliances

    # Ordenação dos resultados — nº de batalhas (recalculado no rebuild; o
    # write-path não toca aqui, custaria um COUNT a cada kill/batalha).
    weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DeletedProfile(Base):
    """Guildas e alianças marcadas como excluídas do jogo (detectado via API do Albion).
    Jogadores usam AlbionPlayer.is_deleted; aqui ficam apenas guild/alliance."""
    __tablename__ = "deleted_profiles"
    __table_args__ = (UniqueConstraint("entity_type", "albion_id"),)

    id: Mapped[int] = pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'guild' | 'alliance'
    albion_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KillIdProbe(Base):
    """Memória de sondagem do kill_sweeper — uma linha por albion_event_id que
    sondamos no endpoint de detalhe (fora da janela de offset 999 da listagem
    de events). Sem isto, cada ciclo re-sondaria todos os buracos (404s) de
    novo. status='found' = evento existe e foi ingerido; status='missing' =
    404, evento inexistente."""
    __tablename__ = "kill_id_probes"

    albion_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "found" | "missing"
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KillSyncCursor(Base):
    """Cursor de paginação do backfill de kills, 1 linha por região. Avança
    a cada ciclo (ver player_tracker.backfill_kills_step) varrendo a janela
    de ~1000 events que a API expõe, igual ao backfill de batalhas."""
    __tablename__ = "kill_sync_cursors"

    region: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
