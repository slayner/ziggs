"""Batalhas ZvZ registradas via Albion gameinfo API."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, json_type, pk


class Battle(Base):
    __tablename__ = "battles"
    __table_args__ = (UniqueConstraint("region", "albion_id", name="uq_battles_region_albion_id"),)

    id: Mapped[int] = pk()
    # Cada servidor Albion tem seu próprio host de API e seus próprios IDs —
    # o mesmo albion_id pode existir em 2 regiões sem ser a mesma batalha.
    # O link público nunca usa isto direto — ver BattleGroup.public_id.
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    albion_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    kill_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cluster: Mapped[str | None] = mapped_column(String(255))
    players_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # "light" = só o resumo por guilda (todas as batalhas); "deep" = eventos de kill
    # paginados + builds + análise de lados (só as ZvZ-qualificadas, ver battle_tracker.py).
    processing_tier: Mapped[str] = mapped_column(String(16), default="light", nullable=False)
    is_zvz: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # False só quando deep-processing encontra uma morte com equipamento (não
    # pelado) que dropou fama 0 — cada Battle.albion_id é UM mapa só, então
    # essa morte sozinha já prova que a zona inteira não é letal (duelo,
    # arena, etc). Default True: batalhas "light" (sem eventos de kill pra
    # checar) e batalhas deep ainda sem essa morte ficam como letais.
    is_lethal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Vira True depois que profile_warmer já rodou a pré-carga de perfil de
    # TODOS os participantes dessa batalha — fila de processar 1 vez só,
    # nunca reprocessa a mesma batalha (ver app/services/profile_warmer.py).
    profiles_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Fila genérica de reprocessamento: quando uma mudança de lógica de
    # processamento (faction_key, elegibilidade etc.) invalida dados já
    # gravados, marca as batalhas afetadas com um motivo (string curta,
    # qualquer texto serve, é só rótulo) em vez de escrever um script avulso
    # que reprocessa tudo de uma vez — ver app/services/battle_reprocessor.py,
    # que varre isso aos poucos, rodando dentro do mesmo processo da API (não
    # um processo separado disputando lock do SQLite com tráfego real).
    # None = nada pendente. Vira None de novo depois que reprocessa com sucesso.
    reprocess_reason: Mapped[str | None] = mapped_column(String(64), index=True)
    # Nick do jogador cujo companion descobriu esta batalha (só quando o report
    # do companion CRIOU a batalha — batalhas já conhecidas não re-creditam).
    # Exibido como agradecimento na página pública da batalha.
    found_by: Mapped[str | None] = mapped_column(String(64))


class ReprocessCampaign(Base):
    """Total de batalhas já marcadas pra cada reprocess_reason (ver Battle) —
    só pra calcular % de progresso na barra do menu de Configurações
    (pending = count atual com esse motivo; done = total - pending)."""
    __tablename__ = "reprocess_campaigns"

    reason: Mapped[str] = mapped_column(String(64), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BattleSyncCursor(Base):
    """Cursor de paginação do backfill histórico, 1 linha por região. Avança
    a cada ciclo (ver battle_tracker.backfill_step) até a API não devolver
    mais nada ou bater no corte de BACKFILL_MAX_AGE — daí trava em done=True
    pra sempre (não tem por que continuar pedindo offset depois disso)."""
    __tablename__ = "battle_sync_cursors"

    region: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BattleGuild(Base):
    __tablename__ = "battle_guilds"
    __table_args__ = (UniqueConstraint("battle_id", "albion_guild_id"),)

    id: Mapped[int] = pk()
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    albion_guild_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    guild_name: Mapped[str] = mapped_column(String(255), nullable=False)
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_name: Mapped[str | None] = mapped_column(String(255))
    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    side_id: Mapped[int | None] = mapped_column(ForeignKey("battle_sides.id", ondelete="SET NULL"))


class BattleSide(Base):
    """Um dos lados reais da luta (ou o bucket de ratos/bystanders)."""
    __tablename__ = "battle_sides"

    id: Mapped[int] = pk()
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(8), nullable=False)  # "A" | "B" | "rats"
    is_rats: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    player_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Kills reais contra o outro lado principal (exclui fogo amigo e ratos).
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BattleParticipant(Base):
    """Um jogador numa batalha — base stats vêm do resumo da API, build/dano/cura
    vêm da paginação de eventos (só em batalhas processing_tier='deep')."""
    __tablename__ = "battle_participants"
    __table_args__ = (UniqueConstraint("battle_id", "albion_player_id"),)

    id: Mapped[int] = pk()
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    albion_player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Indexado: toda query de "membros/roster da guilda X" filtra por aqui.
    # Sem o índice, o otimizador do SQLite caía no índice de albion_player_id
    # (ordenar 2.5M rows pra distinct) em vez de filtrar guild_id primeiro —
    # _members levava 30-42s pra achar 12 membros.
    guild_id: Mapped[str | None] = mapped_column(String(64), index=True)
    guild_name: Mapped[str | None] = mapped_column(String(255))
    # Indexado pelo mesmo motivo de guild_id: _alliance_roster_log e roster de
    # aliança filtram por aqui.
    alliance_id: Mapped[str | None] = mapped_column(String(64), index=True)
    alliance_name: Mapped[str | None] = mapped_column(String(255))
    side_id: Mapped[int | None] = mapped_column(ForeignKey("battle_sides.id", ondelete="SET NULL"))

    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    ip: Mapped[float] = mapped_column(Float, default=0, nullable=False)

    # Acumulados a partir de Participants[].DamageDone/SupportHealingDone de
    # cada evento de kill da batalha — só preenchido em processing_tier='deep'.
    damage_dealt: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    damage_taken: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    healing_done: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    # Vezes que apareceu em Participants[] de uma kill sem ser o matador —
    # crédito de assist (ver _write_deep_data). Usado no sistema de pontos
    # por arma do perfil de jogador (routes/players.py).
    assists: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Lista de builds DISTINTAS vistas no jogador durante a batalha (troca de
    # gear no meio da luta não é raro). Cada item no mesmo formato usado em
    # CompBuilder/albion-items.ts: {weapon, offhand, helmet, armor, boots,
    # cape, mount, bag, food, potion}. None = nunca apareceu num evento.
    equipment: Mapped[list[dict] | None] = mapped_column(json_type())


class BattleKillEvent(Base):
    __tablename__ = "battle_kill_events"
    __table_args__ = (UniqueConstraint("battle_id", "albion_event_id"),)

    id: Mapped[int] = pk()
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    albion_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    killer_participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("battle_participants.id", ondelete="SET NULL")
    )
    victim_participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("battle_participants.id", ondelete="SET NULL")
    )
    killer_side_id: Mapped[int | None] = mapped_column(ForeignKey("battle_sides.id", ondelete="SET NULL"))
    victim_side_id: Mapped[int | None] = mapped_column(ForeignKey("battle_sides.id", ondelete="SET NULL"))

    # Build do matador NESSE evento específico (snapshot da API no momento da
    # kill) — diferente de BattleParticipant.equipment, que acumula TODAS as
    # builds distintas vistas no jogador na batalha inteira, sem saber qual
    # valia em qual kill. Usado pro tooltip do horizonte de eventos.
    killer_equipment: Mapped[dict | None] = mapped_column(json_type())
    # Mesma ideia, mas a build da vítima nesse evento — pro hover do horizonte
    # mostrar a arma de quem morreu, não só a de quem matou.
    victim_equipment: Mapped[dict | None] = mapped_column(json_type())

    # Itens carregados (não equipados) no momento do evento — lista de
    # {item_id, count}. Usado pra calcular o valor aproximado da build na
    # lista de mortes do horizonte, somado ao que está equipado.
    killer_inventory: Mapped[list[dict] | None] = mapped_column(json_type())
    victim_inventory: Mapped[list[dict] | None] = mapped_column(json_type())


class BattleGroup(Base):
    """O sublink público (7 chars, ex. 'k3j9xq2'). Aponta para 1+ batalhas —
    várias batalhas combinadas numa só KB compartilham 1 código só, achado
    pelo fingerprint (lista ordenada de battle_id) pra nunca duplicar grupo
    pra mesma combinação. Criado on-demand (não pra toda batalha que existe,
    só quando alguém efetivamente compartilha/resolve aquele link)."""
    __tablename__ = "battle_groups"

    id: Mapped[int] = pk()
    public_id: Mapped[str] = mapped_column(String(7), unique=True, nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BattleGroupMember(Base):
    __tablename__ = "battle_group_members"
    __table_args__ = (UniqueConstraint("group_id", "battle_id"),)

    id: Mapped[int] = pk()
    group_id: Mapped[int] = mapped_column(
        ForeignKey("battle_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    battle_id: Mapped[int] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BattleIdProbe(Base):
    """Memória de sondagem do battle_sweeper — uma linha por albion_id que
    sondamos no endpoint de detalhe (fora da janela de offset 10000 da
    listagem). Sem isto, cada ciclo re-sondaria todos os buracos (404s) de novo
    e viraria tempestade de 429. status='found' = achado em ≥1 região (light-
    capturado, marcado reprocess_reason='sweeper' pro battle_reprocessor fazer
    o deep); status='missing' = 404 nos 3 hosts, batalha inexistente."""
    __tablename__ = "battle_id_probes"

    albion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "found" | "missing"
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)  # primeira região que achou
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    battle_id: Mapped[int | None] = mapped_column(
        ForeignKey("battles.id", ondelete="SET NULL"), nullable=True
    )
