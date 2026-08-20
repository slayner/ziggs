"""Schemas member-safe do portal do membro — /guilds/{guild_id}/member/*.

Nenhum destes payloads expõe dados administrativos (verificação interna,
notas de morte, listas cruas de signup, transições admin, evidência de
regear). `MemberEventDetail` NÃO é `EventDetail` de propósito: o admin vê o
evento inteiro; o membro só vê o que a guilda escolheu divulgar.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Carteira (economia) ──────────────────────────────────────────────────────

class WalletOut(BaseModel):
    balance: int
    total_earned: int
    # Direção derivada server-side: "in" (recebeu) | "out" (pagou) | "neutral".
    # Nunca confiamos no client pra classificar movimento de saldo.
    transactions: list["WalletTxOut"] = Field(default_factory=list)
    total: int = 0


class WalletTxOut(BaseModel):
    id: int
    kind: str
    # Direção do ponto de vista do MEMBRO: "in" | "out" | "neutral".
    direction: str
    amount: int
    # Nome legível da contraparte quando houver (pay entre membros); None
    # pra movimentos de sistema (event_payout, forfeit, ...).
    counterparty_name: str | None = None
    undone: bool = False
    created_at: datetime


# ── Energia ──────────────────────────────────────────────────────────────────

class EnergyOut(BaseModel):
    balance: int
    entries: list["EnergyEntryOut"] = Field(default_factory=list)
    total: int = 0


class EnergyEntryOut(BaseModel):
    id: int
    kind: str
    ts: str
    player: str
    reason: str | None
    amount: int
    created_at: datetime


# ── Eventos ──────────────────────────────────────────────────────────────────

class MemberEventSummary(BaseModel):
    """Linha da lista de eventos publicados — só o que o membro precisa ver
    antes de abrir o detalhe."""
    id: int
    state: str
    type: str | None = None
    title: str | None = None
    caller_name: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    comp_id: int | None = None
    # True quando o membro pode se inscrever (scheduled/in_progress).
    # False em review/finalized — informa a UI sem expor transições admin.
    can_signup: bool = False


class MemberCompRef(BaseModel):
    """Referência member-safe a uma comp (sem o conteúdo interno de
    slots/roles/builds — o membro vê o resumo, não o editor)."""
    id: int
    name: str
    description: str | None = None


class MemberPayoutRow(BaseModel):
    """Linha de payout divulgada ao membro em evento FINALIZADO. `silver`
    é o valor EFETIVAMENTE PAGO (EventParticipant.silver_received persistido
    no finalize), não uma recomputação de settings que podem ter mudado."""
    user_id: int | None
    display_name: str
    silver_received: int


class MemberSettlementOut(BaseModel):
    """Divulgação de settlement de um evento FINALIZADO: totais + linhas de
    pagamento por participante. Só o que foi pago de fato."""
    tab_value: int
    total_paid: int
    participants: list[MemberPayoutRow] = Field(default_factory=list)


class MemberEventDetail(BaseModel):
    """Detalhe member-safe de um evento publicado. Diferente de EventDetail:
    sem escalation_token, sem allowed_transitions, sem verification steps,
    sem deaths, sem battle_absentees, sem signups cruas, sem regear_summary."""
    id: int
    state: str
    type: str | None = None
    title: str | None = None
    message: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    comp: MemberCompRef | None = None
    # Só preenchido quando o evento está FINALIZADO — divulgação de settlement.
    settlement: MemberSettlementOut | None = None


# ── Comps (read-only) ────────────────────────────────────────────────────────

class MemberCompSummary(BaseModel):
    id: int
    name: str
    description: str | None = None
    archived: bool
    party_count: int


class MemberCompDetail(BaseModel):
    """Detalhe de comp member-safe: estrutura pública (parties/slots/roles),
    sem dados de gestão. Espelho read-only do CompRead administrativo."""
    id: int
    name: str
    description: str | None = None
    archived: bool
    parties: list = Field(default_factory=list)


# ── Signup ───────────────────────────────────────────────────────────────────

class SignupOptionOut(BaseModel):
    key: str
    weapon_id: int
    weapon_name: str
    fn: str
    role_names: list[str] = Field(default_factory=list)


class SignupOptionsOut(BaseModel):
    eligible: list[SignupOptionOut] = Field(default_factory=list)
    block_reason: str | None = None
    # Pré-seleção das preferências globais do membro que aparecem nesta comp.
    preselected: list[str] = Field(default_factory=list)
    min_builds: int | None = None
    # Inscrição atual do membro neste evento (None = ainda não inscrito).
    current: "MemberSignupOut | None" = None


class MemberSignupOut(BaseModel):
    id: int
    functions: list[str] = Field(default_factory=list)
    weapon_fns: list[dict] = Field(default_factory=list)
    created_at: datetime


class SignupIn(BaseModel):
    """POST de auto-inscrição pelo portal. Só aceita pair keys (identidade
    do signup); NUNCA user_id/roles — identidade é derivada server-side do
    membro ativo logado."""
    options: list[str] = Field(default_factory=list)


# ── Preferências arma+fn ─────────────────────────────────────────────────────

class WeaponFnPrefOut(BaseModel):
    weapon_id: int
    fn: str
    weapon_name: str


class WeaponFnValidPairOut(BaseModel):
    """Par (weapon_id, fn) válido numa comp ativa da guilda — usado pelo
    editor de preferências pra só oferecer opções que existem em alguma
    comp. Arma que não está em nenhuma comp não aparece."""
    weapon_id: int
    fn: str
    weapon_name: str


class WeaponFnPrefsOut(BaseModel):
    preferences: list[WeaponFnPrefOut] = Field(default_factory=list)
    # Catálogo de pares válidos (união das comps ativas) — o frontend monta
    # o picker só com estes. Sem isso, o membro veria armas de catálogo global
    # que não estão em nenhuma comp da guilda.
    valid_pairs: list[WeaponFnValidPairOut] = Field(default_factory=list)


class WeaponFnPrefsIn(BaseModel):
    """PUT de preferências: pares (weapon_id, fn). Validados server-side
    contra a união das comps ativas da guilda — pares desconhecidos são
    rejeitados, não silenciosamente descartados nem salvos."""
    preferences: list[dict] = Field(default_factory=list)