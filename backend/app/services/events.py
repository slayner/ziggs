"""
Serviço de eventos: cria, lista, lê e move eventos pela máquina de estados.

Toda mudança de estado passa por `state_machine.transition` (grava transição +
audit). Carimba started_at/callout_at/ended_at conforme entra em cada fase.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas.events import (
    AttendanceIn, BattleAbsenteeOut, DeathIn, DeathOut, DeathUpdate, EventCreate,
    EventDetail, EventSummary, ParticipantIn, ParticipantOut, ParticipantUpdate,
    PayoutPreview, PayoutRow, RegearSummary, SignupOut, VerificationStepOut,
)
from app.domain import state_machine
from app.domain.states import (
    REQUIRED_VERIFICATION_STEPS, EventSeriousness, EventState,
    ParticipationMode, VerificationStep, allowed_targets,
)
from app.models.audit import AuditLog
from app.models.battles import Battle, BattleGuild, BattleParticipant
from app.models.economy import EconomyTransaction
from app.models.events import Event, EventDeath, EventParticipant, EventVerificationStep
from app.models.regear import RegearRequest
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember
from app.services import economy as economy_svc
from app.services import lootlog


class ServiceError(Exception):
    """Erro de regra de negócio (vira 400 na rota)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    db: Session, guild_id: int, actor_id: int | None, action: str,
    entity: str, entity_id: str | int | None,
    before: dict | None = None, after: dict | None = None,
    event_id: int | None = None,
) -> None:
    """Helper de auditoria — trilha imutável de mutações de eventos."""
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action=action, entity=entity, entity_id=str(entity_id) if entity_id is not None else None,
        before=before, after=after, event_id=event_id,
    ))


def _mark_dirty(ev: Event) -> None:
    """Marca que o mass-info E o embed por evento precisam ser reconstruídos pelo
    bot-v2 (outbox). Toda mutação de evento chama isto."""
    ev.signup_message_dirty = True
    ev.event_embed_dirty = True


def _regear_summary(db: Session, ev: Event) -> RegearSummary:
    """Resumo dos regears da thread do evento (landmark). Conta por status e soma
    o valor aprovado (final_total ?? suggested_total). Alimenta a linha de
    review e o gate de finalize em modos tab (leftover/guild_backed)."""
    rows = db.scalars(select(RegearRequest).where(
        RegearRequest.event_id == ev.id,
    )).all()
    pending = approved = denied = 0
    approved_total = 0
    for r in rows:
        if r.status == "paid":
            approved += 1
            approved_total += r.final_total if r.final_total is not None else r.suggested_total
        elif r.status == "denied":
            denied += 1
        elif r.status == "pending":
            pending += 1
    return RegearSummary(pending=pending, approved=approved, denied=denied,
                         approved_total=approved_total)


# ---------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------

def _get(db: Session, guild_id: int, event_id: int) -> Event | None:
    return db.scalar(
        select(Event)
        .where(Event.id == event_id, Event.guild_id == guild_id)
        .options(
            selectinload(Event.verification_steps),
            selectinload(Event.participants),
            selectinload(Event.deaths),
            selectinload(Event.signups),
            selectinload(Event.assignments),
        )
    )


def _participant_valid(ev: Event, p) -> bool:
    """Regular vs irregular: override do admin (is_valid True/False) ou, sem
    override, derivado — válido sse o user se INSCREVEU no evento. Presença na
    call sem inscrição = irregular por padrão (o admin valida arrastando)."""
    if p.is_valid is not None:
        return bool(p.is_valid)
    return any(s.user_id == p.user_id for s in ev.signups)


def _participant_origin(
    p, scaled_ids: set[int], signed_up_ids: set[int], battle_user_ids: set[int],
    member_user_ids: set[int],
) -> str | None:
    """Origem da presença do participante além da escalação. Escalados não
    recebem marcador (a escalação é a origem esperada). Os demais recebem um
    indicador mutuamente exclusivo:

      battle_no_call — estava numa batalha da guilda na janela do evento, mas
        não estava na call (snapshots_present == 0).
      call_outsider — estava na call mas NÃO é membro do servidor (amigo de
        fora entrando só pra assistir). Não entra no split — marcador existe
        justamente pra staff poder ignorar esses amigos.
      call_no_signup — estava na call (snapshots_present > 0), é membro do
        Discord, não se inscreveu e não foi escalado.
      call_signup — estava na call e se inscreveu, mas não foi escalado.
      manual — não estava na call nem em batalha: adicionado à mão pela staff.
    """
    if p.user_id in scaled_ids:
        return None
    in_call = p.snapshots_present > 0
    in_battle = p.user_id in battle_user_ids
    if in_battle and not in_call:
        return "battle_no_call"
    if in_call:
        if p.user_id not in member_user_ids:
            return "call_outsider"
        return "call_no_signup" if p.user_id not in signed_up_ids else "call_signup"
    return "manual"


if __name__ == "__main__":
    # ponytail: self-check do classificador de origem (lógica de branch).
    from types import SimpleNamespace as _NS
    p = lambda uid, snap: _NS(user_id=uid, snapshots_present=snap)
    M = lambda *uids: set(uids)  # membros do Discord passados explicitamente
    assert _participant_origin(p(1, 0), {1}, set(), set(), M(1)) is None            # escalado
    assert _participant_origin(p(1, 3), {1}, {1}, {1}, M(1)) is None               # escalado prevalece
    assert _participant_origin(p(1, 0), set(), set(), {1}, M(1)) == "battle_no_call"
    assert _participant_origin(p(1, 0), set(), {1}, {1}, M(1)) == "battle_no_call"  # batalha sem call, mesmo inscrito
    assert _participant_origin(p(1, 3), set(), set(), set(), M(1)) == "call_no_signup"
    assert _participant_origin(p(1, 3), set(), {1}, set(), M(1)) == "call_signup"
    assert _participant_origin(p(1, 3), set(), set(), set(), M()) == "call_outsider"  # amigo de fora na call
    assert _participant_origin(p(1, 0), set(), set(), set(), M(1)) == "manual"
    print("origin ok")


LOOTSPLIT_MODES = ("none", "leftover", "full", "guild_backed")


def get_lootsplit_mode(guild: Guild | None) -> str:
    """Setting da guilda (Guild.settings["lootsplit_mode"]) — decide só o
    LOOTSPLIT; regear é sempre calculado, não tem mais tipo de evento.
    "none" = sem lootsplit. "leftover" = regear sai da tab, sobra vira split
    (rombo negativo zera, ninguém cobre). "full" = tab inteira vira split,
    regear é custo à parte do banco (default, igual era o antigo
    lootsplit_regear). "guild_backed" = igual "leftover", mas rombo negativo
    é descontado igualmente de TODO membro da guilda (ver EconomyBalance em
    _finalize_payouts) em vez de zerar."""
    mode = (guild.settings or {}).get("lootsplit_mode") if guild else None
    return mode if mode in LOOTSPLIT_MODES else "full"


