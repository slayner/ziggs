"""Administração de energia da guilda — /guilds/{guild_id}/energy-admin/*.

Espelho do controle de energia do bot legado (bot-legacy/cogs/energia.py),
agora no site com auditoria (AuditLog) e guardado pela permissão
`energy.manage` (mesmo mecanismo de role-permission de events.manage/comps.manage).
Tudo tenant-scoped por guild_id.

Rotas member-facing (saldo/ledger do próprio membro) continuam em member.py e
NÃO são tocadas aqui — este é o painel de staff.

Name resolver: reusa a MESMA fonte de registro do bot (BotRegistration ativa,
case-insensitive em albion_player_name) — mesmo formato da rota
/bot/registration-lookup/{guild_id} em auth.py. Assim o que o admin cola no
site casa exatamente com o que o bot legado já casa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.models.audit import AuditLog
from app.models.energy import EnergyBalance, EnergyEntry, EnergyWhitelist
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember, User
from app.services import energy as energy_svc

router = APIRouter(prefix="/guilds/{guild_id}/energy-admin", tags=["energy-admin"])

DEFAULT_ALERT_THRESHOLD = 50


def _mark_energy_control_dirty(db: Session, guild_id: int) -> None:
    """Marca o flag dirty do energy-control para o bot reeditar o embed."""
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return
    settings = dict(g.settings or {})
    settings["energy_control_dirty"] = True
    g.settings = settings
    db.flush()


# ── name resolver (mesma fonte da rota /bot/registration-lookup) ────────────────

def _resolve_nick(db: Session, guild_id: int, nick: str) -> int | None:
    """Devolve o discord_user_id do registro ATIVO com esse nick na guilda
    (case-insensitive), ou None se não há. Um usuário pode ter vários
    personagens — basta o 1º que casa. Mesma query da rota bot lookup."""
    nl = (nick or "").strip().lower()
    if not nl:
        return None
    reg = db.scalar(
        select(BotRegistration.discord_user_id).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.active.is_(True),
            func.lower(BotRegistration.albion_player_name) == nl,
        ).limit(1)
    )
    return int(reg) if reg is not None else None


# ── schemas ────────────────────────────────────────────────────────────────────

class LogImportIn(BaseModel):
    log_text: str = Field(..., description="Log do jogo colada (campos entre aspas)")


class ApplyResultOut(BaseModel):
    applied: int = 0
    duplicates: int = 0
    whitelisted_applied: int = 0
    unregistered: dict[str, int] = Field(default_factory=dict)


class LogImportOut(BaseModel):
    result: ApplyResultOut
    unregistered: dict[str, int] = Field(default_factory=dict)


class ManualSetIn(BaseModel):
    user_id: int
    value: int
    reason: str | None = None


class ManualSetOut(BaseModel):
    user_id: int
    balance: int


class WhitelistToggleOut(BaseModel):
    user_id: int
    whitelisted: bool


class OverviewRow(BaseModel):
    user_id: int
    display_name: str
    balance: int
    whitelisted: bool
    low_energy: bool


class OverviewOut(BaseModel):
    threshold: int
    members: list[OverviewRow] = Field(default_factory=list)


# ── helpers ────────────────────────────────────────────────────────────────────

def _display_name(user: User | None) -> str:
    if user is None:
        return ""
    return user.global_name or user.username


def _audit(
    db: Session, guild_id: int, member: GuildMember,
    action: str, entity_id: str | None, before: dict | None, after: dict | None,
    note: str | None = None,
) -> None:
    db.add(AuditLog(
        guild_id=guild_id,
        actor_id=member.user_id,
        actor_type="site",
        source="site",
        action=action,
        entity="energy",
        entity_id=entity_id,
        before=before,
        after=after,
        note=note,
    ))


def _guild_threshold(guild: Guild | None) -> int:
    if guild is None:
        return DEFAULT_ALERT_THRESHOLD
    v = (guild.settings or {}).get("energy_alert_threshold")
    if v is None:
        return DEFAULT_ALERT_THRESHOLD
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_ALERT_THRESHOLD


# ── rotas ──────────────────────────────────────────────────────────────────────

@router.post("/log-import", response_model=LogImportOut)
def log_import(
    body: LogImportIn,
    member: GuildMember = Depends(deps.require_permission("energy.manage")),
    db: Session = Depends(deps.db_session),
):
    """Aplica a log de energia do jogo no saldo de cada membro. Mesmo pipeline
    do bot legado (parse → resolve nick via BotRegistration ativa → dedup por
    (ts,player,amount) → whitelist ignorada → saldo soma do ledger)."""
    entries = energy_svc.parse_energy_log(body.log_text)
    if not entries:
        raise HTTPException(400, "nenhum lançamento válido na log (verifique as aspas)")

    res = energy_svc.apply_parsed_entries(
        db, member.guild_id, entries, name_resolver=_resolve_nick,
    )
    before = {"lines_parsed": len(entries)}
    after = {
        "applied": res.applied,
        "duplicates": res.duplicates,
        "whitelisted_applied": res.whitelisted_applied,
        "unregistered": dict(res.unregistered or {}),
    }
    _audit(
        db, member.guild_id, member,
        action="energy.log_import", entity_id=None,
        before=before, after=after,
        note=f"{res.applied} aplicados, {res.duplicates} duplicatas, "
             f"{res.whitelisted_applied} whitelisted",
    )
    _mark_energy_control_dirty(db, member.guild_id)
    db.commit()
    return LogImportOut(
        result=ApplyResultOut(
            applied=res.applied,
            duplicates=res.duplicates,
            whitelisted_applied=res.whitelisted_applied,
            unregistered=dict(res.unregistered or {}),
        ),
        unregistered=dict(res.unregistered or {}),
    )


@router.post("/set", response_model=ManualSetOut)
def manual_set(
    body: ManualSetIn,
    member: GuildMember = Depends(deps.require_permission("energy.manage")),
    db: Session = Depends(deps.db_session),
):
    """Define manualmente o saldo de energia de um membro (ajuste compensatório,
    /setenergy do bot legado). Emite um único lançamento kind='adjustment'
    cujo amount é a diferença — a invariante balance == sum(amount) se mantém."""
    before_bal = energy_svc.get_balance(db, member.guild_id, body.user_id)
    new_bal = energy_svc.manual_set(
        db, member.guild_id, body.user_id, body.value,
        actor_discord_id=member.user_id, reason=body.reason,
    )
    _audit(
        db, member.guild_id, member,
        action="energy.manual_set", entity_id=str(body.user_id),
        before={"balance": before_bal},
        after={"balance": new_bal, "value": body.value, "reason": body.reason},
    )
    _mark_energy_control_dirty(db, member.guild_id)
    db.commit()
    return ManualSetOut(user_id=body.user_id, balance=new_bal)


@router.post("/whitelist/{user_id}", response_model=WhitelistToggleOut)
def toggle_whitelist(
    user_id: int,
    member: GuildMember = Depends(deps.require_permission("energy.manage")),
    db: Session = Depends(deps.db_session),
):
    """Liga/desliga um membro na whitelist de energia (quem cuida da guilda é
    ignorado nas logs, igual ao bot legado)."""
    before = bool(db.scalar(select(EnergyWhitelist.id).where(
        EnergyWhitelist.guild_id == member.guild_id,
        EnergyWhitelist.discord_user_id == user_id,
    )))
    added = energy_svc.toggle_whitelist(
        db, member.guild_id, user_id, added_by=member.user_id,
    )
    _audit(
        db, member.guild_id, member,
        action="energy.whitelist_toggle", entity_id=str(user_id),
        before={"whitelisted": before},
        after={"whitelisted": added},
    )
    _mark_energy_control_dirty(db, member.guild_id)
    db.commit()
    return WhitelistToggleOut(user_id=user_id, whitelisted=added)


@router.get("/overview", response_model=OverviewOut)
def overview(
    member: GuildMember = Depends(deps.require_permission("energy.manage")),
    db: Session = Depends(deps.db_session),
):
    """Saldo de energia de cada membro da guilda, com flag de whitelist e de
    energia baixa (vs Guild.settings.energy_alert_threshold, default 50).
    Bounded e tenant-scoped."""
    guild = db.scalar(select(Guild).where(Guild.id == member.guild_id))
    threshold = _guild_threshold(guild)

    # Whitelist da guilda (uid → True).
    wl_ids = set(db.scalars(select(EnergyWhitelist.discord_user_id).where(
        EnergyWhitelist.guild_id == member.guild_id,
    )).all())

    # Registrados (BotRegistration ativa) — mapa uid → primeiro nome de personagem.
    reg_rows = db.execute(
        select(BotRegistration.discord_user_id, BotRegistration.albion_player_name).where(
            BotRegistration.guild_id == member.guild_id,
            BotRegistration.active.is_(True),
        )
    ).all()
    reg_ids = {uid for uid, _ in reg_rows}
    # Primeiro nome de personagem ativo por uid (se múltiplos chars, o 1º vence).
    reg_names: dict[int, str] = {}
    for uid, name in reg_rows:
        if uid not in reg_names:
            reg_names[uid] = name

    # Usuários com pelo menos uma entry (histórico de energia).
    has_history_ids = set(db.scalars(
        select(EnergyEntry.discord_user_id).where(
            EnergyEntry.guild_id == member.guild_id,
        ).distinct()
    ).all())

    # Saldos da guilda.
    bal_rows = db.scalars(select(EnergyBalance).where(
        EnergyBalance.guild_id == member.guild_id,
    )).all()
    balances = {b.discord_user_id: int(b.balance) for b in bal_rows}

    # Membros ativos da guilda (left_at NULL) + nomes de User.
    rows_q = (
        select(GuildMember, User)
        .join(User, User.id == GuildMember.user_id)
        .where(
            GuildMember.guild_id == member.guild_id,
            GuildMember.left_at.is_(None),
        )
    )
    members = list(db.execute(rows_q).all())

    rows: list[OverviewRow] = []
    for gm, user in members:
        uid = gm.user_id
        # Só mostra registrados.
        if uid not in reg_ids:
            continue
        bal = balances.get(uid, 0)
        # Não mostra saldo 0 se nunca teve log.
        if bal == 0 and uid not in has_history_ids:
            continue
        rows.append(OverviewRow(
            user_id=uid,
            display_name=reg_names.get(uid) or _display_name(user),
            balance=bal,
            whitelisted=uid in wl_ids,
            low_energy=bal < threshold,
        ))
    # Ordena por saldo crescente — energia baixa no topo (onde o staff precisa agir).
    rows.sort(key=lambda r: (r.balance, r.display_name))
    return OverviewOut(threshold=threshold, members=rows)