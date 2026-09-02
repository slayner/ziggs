"""Portal do membro — /guilds/{guild_id}/member/*

Rotas member-facing: carteira (economia), energia, eventos publicados,
inscrições e preferências arma+fn. Tudo guardado por `require_active_guild_member`
(membro logado E ainda no servidor).

Diferente das rotas administrativas (events.py/comps.py/auth.py), NUNCA
retornamos `EventDetail` cru — ele carrega verificação interna, notas de
morte, listas de signup, transições admin e evidência de regear. Aqui
montamos payloads member-safe a partir das mesmas tabelas/serviços.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api import deps
from app.api.schemas.member import (
    EnergyEntryOut, EnergyOut, MemberCompDetail, MemberCompRef, MemberCompSummary,
    MemberEventDetail, MemberEventSummary, MemberPayoutRow,
    MemberSettlementOut, MemberSignupOut, SignupIn, SignupOptionOut,
    SignupOptionsOut, WalletOut, WalletTxOut, WeaponFnPrefOut,
    WeaponFnPrefsIn, WeaponFnPrefsOut, WeaponFnValidPairOut,
)
from app.domain.states import EventState
from app.models.catalog import GameRole, Weapon
from app.models.comp_preferences import WeaponFnPreference
from app.models.comps import Comp, CompParty, CompSlotRole
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.energy import EnergyBalance, EnergyEntry
from app.models.events import Event, EventParticipant
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember, User
from app.services import comps as comps_svc
from app.services import event_gates, event_signups, events as events_svc

router = APIRouter(prefix="/guilds/{guild_id}/member", tags=["member"])

# Estados visíveis ao membro: publicados (não-draft) e não removidos.
# draft = ainda não divulgado; cancelled/deleted = limpeza; review = pós-raid
# interno — o membro só vê scheduled/in_progress (auto-inscrição) e finalized
# (divulgação de settlement).
MEMBER_EVENT_STATES = frozenset({
    EventState.SCHEDULED, EventState.IN_PROGRESS, EventState.REVIEW, EventState.FINALIZED,
})


# ── helpers ─────────────────────────────────────────────────────────────────

def _guild_event_weapon_gates(db: Session, guild_id: int) -> dict[str, list[str]]:
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    return ((g.settings or {}) if g else {}).get("event_weapon_gates", {}) or {}


def _member_display_name(user: User) -> str:
    return user.global_name or user.username


def _member_discord_role_ids(member: GuildMember) -> set[int]:
    # JSON list de snowflakes (strings ou ints) — normaliza pra int.
    return {int(r) for r in (member.discord_role_ids or [])}


# ── carteira (economia) ──────────────────────────────────────────────────────

@router.get("/wallet", response_model=WalletOut)
def get_wallet(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Saldo e ledger de prata do próprio membro. Tenant-scoped por
    guild_id + user_id; a direção (in/out/neutral) é derivada server-side a
    partir de from_user_id/to_user_id — nunca do client."""
    uid = member.user_id
    bal = db.scalar(select(EconomyBalance).where(
        EconomyBalance.guild_id == member.guild_id,
        EconomyBalance.discord_user_id == uid,
    ))
    balance = int(bal.balance) if bal else 0
    total_earned = int(bal.total_earned) if bal else 0

    # Conta o total de transações do membro (pra paginação) — todas as linhas
    # onde ele aparece como from, to ou total_earned.
    count_q = (
        select(EconomyTransaction)
        .where(
            EconomyTransaction.guild_id == member.guild_id,
            (EconomyTransaction.from_user_id == uid)
            | (EconomyTransaction.to_user_id == uid)
            | (EconomyTransaction.total_earned_user_id == uid),
        )
    )
    total = db.scalar(
        select(func.count()).select_from(count_q.subquery())
    ) or 0

    rows = db.scalars(
        select(EconomyTransaction)
        .where(
            EconomyTransaction.guild_id == member.guild_id,
            (EconomyTransaction.from_user_id == uid)
            | (EconomyTransaction.to_user_id == uid)
            | (EconomyTransaction.total_earned_user_id == uid),
        )
        .order_by(EconomyTransaction.id.desc())
        .limit(limit).offset(offset)
    ).all()

    # Pré-carrega nomes das contrapartes (pay entre membros) em 1 query.
    counter_ids = {
        r.from_user_id for r in rows if r.from_user_id and r.from_user_id != uid
    } | {
        r.to_user_id for r in rows if r.to_user_id and r.to_user_id != uid
    }
    names: dict[int, str] = {}
    if counter_ids:
        for u in db.scalars(select(User).where(User.id.in_(counter_ids))):
            names[u.id] = u.global_name or u.username

    # Pré-carrega nicks Albion das contrapartes (BotRegistration ativa).
    # O membro quer ver "Slayner" em vez de (ou além de) "Joãozinho" quando
    # houver um /pay — é o nick que ele conhece do jogo.
    albion_names: dict[int, str] = {}
    if counter_ids:
        reg_rows = db.execute(
            select(BotRegistration.discord_user_id, BotRegistration.albion_player_name)
            .where(
                BotRegistration.guild_id == member.guild_id,
                BotRegistration.discord_user_id.in_(counter_ids),
                BotRegistration.active.is_(True),
            )
        ).all()
        for discord_id, albion_name in reg_rows:
            # Pode haver múltiplos chars; guarda o primeiro encontrado.
            if discord_id not in albion_names:
                albion_names[discord_id] = albion_name

    # Pré-carrega nomes dos admins/atores (add/remove/forfeit). O actor de pay
    # é o próprio pagador (já em names); o de event_payout é o finalizador
    # (não interessante mostrar); o de add/remove é o admin que executou.
    actor_ids = {
        r.actor_discord_id for r in rows
        if r.actor_discord_id
        and r.actor_discord_id != uid
        and r.actor_discord_id != r.from_user_id
        and r.actor_discord_id != r.to_user_id
        and r.kind in ("add", "remove", "forfeit")
    }
    actor_names: dict[int, str] = {}
    if actor_ids:
        for u in db.scalars(select(User).where(User.id.in_(actor_ids))):
            actor_names[u.id] = u.global_name or u.username

    # Pré-carrega eventos vinculados (event_payout/event_deficit).
    event_ids = {r.event_id for r in rows if r.event_id is not None}
    event_titles: dict[int, str] = {}
    if event_ids:
        for ev in db.scalars(select(Event).where(Event.id.in_(event_ids))):
            event_titles[ev.id] = ev.title or f"Evento #{ev.id}"

    txs = []
    for r in rows:
        if r.to_user_id == uid:
            direction = "in"
            cp_id = r.from_user_id
        elif r.from_user_id == uid:
            direction = "out"
            cp_id = r.to_user_id
        else:
            # total_earned_user_id == uid mas nem from nem to (ex.: ajuste de
            # sistema) — neutro.
            direction = "neutral"
            cp_id = None
        # Actor: só mostra pra add/remove/forfeit (o admin que disparou). Em
        # pay o actor é o pagador (já é a contraparte); em event_payout o actor
        # é o finalizador do evento (não relevante pro membro).
        actor_name = None
        if r.kind in ("add", "remove", "forfeit") and r.actor_discord_id and r.actor_discord_id != uid:
            actor_name = actor_names.get(r.actor_discord_id)
        txs.append(WalletTxOut(
            id=r.id, kind=r.kind, direction=direction, amount=r.amount,
            counterparty_name=names.get(cp_id) if cp_id else None,
            counterparty_albion_name=albion_names.get(cp_id) if cp_id else None,
            actor_name=actor_name,
            event_id=r.event_id,
            event_title=event_titles.get(r.event_id) if r.event_id else None,
            undone=r.undone, created_at=r.created_at,
        ))
    return WalletOut(
        balance=balance, total_earned=total_earned,
        transactions=txs, total=int(total),
    )