def _calc_payout(ev: Event, db: Session) -> PayoutPreview:
    """Calcula o preview de pagamento. Regear é SEMPRE calculado (universal);
    o lootsplit_mode da guilda decide se/como o valor da tab vira split.
    Participantes IRREGULARES ficam fora do split (sem linha, sem fatia do
    pool) — mortes/regear seguem pelas linhas explícitas de EventDeath."""
    guild = db.get(Guild, ev.guild_id) if db is not None else None
    mode = get_lootsplit_mode(guild)

    valid_participants = [p for p in ev.participants if _participant_valid(ev, p)]
    total_pct = sum(p.percent for p in valid_participants)
    rows: dict[int | None, dict] = {}

    total_regear_approved = sum(d.silver_value for d in ev.deaths if d.approved)

    # Regears da thread do evento (landmark). Em modos tab (leftover/guild_backed)
    # saem da tab — somam no total_regear e são atribuídos ao requester por linha.
    # Em full/none são independentes (débito de banco no approve) — não entram aqui.
    thread_regear_rows: list[tuple[int | None, str | None, int]] = []
    if db is not None and mode in ("leftover", "guild_backed"):
        for r in db.scalars(select(RegearRequest).where(
            RegearRequest.event_id == ev.id, RegearRequest.status == "paid",
        )).all():
            amt = r.final_total if r.final_total is not None else r.suggested_total
            if amt <= 0:
                continue
            total_regear_approved += amt
            thread_regear_rows.append((r.requester_user_id, r.requester_name, amt))

    # Fatia dos loggers (lootlog anônimo): logger_percent da tab é separada e
    # dividida pelo peso; o restante vai pros participantes. Não existe sem
    # lootsplit — sem pool pra repartir, não tem sentido cobrar corte de logger.
    logger_pool = 0
    logger_payouts: list[PayoutRow] = []
    if mode != "none" and guild is not None:
        pct = lootlog.get_lootlog_settings(guild).logger_percent
        weights = lootlog.compute_logger_weights(db, ev.guild_id, ev.id)
        total_w = sum(weights.values())
        if total_w > 0 and pct > 0:
            logger_pool = (ev.tab_value * pct) // 100
            name_of = {p.user_id: (p.user_name or str(p.user_id)) for p in ev.participants}
            for uid, w in sorted(weights.items(), key=lambda x: -x[1]):
                amount = (logger_pool * w) // total_w
                if amount <= 0:
                    continue
                logger_payouts.append(PayoutRow(
                    user_id=uid, display_name=name_of.get(uid, str(uid)),
                    percent=round(100 * w / total_w), lootsplit=amount,
                    regear=0, scout=0, total=amount,
                ))

    # guild_deficit_*: só preenchido em guild_backed quando o regear come mais
    # que a tab — é o rombo que _finalize_payouts vai descontar de cada membro
    # da guilda (EconomyBalance), em vez de zerar como o "leftover" faz.
    guild_deficit_total = 0
    guild_deficit_member_count = 0
    if mode == "none":
        participant_pool = 0
    elif mode in ("leftover", "guild_backed"):
        raw_pool = ev.tab_value - logger_pool - total_regear_approved
        if raw_pool < 0 and mode == "guild_backed":
            participant_pool = 0
            guild_deficit_total = -raw_pool
            if db is not None:
                guild_deficit_member_count = db.scalar(
                    select(func.count()).select_from(GuildMember)
                    .where(GuildMember.guild_id == ev.guild_id)
                ) or 0
        else:
            # ponytail: "leftover" sem cobertura de rombo — se o regear comer a
            # tab inteira, o split zera; ninguém "empresta" a diferença.
            participant_pool = max(0, raw_pool)
    else:  # "full"
        participant_pool = max(0, ev.tab_value - logger_pool)

    for p in valid_participants:
        lootsplit = 0
        if mode != "none" and total_pct > 0:
            lootsplit = int(participant_pool * p.percent / total_pct)
        rows[p.user_id] = {
            "user_id": p.user_id,
            "display_name": p.user_name or str(p.user_id),
            "percent": p.percent,
            "lootsplit": lootsplit,
            "regear": 0,
            "scout": 0,
            "total": lootsplit,
        }

    # Regear é universal agora — toda morte aprovada conta, em qualquer modo.
    for death in ev.deaths:
        if not death.approved:
            continue
        key = death.user_id
        if key in rows:
            rows[key]["regear"] += death.silver_value
            rows[key]["total"] += death.silver_value
        else:
            # Morte manual de alguém não listado como participante
            rows[key] = {
                "user_id": death.user_id,
                "display_name": death.display_name,
                "percent": 0,
                "lootsplit": 0,
                "regear": death.silver_value,
                "scout": 0,
                "total": death.silver_value,
            }

    # Regears da thread (modos tab) — atribui ao requester (quem postou a print
    # = quem morreu). Mesma lógica de linha da morte manual.
    for uid, name, amt in thread_regear_rows:
        if uid in rows:
            rows[uid]["regear"] += amt
            rows[uid]["total"] += amt
        else:
            rows[uid] = {
                "user_id": uid,
                "display_name": name or (str(uid) if uid is not None else "Regear"),
                "percent": 0, "lootsplit": 0, "regear": amt, "scout": 0, "total": amt,
            }

    # Scout: pool SEPARADO financiado pelo valor vendido de cada node capturado
    # (NodeDef.weight × sold_value). Não sai da tab — participantes repartem a tab
    # inteira entre si; o scout recebe por cima, como uma linha extra (igual ao
    # logger pool). Scout que também é participante soma na própria linha.
    scout_payouts: list[PayoutRow] = []
    if db is not None:
        from app.services import nodes as nodes_svc
        for log in nodes_svc.captured_for_event(db, ev.guild_id, ev.id):
            w = nodes_svc.weight_for(db, ev.guild_id, log.node_type)
            amount = int(log.sold_value * w)
            if amount <= 0 or log.scout_id is None:
                continue
            key = log.scout_id
            if key in rows:
                rows[key]["scout"] += amount
                rows[key]["total"] += amount
            else:
                rows[key] = {
                    "user_id": log.scout_id,
                    "display_name": log.scout_name or str(log.scout_id),
                    "percent": 0,
                    "lootsplit": 0,
                    "regear": 0,
                    "scout": amount,
                    "total": amount,
                }
            scout_payouts.append(PayoutRow(
                user_id=log.scout_id,
                display_name=log.scout_name or str(log.scout_id),
                percent=0, lootsplit=0, regear=0, scout=amount, total=amount,
            ))

    payouts = [PayoutRow(**r) for r in rows.values()]
    total_lootsplit = sum(r.lootsplit for r in payouts)
    logger_total = sum(r.lootsplit for r in logger_payouts)
    total_scout = sum(r.scout for r in scout_payouts)
    # Fechamento contábil: lootsplit reparte a pool proporcional ao percent;
    # a soma só pode ser ≤ pool (truncagem int). rounding_loss é a sobra NÃO
    # distribuída por truncagem — não confundir com o que foi deduzido de
    # propósito. Em "leftover" o regear já saiu da tab pro virar pool (ver
    # participant_pool acima), então precisa descontar de novo aqui pra não
    # contar a mesma prata como "perdida". Em "none"/"full" regear é custo à
    # parte do banco (não consome tab), então não entra nessa conta. Scout é
    # pool separado (financiado pelo valor do node, não pela tab). Nunca negativo.
    # "none": a tab inteira nunca teve intenção de virar split — não é "perda".
    spent_from_tab = total_regear_approved if mode in ("leftover", "guild_backed") else 0
    rounding_loss = 0 if mode == "none" else max(0, ev.tab_value - total_lootsplit - logger_total - spent_from_tab)
    return PayoutPreview(
        tab_value=ev.tab_value,
        lootsplit_mode=mode,
        payouts=payouts,
        total_lootsplit=total_lootsplit,
        total_regear=sum(r.regear for r in payouts),
        total_scout=total_scout,
        scout_payouts=scout_payouts,
        rounding_loss=rounding_loss,
        logger_pool=logger_pool,
        logger_payouts=logger_payouts,
        guild_deficit_total=guild_deficit_total,
        guild_deficit_member_count=guild_deficit_member_count,
    )


