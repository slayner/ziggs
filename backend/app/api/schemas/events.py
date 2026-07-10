"""Schemas Pydantic da API de eventos."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    title: str | None = None
    scheduled_at: datetime | None = None
    comp_id: int | None = None
    seriousness: str = "casual"  # casual | serious
    # Voice tracking sempre ligado — não é mais escolha na criação (site parou
    # de mostrar o toggle; % da call sempre calculada a partir dos snapshots).
    participation_mode: str = "voice_percent"  # presence | voice_percent
    message: str | None = None  # texto livre do caller, mostrado no mass-info


class EventUpdate(BaseModel):
    """Edição parcial de um evento (PATCH). Só os campos presentes no body são
    aplicados (caller usa exclude_unset). comp_id=null desvincula a comp; trocar
    a comp remove os signups existentes (a inscrição era pra função da comp
    antiga) — o bot avisa os inscritos por DM, do site só são limpos."""
    title: str | None = None
    scheduled_at: datetime | None = None
    comp_id: int | None = None
    attendance: float | None = None


class EventSummary(BaseModel):
    id: int
    state: str
    # LEGADO — só não-nulo em eventos criados antes do tipo sumir. Ver EventType.
    type: str | None = None
    title: str | None = None
    caller_name: str | None = None
    scheduled_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    comp_id: int | None = None
    seriousness: str = "casual"
    participation_mode: str = "presence"


class VerificationStepOut(BaseModel):
    step: str
    completed: bool
    data: dict = Field(default_factory=dict)


class ParticipantOut(BaseModel):
    id: int
    user_id: int
    user_name: str | None
    percent: int
    base_percent: int
    is_trial: bool
    silver_received: int
    snapshots_present: int = 0
    game_role_id: int | None = None
    game_role_name: str | None = None
    # Resolvido (override do admin OU derivado da inscrição) — usado no cálculo
    # de payout (irregular fica fora do lootsplit), não tem mais coluna própria
    # na UI (lista única, ordenada por percent).
    is_valid: bool = True
    # Origem da presença além da escalação. null = escalado (origem esperada,
    # sem marcador). Demais: "battle_no_call" | "call_outsider" |
    # "call_no_signup" | "call_signup" | "manual" — ver _participant_origin.
    origin: str | None = None


class DeathOut(BaseModel):
    id: int
    user_id: int | None
    display_name: str
    silver_value: int
    notes: str | None
    approved: bool


class PayoutRow(BaseModel):
    user_id: int | None
    display_name: str
    percent: int
    lootsplit: int
    regear: int
    scout: int = 0
    total: int


class PayoutPreview(BaseModel):
    tab_value: int
    # Setting da guilda usado neste cálculo —
    # "none"|"leftover"|"full"|"guild_backed". Regear em si não tem mais tipo,
    # é sempre calculado; isto só existe pra a UI decidir mostrar ou não a
    # coluna de lootsplit (sem replicar a regra aqui).
    lootsplit_mode: str = "full"
    payouts: list[PayoutRow]
    total_lootsplit: int
    total_regear: int
    total_scout: int = 0
    # Sobra da tab não distribuída por truncagem int (lootsplit proporcional).
    # Regear é custo do banco, não consome tab — não entra aqui.
    # Scout é pool SEPARADO financiado pelo valor vendido dos nodes capturados
    # (NodeDef.weight × sold_value) — não sai da tab, não entra no rounding_loss.
    rounding_loss: int = 0
    # Fatia dos loggers (lootlog anônimo): logger_percent da tab é separado e
    # dividido pelo peso de cada logger. Só preeenchido quando o CTA tem
    # submissões de lootlog; vazio/zera nos demais casos (comportamento inalterado).
    logger_pool: int = 0
    logger_payouts: list[PayoutRow] = Field(default_factory=list)
    scout_payouts: list[PayoutRow] = Field(default_factory=list)
    # Só preenchido em modo "guild_backed" quando o regear come mais que a tab.
    # guild_deficit_total = rombo a descontar; guild_deficit_member_count =
    # quantos membros da guilda (GuildMember) dividem essa conta em partes
    # iguais no finalize (ver events._finalize_payouts). Fora desse caso, 0/0.
    guild_deficit_total: int = 0
    guild_deficit_member_count: int = 0


class EventDetail(BaseModel):
    id: int
    state: str
    # LEGADO — só não-nulo em eventos criados antes do tipo sumir. Ver EventType.
    type: str | None = None
    title: str | None = None
    message: str | None = None
    comp_id: int | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    callout_at: datetime | None = None
    ended_at: datetime | None = None
    is_loss: bool = False
    tab_value: int = 0
    tab_image_url: str | None = None
    battleboard_url: str | None = None
    seriousness: str = "casual"
    participation_mode: str = "presence"
    functions_released: bool = False
    total_snapshots: int = 0
    # Pontos de attendance (economia/leaderboard do bot) — UM valor por evento,
    # igual pra todo participante independente do percent do split. Aceita
    # fração (ex.: 0.5, 1.5). Editável na bracket expandida (ver /attendance).
    attendance: float = 1.0
    allowed_transitions: list[str] = Field(default_factory=list)
    verification: list[VerificationStepOut] = Field(default_factory=list)
    participants: list[ParticipantOut] = Field(default_factory=list)
    deaths: list[DeathOut] = Field(default_factory=list)
    signups: list["SignupOut"] = Field(default_factory=list)
    # Registrados (/register) vistos numa batalha real da guilda na janela do
    # evento mas sem nenhum EventParticipant (nem call, nem inscrição) — ver
    # services/events.py:_battle_absentees. Só leitura/heurística, não vira
    # linha no banco sozinho.
    battle_absentees: list["BattleAbsenteeOut"] = Field(default_factory=list)
    payout: PayoutPreview | None = None
    regear_summary: "RegearSummary | None" = None


class RegearSummary(BaseModel):
    """Resumo dos regears da thread do evento (landmark) — alimenta a linha
    "Regears da thread" no review e o gate de finalize em modos tab."""
    pending: int = 0
    approved: int = 0
    denied: int = 0
    approved_total: int = 0


class SignupOut(BaseModel):
    id: int
    user_id: int
    user_name: str | None
    functions: list[str] = Field(default_factory=list)
    created_at: datetime


class BattleAbsenteeOut(BaseModel):
    user_id: int
    user_name: str | None


class ReleaseFunctionsIn(BaseModel):
    released: bool = True


class TransitionRequest(BaseModel):
    to: str
    reason: str | None = None


class StepRequest(BaseModel):
    completed: bool = True
    # Dados específicos do passo (ex.: {"value": 500000000} para tab_value)
    data: dict | None = None


class NodeClaimIn(BaseModel):
    # Captura de node em review: "pegamos o node? por quanto vendeu?".
    # scout_id/scout_name já vivem no NodeEventLog (quem adicionou o node).
    captured: bool = True
    sold_value: int = 0  # valor vendido do node; scout recebe NodeDef.weight × isto


class ParticipantIn(BaseModel):
    user_id: int
    user_name: str | None = None
    percent: int = 0
    base_percent: int = 0
    is_trial: bool = False
    # None = derivar da inscrição; True/False = já nasce validado (ex.:
    # promover um inscrito-sem-presença ou um battle-absentee cria explícito).
    is_valid: bool | None = None


class DeathIn(BaseModel):
    display_name: str
    user_id: int | None = None
    silver_value: int = 0
    notes: str | None = None


class DeathUpdate(BaseModel):
    approved: bool | None = None
    silver_value: int | None = None
    notes: str | None = None


class ParticipantUpdate(BaseModel):
    game_role_id: int | None = None
    percent: int | None = None
    is_trial: bool | None = None
    # Sem coluna própria na UI — editar percent já implica is_valid=True (o
    # admin está deliberadamente dando split a essa pessoa).
    is_valid: bool | None = None


class AttendanceIn(BaseModel):
    value: float


class RegearItemOut(BaseModel):
    slot: str
    item_id: str
    name: str
    quality: int
    quantity: int
    unit_price: int
    total_price: int


class RegearEstimateOut(BaseModel):
    participant_id: int
    user_name: str | None
    game_role_id: int | None
    game_role_name: str | None
    items: list[RegearItemOut]
    total: int
    price_basis: str
    calculated_at: str


# ── Escalação (assentamento de inscritos nos slots da comp) ───────────────────

class BuildItemOut(BaseModel):
    slot: str
    item_id: str
    name: str
    quality: int
    quantity: int


class EscalationRoleOut(BaseModel):
    id: int
    name: str
    invisible_function: str | None = None
    weapon_name: str | None = None
    offhand: str | None = None
    helmet: str | None = None
    armor: str | None = None
    boots: str | None = None
    cape: str | None = None
    food: str | None = None
    play_style: str | None = None
    obs: str | None = None
    build_items: list[BuildItemOut] = Field(default_factory=list)
    color: str | None = None
    q_spell: str | None = None
    w_spell: str | None = None
    passive_spell: str | None = None
    gear_spells: dict[str, str | None] = Field(default_factory=dict)


class EscalationSlotOut(BaseModel):
    id: int
    position: int
    label: str | None = None
    fn: str | None = None
    notes: str | None = None
    roles: list[EscalationRoleOut] = Field(default_factory=list)


class EscalationPartyOut(BaseModel):
    id: int
    position: int
    name: str | None = None
    slots: list[EscalationSlotOut] = Field(default_factory=list)


class AssignmentOut(BaseModel):
    slot_id: int | None
    user_id: int
    user_name: str | None
    game_role_id: int | None = None


class EscalationSignupOut(BaseModel):
    user_id: int
    user_name: str | None
    functions: list[str] = Field(default_factory=list)


class EscalationEventOut(BaseModel):
    id: int
    title: str | None = None
    scheduled_at: datetime | None = None
    seriousness: str
    state: str
    comp_id: int | None = None
    comp_name: str | None = None
    functions_released: bool = False


class EscalationOut(BaseModel):
    event: EscalationEventOut
    parties: list[EscalationPartyOut] = Field(default_factory=list)
    assignments: list[AssignmentOut] = Field(default_factory=list)
    enlisted: list[EscalationSignupOut] = Field(default_factory=list)
    can_manage: bool = False


class AssignIn(BaseModel):
    slot_id: int
    user_id: int
    user_name: str | None = None
    game_role_id: int


class EscalationPricesOut(BaseModel):
    prices: dict[str, int] = Field(default_factory=dict)