# ── energia ──────────────────────────────────────────────────────────────────

@router.get("/energy", response_model=EnergyOut)
def get_energy(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Saldo e ledger de energia (append-only) do próprio membro. Bounded e
    tenant-scoped."""
    uid = member.user_id
    bal = db.scalar(select(EnergyBalance).where(
        EnergyBalance.guild_id == member.guild_id,
        EnergyBalance.discord_user_id == uid,
    ))
    balance = int(bal.balance) if bal else 0

    count = db.scalar(
        select(func.count())
        .select_from(EnergyEntry)
        .where(
            EnergyEntry.guild_id == member.guild_id,
            EnergyEntry.discord_user_id == uid,
        )
    ) or 0

    rows = db.scalars(
        select(EnergyEntry)
        .where(
            EnergyEntry.guild_id == member.guild_id,
            EnergyEntry.discord_user_id == uid,
        )
        .order_by(EnergyEntry.id.desc())
        .limit(limit).offset(offset)
    ).all()
    entries = [
        EnergyEntryOut(
            id=e.id, kind=e.kind, ts=e.ts, player=e.player,
            reason=e.reason, amount=e.amount, created_at=e.created_at,
        )
        for e in rows
    ]
    return EnergyOut(balance=balance, entries=entries, total=count or 0)


# ── eventos ──────────────────────────────────────────────────────────────────

@router.get("/events", response_model=list[MemberEventSummary])
def list_events(
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Lista apenas eventos PUBLISHED (scheduled/in_progress/finalized).
    Draft/review/cancelled/deleted nunca aparecem ao membro."""
    rows = db.scalars(
        select(Event)
        .where(Event.guild_id == member.guild_id, Event.state.in_(MEMBER_EVENT_STATES))
        .order_by(Event.scheduled_at.is_(None), Event.scheduled_at.asc())
    )
    return [
        MemberEventSummary(
            id=e.id, state=e.state.value,
            type=e.type.value if e.type else None,
            title=e.title, caller_name=e.caller_name,
            scheduled_at=e.scheduled_at, started_at=e.started_at,
            ended_at=e.ended_at, comp_id=e.comp_id,
            can_signup=e.state in (EventState.SCHEDULED, EventState.IN_PROGRESS),
        )
        for e in rows
    ]


def _get_member_event(db: Session, guild_id: int, event_id: int) -> Event:
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id,
        Event.state.in_(MEMBER_EVENT_STATES),
    ))
    if ev is None:
        raise HTTPException(404, "evento não encontrado")
    return ev