def _detail(ev: Event, db: Session) -> EventDetail:
    from app.models.catalog import GameRole
    step_map = {s.step: s for s in ev.verification_steps}
    steps = [
        VerificationStepOut(
            step=s.value,
            completed=bool(step_map[s].completed if s in step_map else False),
            data=step_map[s].data if s in step_map else {},
        )
        for s in VerificationStep
    ]
    role_ids = {p.game_role_id for p in ev.participants if p.game_role_id}
    role_names: dict[int, str] = {}
    if role_ids:
        role_names = {
            r.id: r.name
            for r in db.scalars(select(GameRole).where(GameRole.id.in_(role_ids)))
        }
    # Origem além-da-escalação: escalados não recebem marcador (origem esperada);
    # os demais recebem um de battle_no_call/call_outsider/call_no_signup/
    # call_signup/manual. member_user_ids distingue amigo de fora na call
    # (não membro do servidor → call_outsider, não entra no split).
    scaled_ids = {a.user_id for a in ev.assignments}
    signed_up_ids = {s.user_id for s in ev.signups}
    battle_user_ids_map = _battle_members(db, ev)
    battle_user_ids = set(battle_user_ids_map.keys())
    part_user_ids = {p.user_id for p in ev.participants}
    member_user_ids = set(db.scalars(
        select(GuildMember.user_id).where(
            GuildMember.guild_id == ev.guild_id,
            GuildMember.user_id.in_(part_user_ids),
        )
    )) if part_user_ids else set()
    participants = [
        ParticipantOut(
            id=p.id, user_id=p.user_id, user_name=p.user_name,
            percent=p.percent, base_percent=p.base_percent,
            is_trial=p.is_trial, silver_received=p.silver_received,
            snapshots_present=p.snapshots_present,
            game_role_id=p.game_role_id,
            game_role_name=role_names.get(p.game_role_id) if p.game_role_id else None,
            is_valid=_participant_valid(ev, p),
            origin=_participant_origin(p, scaled_ids, signed_up_ids, battle_user_ids, member_user_ids),
        )
        for p in ev.participants
    ]
    deaths = [
        DeathOut(
            id=d.id, user_id=d.user_id, display_name=d.display_name,
            silver_value=d.silver_value, notes=d.notes, approved=d.approved,
        )
        for d in ev.deaths
    ]
    signups = [
        SignupOut(
            id=s.id, user_id=s.user_id, user_name=s.user_name,
            functions=list(s.functions or []),
            created_at=s.created_at,
        )
        for s in ev.signups
    ]
    payout = (
        _calc_payout(ev, db)
        if ev.state in (EventState.REVIEW, EventState.FINALIZED)
        else None
    )
    regear_summary = _regear_summary(db, ev) if ev.regear_thread_id or ev.regear_thread_dirty else None
    return EventDetail(
        id=ev.id, state=ev.state.value,
        type=ev.type.value if ev.type else None,
        title=ev.title, message=ev.message, comp_id=ev.comp_id,
        scheduled_at=ev.scheduled_at, started_at=ev.started_at,
        callout_at=ev.callout_at, ended_at=ev.ended_at, is_loss=ev.is_loss,
        tab_value=ev.tab_value, tab_image_url=ev.tab_image_url,
        battleboard_url=ev.battleboard_url,
        seriousness=ev.seriousness.value, participation_mode=ev.participation_mode.value,
        functions_released=ev.functions_released, total_snapshots=ev.total_snapshots,
        attendance=ev.attendance,
        allowed_transitions=sorted(t.value for t in allowed_targets(ev.state)),
        verification=steps,
        participants=participants,
        deaths=deaths,
        signups=signups,
        battle_absentees=_absentees_from_members(battle_user_ids_map, ev),
        payout=payout,
        regear_summary=regear_summary,
    )


def _battle_members(db: Session, ev: Event) -> dict[int, str]:
    """Membros registrados (/register) vistos numa batalha REAL da guilda (API
    pública do Albion) dentro da janela do evento: {discord_user_id: nome}.
    Heurística por sobreposição de horário (não existe FK evento↔batalha). Só
    leitura. Usado tanto p/ flag de origem (battle_no_call) quanto p/ os
    absentees (membros em batalha sem nenhum EventParticipant)."""
    guild = db.get(Guild, ev.guild_id)
    if guild is None or not guild.albion_guild_id or ev.started_at is None:
        return {}
    window_end = ev.ended_at or _now()
    battle_ids = db.scalars(
        select(Battle.id).join(BattleGuild, BattleGuild.battle_id == Battle.id).where(
            BattleGuild.albion_guild_id == guild.albion_guild_id,
            Battle.start_time <= window_end,
            or_(Battle.end_time.is_(None), Battle.end_time >= ev.started_at),
        )
    ).all()
    if not battle_ids:
        return {}
    battle_players = {
        bp.albion_player_id: bp.name
        for bp in db.scalars(
            select(BattleParticipant).where(
                BattleParticipant.battle_id.in_(battle_ids),
                BattleParticipant.guild_id == guild.albion_guild_id,
            )
        )
    }
    if not battle_players:
        return {}
    out: dict[int, str] = {}
    regs = db.scalars(
        select(BotRegistration).where(
            BotRegistration.guild_id == ev.guild_id,
            BotRegistration.active.is_(True),
            BotRegistration.albion_player_id.in_(battle_players.keys()),
        )
    )
    for reg in regs:
        # Multi-char: primeiro registro vence (mesmo critério da escalação).
        if reg.discord_user_id in out:
            continue
        out[reg.discord_user_id] = battle_players.get(reg.albion_player_id) or reg.albion_player_name
    return out


def _absentees_from_members(members: dict[int, str], ev: Event) -> list[BattleAbsenteeOut]:
    """Membros em batalha na janela do evento sem nenhum EventParticipant (nem
    call, nem inscrição). Reaproveita o mapa já consultado em _detail — evita
    uma segunda query de batalha."""
    already = {p.user_id for p in ev.participants}
    return [
        BattleAbsenteeOut(user_id=uid, user_name=name)
        for uid, name in members.items()
        if uid not in already
    ]


# ---------------------------------------------------------------------------
# eventos
# ---------------------------------------------------------------------------

def create_event(
    db: Session, guild_id: int, payload: EventCreate,
    actor_id: int | None, caller_name: str | None,
) -> int:
    try:
        seriousness = EventSeriousness(payload.seriousness)
    except ValueError:
        raise ServiceError(f"seriousness inválida: {payload.seriousness}")
    try:
        participation_mode = ParticipationMode(payload.participation_mode)
    except ValueError:
        raise ServiceError(f"participation_mode inválido: {payload.participation_mode}")
    # Sem tipo de evento — regear e lootsplit são sempre calculados (ver
    # _calc_payout); o que muda é só o lootsplit_mode da guilda (Guild.settings).

    # Horário do evento é SEMPRE UTC. Se chegar naive (ex.: cliente sem tz),
    # assume UTC; se chegar aware, normaliza pra UTC. (SQLite descarta o tzinfo
    # no storage — a leitura normaliza de volta em event_signups._ensure_utc.)
    scheduled_at = payload.scheduled_at
    if scheduled_at is not None:
        scheduled_at = scheduled_at.astimezone(timezone.utc) if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)

    ev = Event(
        guild_id=guild_id,
        state=EventState.SCHEDULED,
        title=payload.title,
        message=payload.message,
        scheduled_at=scheduled_at,
        comp_id=payload.comp_id,
        seriousness=seriousness,
        participation_mode=participation_mode,
        caller_id=actor_id,
        caller_name=caller_name,
    )
    # Já nasce pronto pro mass-info se tiver comp — o polling do bot-v2 posta
    # assim que existir um events_channel_id configurado pra guilda.
    if payload.comp_id:
        ev.signup_message_dirty = True
        # (sem embed thread ainda — event_embed_dirty fica False até o callout)
    db.add(ev)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="event.create", entity="event", entity_id=str(ev.id),
        after={"state": ev.state.value, "title": ev.title},
    ))
    # Enfileira gatilho de ping "created" — o bot consome no próximo poll e
    # faz o bump (+ @everyone se a guilda deixou esse gatilho ligado). Import
    # tardio: event_signups importa events (ServiceError), evita ciclo no load.
    from app.services import event_signups as event_signups_svc
    event_signups_svc._enqueue_ping(db, guild_id, ev, event_signups_svc.PING_TRIGGER_CREATED)
    return ev.id


def list_events(db: Session, guild_id: int) -> list[EventSummary]:
    rows = db.scalars(
        select(Event).where(Event.guild_id == guild_id).order_by(Event.id.desc())
    )
    return [
        EventSummary(
            id=e.id, state=e.state.value,
            type=e.type.value if e.type else None,
            title=e.title, caller_name=e.caller_name,
            scheduled_at=e.scheduled_at, created_at=e.created_at,
            started_at=e.started_at, ended_at=e.ended_at,
            comp_id=e.comp_id, seriousness=e.seriousness.value,
            participation_mode=e.participation_mode.value,
        )
        for e in rows
    ]


def set_functions_released(
    db: Session, guild_id: int, event_id: int, released: bool, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    ev.functions_released = released
    _mark_dirty(ev)
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="event.set_functions_released", entity="event", entity_id=str(ev.id),
        after={"functions_released": released},
    ))
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def set_attendance(
    db: Session, guild_id: int, event_id: int, payload: AttendanceIn, actor_id: int | None,
) -> EventDetail:
    """Attendance é UM valor por evento (não por participante) — todo mundo
    que participou recebe a mesma quantidade, só o percent do split varia."""
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    before = ev.attendance
    ev.attendance = payload.value
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="event.set_attendance", entity="event", entity_id=str(ev.id),
        before={"attendance": before}, after={"attendance": payload.value},
    ))
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def list_signups(db: Session, guild_id: int, event_id: int) -> list[SignupOut]:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    return [
        SignupOut(
            id=s.id, user_id=s.user_id, user_name=s.user_name,
            functions=list(s.functions or []),
            created_at=s.created_at,
        )
        for s in ev.signups
    ]


def get_event(db: Session, guild_id: int, event_id: int) -> EventDetail | None:
    ev = _get(db, guild_id, event_id)
    return _detail(ev, db) if ev else None


def list_voice_active(db: Session, guild_id: int) -> list[dict]:
    """Eventos IN_PROGRESS com participation_mode=VOICE_PERCENT — alvo do
    snapshot loop do bot (a cada 30s). Leitura crua (sem _detail) pra ser
    barata; o bot só precisa dos ids."""
    rows = db.scalars(
        select(Event).where(
            Event.guild_id == guild_id,
            Event.state == EventState.IN_PROGRESS,
            Event.participation_mode == ParticipationMode.VOICE_PERCENT,
        )
    )
    return [{"id": e.id, "title": e.title} for e in rows]