def _comp_ref(db: Session, comp_id: int | None) -> MemberCompRef | None:
    if comp_id is None:
        return None
    c = db.scalar(select(Comp).where(Comp.id == comp_id))
    if c is None:
        return None
    return MemberCompRef(id=c.id, name=c.name, description=c.description)


def _settlement(ev: Event, db: Session) -> MemberSettlementOut | None:
    """Divulgação de settlement só pra FINALIZED, e só com valores
    EFETIVAMENTE pagos (EventParticipant.silver_received persistido no
    finalize). Não recomputa _calc_payout — settings podem ter mudado desde
    o finalize e rerodar daria número diferente do que o membro recebeu."""
    if ev.state is not EventState.FINALIZED:
        return None
    parts = db.scalars(
        select(EventParticipant)
        .where(EventParticipant.event_id == ev.id)
        .order_by(EventParticipant.silver_received.desc())
    ).all()
    rows = [
        MemberPayoutRow(
            user_id=p.user_id,
            display_name=p.user_name or str(p.user_id),
            silver_received=int(p.silver_received),
        )
        for p in parts if int(p.silver_received) != 0
    ]
    return MemberSettlementOut(
        tab_value=int(ev.tab_value),
        total_paid=sum(r.silver_received for r in rows),
        participants=rows,
    )