def transition(
    db: Session, guild_id: int, event_id: int, to: str,
    actor_id: int | None, reason: str | None,
    *, actor_source: str = "site",
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    try:
        target = EventState(to)
    except ValueError:
        raise ServiceError(f"estado inválido: {to}")

    actor = state_machine.Actor(id=actor_id, source=actor_source)
    # Gate de finalize em modos tab: regears da thread pendentes bloqueiam
    # (alguns podem ser negados, e negados não puxam da tab). Antes do
    # state_machine.transition pra não deixar o evento meio-finalizado.
    if target is EventState.FINALIZED:
        guild = db.get(Guild, ev.guild_id)
        if get_lootsplit_mode(guild) in ("leftover", "guild_backed"):
            summ = _regear_summary(db, ev)
            if summ.pending > 0:
                raise ServiceError(
                    "evento tem regears da thread pendentes; julgue todos antes de finalizar"
                )
    try:
        state_machine.transition(db, ev, target, actor, reason)
    except (state_machine.TransitionDenied,) as e:
        raise ServiceError(str(e))
    except Exception as e:
        raise ServiceError(str(e))

    if target is EventState.IN_PROGRESS and ev.started_at is None:
        ev.started_at = _now()
    if target is EventState.IN_PROGRESS and not ev.regear_thread_id:
        # Bot cria a thread de regear no canal dedicado (outbox, espelho do
        # embed-dirty). Limpo quando o bot posta /regear-thread-synced.
        ev.regear_thread_dirty = True
    if target is EventState.IN_PROGRESS and not ev.lootlog_thread_id:
        # Mesmo outbox pra thread de lootlog (🪵 Log — Evento #N): o bot cria no
        # canal dedicado e limpa via /lootlog-thread-synced. Espelho do regear.
        ev.lootlog_thread_dirty = True
    elif target is EventState.REVIEW and ev.callout_at is None:
        # Callout: era em DEFINITION, agora roda em IN_PROGRESS→REVIEW. Seta
        # callout_at, freeza voice% (snapshots param de contar) e cria os
        # marcadores de verificação (TAB_VALUE/NODES) como opcional.
        ev.callout_at = _now()
        if ev.participation_mode is ParticipationMode.VOICE_PERCENT:
            _freeze_voice_percentages(db, ev)
        _ensure_steps(db, ev)
    elif target is EventState.FINALIZED:
        ev.ended_at = _now()
        _finalize_payouts(db, ev, actor_id)

    # Qualquer mudança de estado afeta o mass-info (novo emoji de status,
    # evento sai/entra da lista de ativos, etc.) — marca dirty pra o polling
    # do bot reconstruir o embed no próximo ciclo (agora 5s).
    _mark_dirty(ev)
    db.flush()
    # Pings de @everyone nos gatilhos de estado (status triggers sempre
    # bumpam; @everyone só se a guilda deixou o gatilho ligado). Import tardio
    # por causa do ciclo event_signups↔events (ver create_event).
    from app.services import event_signups as event_signups_svc
    if target is EventState.IN_PROGRESS:
        event_signups_svc._enqueue_ping(db, guild_id, ev, event_signups_svc.PING_TRIGGER_IN_PROGRESS)
    elif target is EventState.REVIEW:
        event_signups_svc._enqueue_ping(db, guild_id, ev, event_signups_svc.PING_TRIGGER_REVIEW)
    return _detail(ev, db)


def _ensure_steps(db: Session, ev: Event) -> None:
    existing = {s.step for s in ev.verification_steps}
    for step in REQUIRED_VERIFICATION_STEPS:
        if step not in existing:
            db.add(EventVerificationStep(event_id=ev.id, step=step, completed=False))
    db.flush()
    db.refresh(ev)


def set_step(
    db: Session, guild_id: int, event_id: int, step_str: str,
    completed: bool, data: dict | None, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    try:
        step = VerificationStep(step_str)
    except ValueError:
        raise ServiceError(f"passo inválido: {step_str}")
    # Fluxo novo: só TAB_VALUE e NODES são usados como marcadores opcionais em
    # review. Os outros (participation/missing_loots/tab_image/battles) são
    # legados do enum antigo — rejeita pra não criar linha órfã.
    if step not in (VerificationStep.TAB_VALUE, VerificationStep.NODES):
        raise ServiceError(f"passo {step.value} não é mais usado no fluxo de revisão")

    _ensure_steps(db, ev)
    row = next((s for s in ev.verification_steps if s.step == step), None)
    if row is None:
        row = EventVerificationStep(event_id=ev.id, step=step)
        db.add(row)
    row.completed = completed
    row.completed_by = actor_id
    row.completed_at = _now() if completed else None
    if data is not None:
        row.data = data

    # Espelha campos importantes de volta no evento para facilitar queries/relatórios.
    if step is VerificationStep.TAB_VALUE and data and "value" in data:
        ev.tab_value = int(data["value"])
    if step is VerificationStep.TAB_IMAGE and data and "url" in data:
        ev.tab_image_url = str(data["url"])
    if step is VerificationStep.BATTLES and data and "battleboard_url" in data:
        ev.battleboard_url = str(data["battleboard_url"]) or None

    _mark_dirty(ev)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# participantes
# ---------------------------------------------------------------------------

def add_participant(
    db: Session, guild_id: int, event_id: int,
    payload: ParticipantIn, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    existing = {p.user_id for p in ev.participants}
    if payload.user_id in existing:
        raise ServiceError("participante já registrado")
    db.add(EventParticipant(
        event_id=event_id, guild_id=guild_id,
        user_id=payload.user_id, user_name=payload.user_name,
        percent=payload.percent, base_percent=payload.base_percent,
        is_trial=payload.is_trial,
        is_valid=payload.is_valid,
    ))
    _audit(db, guild_id, actor_id, "event.add_participant", "event_participant", None,
           after={"event_id": event_id, "user_id": payload.user_id, "percent": payload.percent},
           event_id=event_id)
    _mark_dirty(ev)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def remove_participant(
    db: Session, guild_id: int, event_id: int,
    participant_id: int, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(
        select(EventParticipant).where(
            EventParticipant.id == participant_id,
            EventParticipant.event_id == event_id,
        )
    )
    if row is None:
        raise ServiceError("participante não encontrado")
    before = {"user_id": row.user_id, "percent": row.percent}
    db.delete(row)
    _audit(db, guild_id, actor_id, "event.remove_participant", "event_participant", participant_id,
           before=before, event_id=event_id)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def update_participant(
    db: Session, guild_id: int, event_id: int,
    participant_id: int, payload: ParticipantUpdate, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(
        select(EventParticipant).where(
            EventParticipant.id == participant_id,
            EventParticipant.event_id == event_id,
        )
    )
    if row is None:
        raise ServiceError("participante não encontrado")
    before = {"game_role_id": row.game_role_id, "percent": row.percent,
              "is_trial": row.is_trial, "is_valid": row.is_valid}
    if payload.game_role_id is not None:
        row.game_role_id = payload.game_role_id
    elif "game_role_id" in (payload.model_fields_set or set()):
        row.game_role_id = None
    if payload.percent is not None:
        row.percent = payload.percent
    if payload.is_trial is not None:
        row.is_trial = payload.is_trial
    if payload.is_valid is not None:
        row.is_valid = payload.is_valid
    # Mudou split/validade em review/finalized? Marca o embed do evento sujo.
    _mark_dirty(ev)
    _audit(db, guild_id, actor_id, "event.update_participant", "event_participant", participant_id,
           before=before, after=payload.model_dump(exclude_unset=True), event_id=event_id)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# mortes / regear
# ---------------------------------------------------------------------------

def add_death(
    db: Session, guild_id: int, event_id: int,
    payload: DeathIn, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    db.add(EventDeath(
        event_id=event_id, guild_id=guild_id,
        user_id=payload.user_id,
        display_name=payload.display_name,
        silver_value=payload.silver_value,
        notes=payload.notes,
        approved=False,
    ))
    _audit(db, guild_id, actor_id, "event.add_death", "event_death", None,
           after={"display_name": payload.display_name, "silver_value": payload.silver_value},
           event_id=event_id)
    _mark_dirty(ev)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def update_death(
    db: Session, guild_id: int, event_id: int,
    death_id: int, payload: DeathUpdate, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    death = db.scalar(
        select(EventDeath).where(
            EventDeath.id == death_id, EventDeath.event_id == event_id
        )
    )
    if death is None:
        raise ServiceError("morte não encontrada")
    before = {"approved": death.approved, "silver_value": death.silver_value, "notes": death.notes}
    if payload.approved is not None:
        death.approved = payload.approved
    if payload.silver_value is not None:
        death.silver_value = payload.silver_value
    if payload.notes is not None:
        death.notes = payload.notes
    _audit(db, guild_id, actor_id, "event.update_death", "event_death", death_id,
           before=before, after=payload.model_dump(exclude_unset=True), event_id=event_id)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def remove_death(
    db: Session, guild_id: int, event_id: int,
    death_id: int, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    death = db.scalar(
        select(EventDeath).where(
            EventDeath.id == death_id, EventDeath.event_id == event_id
        )
    )
    if death is None:
        raise ServiceError("morte não encontrada")
    before = {"display_name": death.display_name, "silver_value": death.silver_value}
    db.delete(death)
    _audit(db, guild_id, actor_id, "event.remove_death", "event_death", death_id,
           before=before, event_id=event_id)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# finalização
# ---------------------------------------------------------------------------

def _finalize_payouts(db: Session, ev: Event, actor_id: int | None = None) -> None:
    """Grava silver_received em cada participante, credita o EconomyBalance de
    cada um (é isto que faz a prata ficar de fato disponível pro /balance e
    /pay do bot — silver_received sozinho é só o valor mostrado no review) e
    cobre o regear.

    Participantes SEMPRE recebem prata (nunca é descontado). Em "leftover" e
    "guild_backed" o regear já saiu da PRÓPRIA tab (embutido no
    participant_pool calculado por _calc_payout) — o banco não é tocado. Em
    "none"/"full" o regear é custo do banco da guilda, que pode ficar negativo
    (ex.: CTA encerrado em perda). Em "guild_backed", se o regear tiver comido
    mais que a tab (guild_deficit_total > 0), o rombo é descontado igualmente
    do saldo de EconomyBalance de TODO membro da guilda (GuildMember) — ao
    contrário de "leftover", que simplesmente zera o split nesse caso.
    """
    payout = _calc_payout(ev, db)
    payout_map = {r.user_id: r for r in payout.payouts}
    for p in ev.participants:
        row = payout_map.get(p.user_id)
        if row:
            p.silver_received = row.total
            if row.total > 0:
                bal = economy_svc.get_or_create_balance(db, ev.guild_id, p.user_id)
                bal.balance += row.total
                bal.total_earned += row.total
                db.add(EconomyTransaction(
                    guild_id=ev.guild_id, kind="event_payout",
                    actor_discord_id=actor_id or 0,
                    from_user_id=None, to_user_id=p.user_id, total_earned_user_id=p.user_id,
                    amount=row.total,
                ))

    if payout.total_regear > 0 and payout.lootsplit_mode not in ("leftover", "guild_backed"):
        guild = db.get(Guild, ev.guild_id)
        if guild is not None:
            guild.bank_balance -= payout.total_regear

    if payout.lootsplit_mode == "guild_backed" and payout.guild_deficit_total > 0:
        member_ids = db.scalars(
            select(GuildMember.user_id).where(GuildMember.guild_id == ev.guild_id)
        ).all()
        # ponytail: truncagem int — se o déficit for menor que o nº de membros,
        # a sobra (< nº de membros de prata) fica sem cobrar de ninguém.
        per_member = payout.guild_deficit_total // len(member_ids) if member_ids else 0
        if per_member > 0:
            for uid in member_ids:
                bal = economy_svc.get_or_create_balance(db, ev.guild_id, uid)
                bal.balance -= per_member
                db.add(EconomyTransaction(
                    guild_id=ev.guild_id, kind="event_deficit",
                    actor_discord_id=actor_id or 0,
                    from_user_id=uid, to_user_id=None, total_earned_user_id=None,
                    amount=per_member,
                ))


# ---------------------------------------------------------------------------
# voz (VOICE_PERCENT) — snapshot loop do bot → freeze no callout
# ---------------------------------------------------------------------------

# Default do desconto de trial (knob de calibração em Guild.settings.trial_percent).
DEFAULT_TRIAL_PERCENT = 20


def voice_snapshot(
    db: Session, guild_id: int, event_id: int,
    present: list[dict], at: datetime | None = None,
) -> dict:
    """Acumula um snapshot da sala CTA: total_snapshots += 1 e
    snapshots_present += 1 p/ cada jogador presente (upsert de participante).

    Só produz efeito se o evento estiver VOICE_PERCENT + IN_PROGRESS — caso
    contrário é no-op (o bot pode chamar cego a cada 30s sem raciocinar).
    """
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    if ev.participation_mode is not ParticipationMode.VOICE_PERCENT:
        return {"ok": False, "reason": "not_voice_percent", "total_snapshots": ev.total_snapshots}
    if ev.state is not EventState.IN_PROGRESS:
        return {"ok": False, "reason": "not_in_progress", "total_snapshots": ev.total_snapshots}

    ev.total_snapshots += 1
    existing = {p.user_id: p for p in ev.participants}
    for entry in present:
        uid = int(entry["user_id"])
        name = entry.get("user_name")
        is_trial = bool(entry.get("is_trial"))
        row = existing.get(uid)
        if row is None:
            row = EventParticipant(
                event_id=event_id, guild_id=guild_id,
                user_id=uid, user_name=name,
                base_percent=0, percent=0, is_trial=is_trial,
                snapshots_present=1,
            )
            db.add(row)
            existing[uid] = row
            ev.participants.append(row)
        else:
            row.snapshots_present += 1
            if name and not row.user_name:
                row.user_name = name
            # Role é source-of-truth pra marcar trial: só escalona p/ True,
            # nunca clobber um un-flag manual do admin.
            if is_trial:
                row.is_trial = True
    db.flush()
    return {"ok": True, "total_snapshots": ev.total_snapshots,
            "present_count": len(present)}


def _trial_percent_for(db: Session, ev: Event) -> int:
    guild = db.get(Guild, ev.guild_id)
    if guild is None:
        return DEFAULT_TRIAL_PERCENT
    val = (guild.settings or {}).get("trial_percent")
    if isinstance(val, (int, float)) and 0 <= val <= 100:
        return int(val)
    return DEFAULT_TRIAL_PERCENT


def _freeze_voice_percentages(db: Session, ev: Event) -> None:
    """IN_PROGRESS→REVIEW (callout): converte snapshots_present em base_percent
    definitivo e aplica o desconto de trial em percent. Participantes sem
    snapshot ficam com 0 (não apareceram na voz). Manual edits em percent
    depois do freeze são respeitados (update_participant escreve direto)."""
    total = ev.total_snapshots
    if total <= 0:
        return  # evento de voz sem nenhum snapshot: mantém percents manuais
    trial_pct = _trial_percent_for(db, ev)
    for p in ev.participants:
        base = round(p.snapshots_present * 100 / total)
        p.base_percent = max(0, min(100, base))
        if p.is_trial:
            p.percent = round(p.base_percent * (1 - trial_pct / 100))
        else:
            p.percent = p.base_percent
    db.flush()


def _voice_self_check() -> None:  # pragma: no cover
    # ponytail: contrato do freeze — base clampado e trial sempre ≤ base.
    total = 10
    assert max(0, min(100, round(3 * 100 / total))) == 30
    base = 80
    assert round(base * (1 - 20 / 100)) == 64  # trial 20% → 64
    assert round(base * (1 - 0 / 100)) == base  # trial 0% → intacto


# ---------------------------------------------------------------------------
# embed por evento (thread 📑 EVENTO #N) — DTO + outbox dirty
# ---------------------------------------------------------------------------

# Estados em que o evento tem uma thread de embed (criada no callout/REVIEW).
EMBED_ACTIVE_STATES = frozenset({
    EventState.REVIEW, EventState.FINALIZED,
})


def embed_dto(db: Session, guild_id: int, event_id: int) -> dict | None:
    """DTO único pro embed do bot-v2: detail + nodes próximos do callout +
    escalação (read-only). Reusa _detail (participants/deaths/verification/
    payout/allowed_transitions)."""
    from app.models.events import EventAssignment
    from app.services import nodes as nodes_svc
    ev = _get(db, guild_id, event_id)
    if ev is None:
        return None
    detail = _detail(ev, db)
    ts = ev.callout_at or ev.started_at or _now()
    # scout_amount = sold_value × NodeDef.weight — o que o scout ganha por cima
    # da tab (pool separado). Só custa um lookup de peso por node capturado.
    nodes = []
    for n in nodes_svc.near_cta(db, guild_id, ts):
        is_mine = n.event_id == event_id
        sold = int(n.sold_value) if is_mine else 0
        w = nodes_svc.weight_for(db, guild_id, n.node_type) if is_mine else 1.0
        nodes.append({
            "node_type": n.node_type, "map_name": n.map_name,
            "spawn_at": n.spawn_at.isoformat() if n.spawn_at else None,
            "scout_name": n.scout_name,
            "scout_id": n.scout_id,
            "captured": bool(n.captured) if is_mine else False,
            "sold_value": sold,
            "scout_amount": int(sold * w),
            "node_log_id": n.id,
        })
    assignments = [
        {"slot_id": a.comp_slot_id, "user_id": a.user_id,
         "user_name": a.user_name, "game_role_id": a.game_role_id}
        for a in db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == event_id)
        )
    ]
    return {"event": detail, "nodes": nodes, "assignments": assignments,
            "event_channel_id": str(ev.event_channel_id) if ev.event_channel_id else None,
            "event_message_id": str(ev.event_message_id) if ev.event_message_id else None,
            "split_thread_id": str(ev.split_thread_id) if ev.split_thread_id else None}


def list_embed_dirty(db: Session, guild_id: int, force: bool = False) -> list[dict]:
    """Eventos com embed sujo (mutação ocorreu) e que têm thread de embed —
    alvo do loop de refresh do bot-v2. Devolve o DTO completo (embed_dto)
    inline pra cada evento — antes só devolvia ids e o bot fazia mais um GET
    /embed por evento (N+1: no catch-up de restart com force=True eram até 8
    round-trips sequenciais só pra listar, virando facilmente 9+ chamadas e
    sobrando pouco dos 5s de timeout do bot pra cada uma). Mesmo fix que já
    existia pra pending-work/mass-info (ver build_pending_work) — 1 request
    só, o cálculo de cada DTO fica inteiro no processo, sem round-trip extra.

    force=True ignora o dirty e devolve TODOS os eventos com thread ativa —
    usado só no catch-up de on_ready do bot-v2: um restart mata os botões já
    anexados em memória (EventEmbedView sem custom_id/add_view), e sem
    staleness-timer aqui (ao contrário do mass-info) o embed ficaria com
    botões mortos pra sempre até a próxima mutação marcar dirty de novo."""
    conditions = [Event.guild_id == guild_id, Event.state.in_(EMBED_ACTIVE_STATES)]
    if not force:
        conditions.append(Event.event_embed_dirty.is_(True))
    rows = db.scalars(select(Event).where(*conditions))
    out = []
    for e in rows:
        dto = embed_dto(db, guild_id, e.id)
        if dto is None:
            continue
        out.append({**dto, "event_id": e.id, "state": e.state.value})
    return out


def mark_embed_synced(db: Session, guild_id: int, event_id: int) -> bool:
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.event_embed_dirty = False
    db.flush()
    return True


def set_embed_ids(
    db: Session, guild_id: int, event_id: int, *,
    event_channel_id: int | None = None,
    event_message_id: int | None = None,
    lootlog_thread_id: int | None = None,
    split_thread_id: int | None = None,
    regear_thread_id: int | None = None,
    clear_dirty: bool = True,
) -> bool:
    """Grava os ids de canal/mensagem/thread que o bot criou/achou no Event.
    Usado tanto na criação da thread (callout) quanto no refresh — grava só o
    que veio (None = não tocar) e limpa o flag dirty se clear_dirty."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    if event_channel_id is not None:
        ev.event_channel_id = event_channel_id
    if event_message_id is not None:
        ev.event_message_id = event_message_id
    if lootlog_thread_id is not None:
        ev.lootlog_thread_id = lootlog_thread_id
    if split_thread_id is not None:
        ev.split_thread_id = split_thread_id
    if regear_thread_id is not None:
        ev.regear_thread_id = regear_thread_id
    if clear_dirty:
        ev.event_embed_dirty = False
    db.flush()
    return True


def set_regear_thread_id(
    db: Session, guild_id: int, event_id: int, thread_id: int | None,
    *, clear_dirty: bool = True,
) -> bool:
    """Grava o id da thread de regear criada pelo bot e limpa o flag dirty
    (outbox). Espelho de set_embed_ids, mas isolado pra não mexer no embed-dirty."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.regear_thread_id = thread_id
    if clear_dirty:
        ev.regear_thread_dirty = False
    db.flush()
    return True


def list_regear_thread_dirty(db: Session, guild_id: int) -> list[dict]:
    """Eventos com thread de regear pendente de criação — alvo do loop do
    bot-v2. Devolve id+title+state pra o bot nomear a thread.

    Inclui IN_PROGRESS/REVIEW/FINALIZED: o evento pode ficar só poucos
    segundos em IN_PROGRESS antes do callout, e se o bot não criou a thread
    nesse intervalo (bot reiniciando, janela curta, create falhou em silêncio),
    o flag dirty ficaria preso pra sempre. FINALIZED entra pra recuperar
    eventos que o bot perdeu a janela (espelha EMBED_ACTIVE_STATES do embed);
    a thread é criada e o loop arquiva no tick seguinte. CANCELLED/DELETED
    não entram — esses só vão pro archive. Exige regear_thread_id IS NULL
    pra não recriar thread já feita (segurança se o -synced falhou e o dirty
    ficou ligado)."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id,
        Event.state.in_((EventState.IN_PROGRESS, EventState.REVIEW, EventState.FINALIZED)),
        Event.regear_thread_dirty.is_(True),
        Event.regear_thread_id.is_(None),
    ))
    return [
        {"event_id": e.id, "title": e.title or f"Evento {e.id}",
         "state": e.state.value}
        for e in rows
    ]


def list_regear_thread_terminal(db: Session, guild_id: int) -> list[dict]:
    """Eventos em estado terminal (CANCELLED/DELETED/FINALIZED) com thread de
    regear ativa AINDA NÃO arquivada — alvo do loop do bot. Best-effort; o flag
    regear_thread_archived tira o evento da lista depois de arquivado (evita
    re-arquivar a cada tick e martelar rate-limit do Discord)."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id,
        Event.regear_thread_id.is_not(None),
        Event.regear_thread_archived.is_(False),
        Event.state.in_((EventState.CANCELLED, EventState.DELETED, EventState.FINALIZED)),
    ))
    return [
        {"event_id": e.id, "title": e.title or f"Evento {e.id}",
         "state": e.state.value, "regear_thread_id": str(e.regear_thread_id)}
        for e in rows
    ]


def mark_regear_thread_archived(db: Session, guild_id: int, event_id: int) -> bool:
    """Marca que o bot já arquivou (lock) a thread de regear — tira o evento da
    lista de arquivamento. regear_thread_id fica setado (resumo do review)."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.regear_thread_archived = True
    db.flush()
    return True


# ── thread de lootlog (espelho do regear) ────────────────────────────────────

def set_lootlog_thread_id(
    db: Session, guild_id: int, event_id: int, thread_id: int | None,
    *, clear_dirty: bool = True,
) -> bool:
    """Grava o id da thread de lootlog criada pelo bot e limpa o flag dirty."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.lootlog_thread_id = thread_id
    if clear_dirty:
        ev.lootlog_thread_dirty = False
    db.flush()
    return True


def list_lootlog_thread_dirty(db: Session, guild_id: int) -> list[dict]:
    """Eventos com thread de lootlog pendente de criação — alvo do loop do
    bot-v2. Espelho de list_regear_thread_dirty (mesmo motivo de incluir
    IN_PROGRESS/REVIEW/FINALIZED)."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id,
        Event.state.in_((EventState.IN_PROGRESS, EventState.REVIEW, EventState.FINALIZED)),
        Event.lootlog_thread_dirty.is_(True),
        Event.lootlog_thread_id.is_(None),
    ))
    return [
        {"event_id": e.id, "title": e.title or f"Evento {e.id}",
         "state": e.state.value}
        for e in rows
    ]


def list_lootlog_thread_terminal(db: Session, guild_id: int) -> list[dict]:
    """Eventos terminais com thread de lootlog ativa AINDA NÃO arquivada."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id,
        Event.lootlog_thread_id.is_not(None),
        Event.lootlog_thread_archived.is_(False),
        Event.state.in_((EventState.CANCELLED, EventState.DELETED, EventState.FINALIZED)),
    ))
    return [
        {"event_id": e.id, "title": e.title or f"Evento {e.id}",
         "state": e.state.value, "lootlog_thread_id": str(e.lootlog_thread_id)}
        for e in rows
    ]


def mark_lootlog_thread_archived(db: Session, guild_id: int, event_id: int) -> bool:
    """Marca que o bot já arquivou (lock) a thread de lootlog."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.lootlog_thread_archived = True
    db.flush()
    return True


def list_event_thread_terminal(db: Session, guild_id: int) -> list[dict]:
    """Eventos em estado terminal (CANCELLED/DELETED/FINALIZED) com thread de
    EMBED ativa AINDA NÃO arquivada — alvo do loop do bot. Espelho de
    list_regear_thread_terminal; event_channel_id só é de fato uma Thread
    quando a guilda tem sala de revisão configurada (senão é um canal comum
    sem o que trancar — o bot decide isso na hora, aqui só filtra por ids
    presentes). Best-effort; event_thread_archived tira da lista depois."""
    rows = db.scalars(select(Event).where(
        Event.guild_id == guild_id,
        Event.event_channel_id.is_not(None),
        Event.event_message_id.is_not(None),
        Event.event_thread_archived.is_(False),
        Event.state.in_((EventState.CANCELLED, EventState.DELETED, EventState.FINALIZED)),
    ))
    return [
        {"event_id": e.id, "title": e.title or f"Evento {e.id}",
         "state": e.state.value, "event_channel_id": str(e.event_channel_id)}
        for e in rows
    ]


def mark_event_thread_archived(db: Session, guild_id: int, event_id: int) -> bool:
    """Marca que o bot já arquivou (lock) a thread do embed — tira o evento da
    lista de arquivamento."""
    ev = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        return False
    ev.event_thread_archived = True
    db.flush()
    return True


if __name__ == "__main__":  # pragma: no cover
    _voice_self_check()
    print("voice self-check ok")