@router.get("/events/{event_id}", response_model=MemberEventDetail)
def get_event(
    event_id: int,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    ev = _get_member_event(db, member.guild_id, event_id)
    return MemberEventDetail(
        id=ev.id, state=ev.state.value,
        type=ev.type.value if ev.type else None,
        title=ev.title, message=ev.message,
        scheduled_at=ev.scheduled_at, started_at=ev.started_at,
        ended_at=ev.ended_at,
        comp=_comp_ref(db, ev.comp_id),
        settlement=_settlement(ev, db),
    )


# ── inscrições (self-signup, mesmos gates do bot) ───────────────────────────

@router.get("/events/{event_id}/signup-options", response_model=SignupOptionsOut)
def get_signup_options(
    event_id: int,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Deriva Discord roles do GuildMember ativo e chama o MESMO serviço do
    bot (event_signups.get_eligible_options + get_profile_options). Devolve
    opções elegíveis, motivo de recusa, pré-seleção e inscrição atual."""
    uid = member.user_id
    role_ids = _member_discord_role_ids(member)
    gates = _guild_event_weapon_gates(db, member.guild_id)
    try:
        eligible, reason, current, min_builds = event_signups.get_eligible_options(
            db, member.guild_id, event_id, uid, role_ids, gates,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    options = [SignupOptionOut(**o) for o in eligible]
    preselected: list[str] = []
    if eligible:
        ev = db.scalar(select(Event).where(Event.id == event_id))
        comp_id = ev.comp_id if ev else None
        by_key = {o.key: o for o in options}
        preselected = event_signups.get_profile_options(
            db, member.guild_id, comp_id, uid, by_key,
        )
    current_out = None
    if current is not None:
        current_out = MemberSignupOut(
            id=current.id,
            functions=list(current.functions or []),
            weapon_fns=[dict(e) for e in (current.weapon_fns or []) if isinstance(e, dict)],
            created_at=current.created_at,
        )
    return SignupOptionsOut(
        eligible=options, block_reason=reason,
        preselected=preselected, min_builds=min_builds,
        current=current_out,
    )


@router.post("/events/{event_id}/signup", response_model=MemberSignupOut)
def create_or_update_signup(
    event_id: int,
    payload: SignupIn,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Auto-inscrição pelo portal. Aceita SÓ pair keys (options) — identidade
    (user_id), nome e Discord roles são derivados server-side do membro
    ativo. Restrições de scheduled/in_progress vêm do serviço compartilhado
    (signup_block_reason), não de validação duplicada aqui."""
    uid = member.user_id
    user = db.scalar(select(User).where(User.id == uid))
    if user is None:
        raise HTTPException(403, "sem acesso a essa guilda")
    name = _member_display_name(user)
    role_ids = _member_discord_role_ids(member)
    gates = _guild_event_weapon_gates(db, member.guild_id)
    try:
        row = event_signups.upsert_signup(
            db, member.guild_id, event_id, uid, name, list(payload.options),
            role_ids, gates, actor_source="site",
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return MemberSignupOut(
        id=row.id,
        functions=list(row.functions or []),
        weapon_fns=[dict(e) for e in (row.weapon_fns or []) if isinstance(e, dict)],
        created_at=row.created_at,
    )


@router.delete("/events/{event_id}/signup", status_code=204)
def remove_signup(
    event_id: int,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Remove SÓ a inscrição do próprio membro. As restrições de estado vêm do
    serviço compartilhado (signup_block_reason)."""
    try:
        event_signups.remove_signup(
            db, member.guild_id, event_id, member.user_id,
            actor_source="site",
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return None


# ── comps (read-only) ────────────────────────────────────────────────────────

@router.get("/comps", response_model=list[MemberCompSummary])
def list_comps(
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Comps publicadas (não-arquivadas) da guilda, read-only. Reusa o serviço
    administrativo (comps.list_comps) — sem mutação."""
    return comps_svc.list_comps(db, member.guild_id, include_archived=False)


@router.get("/comps/{comp_id}", response_model=MemberCompDetail)
def get_comp(
    comp_id: int,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    detail = comps_svc.get_comp(db, member.guild_id, comp_id)
    if detail is None:
        raise HTTPException(404, "composição não encontrada")
    # MemberCompDetail espelha CompRead (parties/slots/roles públicos); sem
    # campos administrativos adicionais — CompRead já é só leitura.
    return MemberCompDetail(
        id=detail.id, name=detail.name, description=detail.description,
        archived=detail.archived, parties=detail.parties,
    )


# ── preferências arma+fn ─────────────────────────────────────────────────────

def _guild_valid_pairs(db: Session, guild_id: int) -> list[WeaponFnValidPairOut]:
    """Lista de pares (weapon_id, fn, weapon_name) das comps ATIVAS da guilda.
    O frontend usa isso pra montar o picker de preferências — só aparecem
    armas que estão em pelo menos uma comp. Arma de catálogo global que não
    está em nenhuma comp da guilda NÃO aparece."""
    comps = db.scalars(
        select(Comp).where(Comp.guild_id == guild_id, Comp.archived.is_(False))
        .options(selectinload(Comp.parties).selectinload(CompParty.slots))
    ).all()
    # weapon_id → set de fns
    pair_map: dict[int, set[str]] = {}
    for c in comps:
        for party in c.parties:
            for slot in party.slots:
                slot_fn = slot.fn or event_signups.FALLBACK_CATEGORY
                role_ids = list(db.scalars(
                    select(CompSlotRole.game_role_id).where(CompSlotRole.slot_id == slot.id)
                ))
                if not role_ids:
                    continue
                weapon_ids = set(db.scalars(
                    select(GameRole.weapon_id)
                    .where(GameRole.id.in_(role_ids), GameRole.weapon_id.is_not(None))
                ))
                for wid in weapon_ids:
                    pair_map.setdefault(wid, set()).add(event_gates.fn_key(slot_fn))

    weapon_ids = list(pair_map.keys())
    names: dict[int, str] = {}
    if weapon_ids:
        for w in db.scalars(select(Weapon).where(Weapon.id.in_(weapon_ids))):
            names[w.id] = w.name

    out: list[WeaponFnValidPairOut] = []
    for wid in sorted(weapon_ids, key=lambda x: names.get(x, f"w{x}")):
        for fn in sorted(pair_map[wid]):
            out.append(WeaponFnValidPairOut(
                weapon_id=wid, fn=fn,
                weapon_name=names.get(wid) or f"w{wid}",
            ))
    return out


def _valid_pair_keys(db: Session, guild_id: int) -> set[str]:
    """Set de pair_keys válidos — usado pra validar o PUT."""
    return {event_gates.pair_key(p.weapon_id, p.fn) for p in _guild_valid_pairs(db, guild_id)}


@router.get("/weapon-fn-preferences", response_model=WeaponFnPrefsOut)
def get_weapon_fn_preferences(
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    prefs = db.scalars(
        select(WeaponFnPreference).where(
            WeaponFnPreference.guild_id == member.guild_id,
            WeaponFnPreference.user_id == member.user_id,
        )
    ).all()
    weapon_ids = {p.weapon_id for p in prefs}
    names: dict[int, str] = {}
    if weapon_ids:
        for w in db.scalars(select(Weapon).where(Weapon.id.in_(weapon_ids))):
            names[w.id] = w.name
    return WeaponFnPrefsOut(
        preferences=[
            WeaponFnPrefOut(
                weapon_id=p.weapon_id, fn=p.fn,
                weapon_name=names.get(p.weapon_id) or f"w{p.weapon_id}",
            )
            for p in prefs
        ],
        valid_pairs=_guild_valid_pairs(db, member.guild_id),
    )


@router.put("/weapon-fn-preferences", response_model=WeaponFnPrefsOut)
def put_weapon_fn_preferences(
    payload: WeaponFnPrefsIn,
    member: GuildMember = Depends(deps.require_active_guild_member),
    db: Session = Depends(deps.db_session),
):
    """Substitui as preferências do membro. Cada par submetido é validado
    contra a união das comps ativas; pares desconhecidos são rejeitados (não
    salvos silenciosamente). Só os pares válidos são preservados."""
    valid_keys = _valid_pair_keys(db, member.guild_id)
    # Normaliza submissão -> {(weapon_id, fn)} e rejeita desconhecidos.
    submitted: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for entry in payload.preferences:
        try:
            wid = int(entry.get("weapon_id"))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(400, "weapon_id inválido")
        fn_raw = entry.get("fn")
        if not isinstance(fn_raw, str) or not fn_raw.strip():
            raise HTTPException(400, "fn inválido")
        fn = event_gates.fn_key(fn_raw)
        key = event_gates.pair_key(wid, fn)
        if key not in valid_keys:
            raise HTTPException(400, f"par desconhecido nesta guilda: {key}")
        pair = (wid, fn)
        if pair in seen:
            continue
        seen.add(pair)
        submitted.append(pair)

    existing = list(db.scalars(
        select(WeaponFnPreference).where(
            WeaponFnPreference.guild_id == member.guild_id,
            WeaponFnPreference.user_id == member.user_id,
        )
    ))
    existing_map = {(p.weapon_id, p.fn): p for p in existing}
    submitted_set = set(submitted)

    for pair, row in existing_map.items():
        if pair not in submitted_set:
            db.delete(row)
    for wid, fn in submitted:
        if (wid, fn) not in existing_map:
            db.add(WeaponFnPreference(
                guild_id=member.guild_id, user_id=member.user_id,
                weapon_id=wid, fn=fn,
            ))
    db.commit()
    return get_weapon_fn_preferences(member=member, db=db)