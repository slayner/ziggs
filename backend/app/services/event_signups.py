"""Serviço de inscrições em eventos (signups) — substitui `cta_function_logs`
+ Google Sheets do bot antigo. Aplica o gate (`event_gates.py`) e mantém o
outbox flag (`Event.signup_message_dirty`) que o polling do bot-v2 consome
(ver `/bot/events/*` em `app/api/routes/auth.py`).

Mensagem única por guilda (não uma por evento) — replica o mass-info do bot
antigo, que é um único embed rolando com todos os CTAs ativos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.states import EventState
from app.models.audit import AuditLog
from app.models.catalog import GameRole, Weapon
from app.models.comps import Comp, CompParty, CompSlot
from app.models.events import Event, EventSignup
from app.models.tenancy import Guild, GuildRolePermission
from app.services import event_gates
from app.services.events import ServiceError

# Categoria "outra" quando a função não tem arma associada (ou a arma não tem
# invisible_function) — cai num grupo genérico no picker do bot em vez de sumir.
FALLBACK_CATEGORY = "other"

# Estados em que um evento ainda aparece no mass-info (aceita inscrição).
# Fluxo novo: review é pós-raid (verificações) — não aceita mais signups.
ACTIVE_STATES = frozenset({EventState.SCHEDULED, EventState.IN_PROGRESS})

# Janela "lupa" do bot antigo: T-10min até o evento começar, e mais um tempo
# rodando (aqui, até 1h depois do horário marcado) — período em que a prévia
# de parties é mostrada e o embed atualiza com mais frequência.
LUPA_WINDOW_BEFORE = timedelta(minutes=10)
LUPA_WINDOW_AFTER = timedelta(hours=1)
STALE_REFRESH_SECONDS = 15

# ── Pings de @everyone do mass-info (plataforma configurável por guilda) ──
#
# Gatilhos = momentos em que o mass-info deleta a embed antiga e reenvia (bump)
# pra poder pingar @everyone (editar mensagem não pingua quem já recebeu). O bot
# só executa o que o site manda (ver cogs/events.py sync_massinfo): o site decide
# SE cada gatilho dispara bump + @everyone (ping=True) ou bump silencioso
# (ping=False) — exceto t10min, que só age quando ligado (sem bump se desligado).
#
# created    — evento recém-criado (status trigger, sempre bumpa)
# t10min     — 10min antes do horário marcado (pure ping: só age se ligado)
# in_progress— scheduled → in_progress (status trigger, sempre bumpa)
# review     — in_progress → review (status trigger, sempre bumpa)
PING_TRIGGER_CREATED = "created"
PING_TRIGGER_T10MIN = "t10min"
PING_TRIGGER_IN_PROGRESS = "in_progress"
PING_TRIGGER_REVIEW = "review"
ALL_PING_TRIGGERS = (PING_TRIGGER_CREATED, PING_TRIGGER_T10MIN, PING_TRIGGER_IN_PROGRESS, PING_TRIGGER_REVIEW)
# Default quando a guilda nunca configurou: os 3 que o user pediu (review off).
DEFAULT_PING_TRIGGERS = frozenset({PING_TRIGGER_CREATED, PING_TRIGGER_T10MIN, PING_TRIGGER_IN_PROGRESS})
# t10min é pure-ping: só enqueue se ligado (não bumpa silencioso quando off).
_PURE_PING_TRIGGERS = frozenset({PING_TRIGGER_T10MIN})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite descarta o tzinfo mesmo com DateTime(timezone=True), então datas
    lidas do banco voltam naive. O evento é sempre UTC (ver create_event), então
    um naive == UTC. Normaliza pra aware antes de comparar com _now()."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _lupa_active(ev: Event) -> bool:
    if ev.scheduled_at is None:
        return False
    now = _now()
    sched = _ensure_utc(ev.scheduled_at)
    return (sched - LUPA_WINDOW_BEFORE) <= now <= (sched + LUPA_WINDOW_AFTER)


def get_ping_triggers(settings: dict | None) -> frozenset[str]:
    """Conjunto de gatilhos de @everyone habilitados pra essa guilda. Default
    DEFAULT_PING_TRIGGERS quando nunca configurado (settings None OU chave
    ausente). [] explícito = tudo off (admin desligou todos) — distinto de
    "nunca configurado". Valida contra ALL_PING_TRIGGERS, ignorando nomes
    desconhecidos (compat retroativa se a lista crescer)."""
    if settings is None or "events_ping_triggers" not in settings:
        return DEFAULT_PING_TRIGGERS
    raw = settings.get("events_ping_triggers") or []
    return frozenset(t for t in raw if t in ALL_PING_TRIGGERS)


def _enqueue_ping(
    db: Session, guild_id: int, ev: Event, trigger: str,
) -> None:
    """Appenda um gatilho ao outbox de pings do mass-info (consumido pelo bot no
    próximo poll de /bot/events/.../pending-work). Decisão de pingar vs bump
    silencioso vive aqui (fonte da verdade = o site):
      - status trigger (created/in_progress/review): sempre enqueue (bump);
        ping=True sse o gatilho está no conjunto habilitado da guilda.
      - pure-ping trigger (t10min): só enqueue se habilitado (ping=True).
    O bot só lê `ping` de cada entrada — nunca decide sozinho."""
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return
    settings = dict(g.settings or {})
    enabled = get_ping_triggers(settings)
    is_pure = trigger in _PURE_PING_TRIGGERS
    ping = trigger in enabled
    if is_pure and not ping:
        return  # t10min desligado: nem bump (não é mudança de estado, só barulho)
    outbox = list(settings.get("pending_ping_triggers") or [])
    outbox.append({"event_id": ev.id, "trigger": trigger, "ping": ping})
    settings["pending_ping_triggers"] = outbox
    g.settings = settings
    db.flush()


def announce_lupa_starts(db: Session, guild_id: int, events: list[Event]) -> None:
    """t10min: pra cada evento SCHEDULED que acabou de entrar na janela lupa
    (now >= scheduled_at - 10min) e ainda não foi anunciado, enqueue o gatilho
    (que só pinga se a guilda ligou t10min — ver _enqueue_ping). Idempotente via
    settings["lupa_announced"] (lista de event_ids já anunciados); poda os ids
    de eventos que saíram dos ativos pra a lista não crescer indefinidamente.

    Short-circuit sem DB quando não há nada a fazer (nada due E announced já
    consistente com os ativos) — evita um write por poll de 5s."""
    active_ids = {e.id for e in events}
    now = _now()
    due = [
        e for e in events
        if e.state is EventState.SCHEDULED
        and e.scheduled_at is not None
        and _ensure_utc(e.scheduled_at) - LUPA_WINDOW_BEFORE <= now
    ]
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return
    settings = dict(g.settings or {})
    announced = set(settings.get("lupa_announced") or [])
    pruned = announced & active_ids
    to_announce = [e for e in due if e.id not in announced]
    if not to_announce and pruned == announced:
        return  # nada due novo nem limpeza de announced — não escreve
    for e in to_announce:
        _enqueue_ping(db, guild_id, e, PING_TRIGGER_T10MIN)
        pruned.add(e.id)
    # _enqueue_ping pode ter recarregado g.settings; relê antes de sobrescrever
    # lupa_announced (ele não toca essa chave, mas o dict settings aqui é stale).
    settings = dict(g.settings or {})
    settings["lupa_announced"] = sorted(pruned)
    g.settings = settings
    db.flush()


def get_pending_ping_triggers(settings: dict | None) -> list[dict]:
    """Outbox de pings pronto pra o bot consumir (cada item: {event_id, trigger,
    ping}). Não limpa — o bot limpa via ack_ping_triggers depois de disparar."""
    return list((settings or {}).get("pending_ping_triggers") or [])


def ack_ping_triggers(db: Session, guild_id: int) -> None:
    """Esvazia o outbox de pings — chamado pelo bot depois de fazer o bump/ping,
    pra o próximo poll não repetir o mesmo @everyone."""
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return
    settings = dict(g.settings or {})
    if settings.get("pending_ping_triggers"):
        settings["pending_ping_triggers"] = []
        g.settings = settings
        db.flush()


def _load_party_defs(
    db: Session, comp_id: int | None,
) -> tuple[list[event_gates.PartyDef], dict[str, str], set[str]]:
    """(parties, {nome_da_funcao: categoria}, flex_names) — categoria vem de
    GameRole.weapon_id -> Weapon.invisible_function (tank/healer/support/dps/
    pierce), usada pro bot agrupar o picker. O bot antigo usava um emoji
    prefixado no nome da função pra isso; os GameRole reais daqui não têm
    essa convenção (conferido direto no banco antes de portar).

    `flex_names` = nomes das funções que pertencem a algum slot com >1 role
    (flex é emergente da estrutura do slot, sem flag no banco). Usado pela
    validação de min/max builds: roles flex contam no máx mas não no min (>1)."""
    if comp_id is None:
        return [], {}, set()
    comp = db.scalar(
        select(Comp)
        .where(Comp.id == comp_id)
        .options(selectinload(Comp.parties).selectinload(CompParty.slots).selectinload(CompSlot.roles))
    )
    if comp is None:
        return [], {}, set()

    game_role_ids = {
        csr.game_role_id
        for party in comp.parties for slot in party.slots for csr in slot.roles
    }
    game_roles: dict[int, GameRole] = {}
    if game_role_ids:
        game_roles = {
            r.id: r for r in db.scalars(select(GameRole).where(GameRole.id.in_(game_role_ids)))
        }
    weapon_ids = {r.weapon_id for r in game_roles.values() if r.weapon_id}
    weapon_functions: dict[int, str] = {}
    if weapon_ids:
        weapon_functions = {
            w.id: w.invisible_function
            for w in db.scalars(select(Weapon).where(Weapon.id.in_(weapon_ids)))
            if w.invisible_function
        }

    categories: dict[str, str] = {}
    for r in game_roles.values():
        categories[r.name] = weapon_functions.get(r.weapon_id, FALLBACK_CATEGORY) if r.weapon_id else FALLBACK_CATEGORY

    parties: list[event_gates.PartyDef] = []
    flex_names: set[str] = set()
    for party in comp.parties:
        names: set[str] = set()
        for slot in party.slots:
            is_flex_slot = len(slot.roles) > 1
            for csr in slot.roles:
                role = game_roles.get(csr.game_role_id)
                if role:
                    names.add(role.name)
                    if is_flex_slot:
                        flex_names.add(role.name)
        parties.append(event_gates.PartyDef(total_slots=len(party.slots), role_names=names))
    return parties, categories, flex_names


def _is_staff(db: Session, guild_id: int, discord_role_ids: set[int]) -> bool:
    if not discord_role_ids:
        return False
    rows = db.scalars(
        select(GuildRolePermission).where(
            GuildRolePermission.guild_id == guild_id,
            GuildRolePermission.discord_role_id.in_(discord_role_ids),
        )
    )
    return any(rp.permissions.get("events.manage") for rp in rows)


def _get_event(db: Session, guild_id: int, event_id: int) -> Event:
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        raise ServiceError("evento não encontrado")
    return ev


def signup_count(db: Session, event_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(EventSignup).where(EventSignup.event_id == event_id)
    ) or 0


def get_eligible_functions(
    db: Session, guild_id: int, event_id: int,
    discord_user_id: int, discord_role_ids: set[int],
    event_role_gates: dict[str, list[str]],
) -> tuple[list[str], str | None, EventSignup | None, dict[str, str], int | None, int | None, set[str]]:
    """(funções elegíveis pra ESTE usuário agora, motivo_da_recusa, sua
    inscrição atual, {nome_da_funcao: categoria}, min_builds, max_builds,
    flex_names). min/max vêm de Guild.settings (signup_min/max_builds);
    flex_names é o conjunto de funções que pertencem a slot com >1 role."""
    ev = _get_event(db, guild_id, event_id)
    parties, categories, flex_names = _load_party_defs(db, ev.comp_id)
    all_signups = db.scalars(select(EventSignup).where(EventSignup.event_id == event_id)).all()
    signup_function_1s = [s.functions[0] for s in all_signups if s.functions]
    current = next((s for s in all_signups if s.user_id == discord_user_id), None)

    is_staff = _is_staff(db, guild_id, discord_role_ids)
    functions, reason = event_gates.eligible_functions(
        parties, signup_function_1s, discord_role_ids, event_role_gates,
        ev.functions_released, is_staff,
    )
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    min_builds = settings.get("signup_min_builds")
    max_builds = settings.get("signup_max_builds")
    return functions, reason, current, categories, min_builds, max_builds, flex_names


def upsert_signup(
    db: Session, guild_id: int, event_id: int,
    user_id: int, user_name: str | None, functions: list[str],
    discord_role_ids: set[int], event_role_gates: dict[str, list[str]],
) -> EventSignup:
    ev = _get_event(db, guild_id, event_id)
    eligible, _reason, _current, _categories, min_builds, max_builds, flex_names = get_eligible_functions(
        db, guild_id, event_id, user_id, discord_role_ids, event_role_gates,
    )
    # Corta pra interseção com o que É elegível agora — resistência a um
    # botão desatualizado (o usuário viu a lista antes de outra pessoa
    # preencher a vaga entretanto).
    trimmed = [f for f in functions if f in eligible]
    total = len(trimmed)
    non_flex = [f for f in trimmed if f not in flex_names]
    # max: total de builds (flex conta). min: quando >1, só builds não-flex
    # contam (flex é opcional por cima); quando ==1, exige ao menos 1 build.
    if max_builds is not None and total > max_builds:
        raise ServiceError(f"máximo de {max_builds} builds")
    if min_builds is not None:
        if min_builds > 1 and len(non_flex) < min_builds:
            raise ServiceError(f"escolha ao menos {min_builds} builds não-flex")
        if min_builds == 1 and total < 1:
            raise ServiceError("escolha ao menos 1 build")

    # _current já é a linha deste usuário (veio do load de get_eligible_functions)
    # — sem segunda query pelo mesmo registro.
    row = _current
    before = list(row.functions or []) if row is not None else None
    if row is None:
        row = EventSignup(event_id=event_id, guild_id=guild_id, user_id=user_id)
        db.add(row)
    row.user_name = user_name
    row.functions = trimmed

    db.add(AuditLog(
        guild_id=guild_id, actor_id=user_id, actor_type="bot", source="bot",
        action="signup.upsert", entity="event_signup", entity_id=None,
        event_id=event_id,
        before={"functions": before} if before is not None else None,
        after={"functions": trimmed, "user_name": user_name},
    ))
    ev.signup_message_dirty = True
    db.flush()
    return row


def remove_signup(db: Session, guild_id: int, event_id: int, user_id: int) -> None:
    ev = _get_event(db, guild_id, event_id)
    row = db.scalar(
        select(EventSignup).where(EventSignup.event_id == event_id, EventSignup.user_id == user_id)
    )
    if row is not None:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=user_id, actor_type="bot", source="bot",
            action="signup.remove", entity="event_signup", entity_id=None,
            event_id=event_id, before={"functions": list(row.functions or [])},
        ))
        db.delete(row)
        ev.signup_message_dirty = True
    db.flush()


def list_active_events(db: Session, guild_id: int) -> list[Event]:
    return list(db.scalars(
        select(Event)
        .where(Event.guild_id == guild_id, Event.state.in_(ACTIVE_STATES))
        .order_by(Event.scheduled_at.is_(None), Event.scheduled_at.asc())
    ))


def catch_up_states(db: Session, guild_id: int, events: list[Event] | None = None) -> int:
    """Transição automática SCHEDULED -> IN_PROGRESS quando o horário marcado
    chegou. Roda a cada poll do bot-v2 (ver /bot/events/.../pending-work).

    O estado real vive no banco, não no bot — então eventos que já estavam
    IN_PROGRESS quando o bot caiu continuam nesse estado e voltam pro mass-info
    no primeiro poll após reconectar. O que falta é justamente os SCHEDULED cujo
    horário chegou enquanto o bot estava offline: este catch-up avança eles em
    lote. Enquanto o bot está online, avança em ~5s; se esteve offline, recupera
    tudo no primeiro poll do retorno."""
    if events is None:
        events = list_active_events(db, guild_id)
    now = _now()
    advanced = 0
    for e in events:
        if e.state is not EventState.SCHEDULED or e.scheduled_at is None:
            continue
        if _ensure_utc(e.scheduled_at) > now:
            continue
        # Import tardio: events importa lootlog, e event_signups é importado por
        # rotas — evita ciclo no load do módulo. Commit por evento: um erro num
        # (ex.: concorrência) não derruba o resto do lote.
        from app.services import events as events_svc
        try:
            events_svc.transition(
                db, guild_id, e.id, EventState.IN_PROGRESS.value,
                actor_id=None, reason="auto: scheduled_at reached",
            )
            db.commit()
            advanced += 1
        except events_svc.ServiceError:
            db.rollback()
    return advanced


def _dirty_terminal_events(db: Session, guild_id: int) -> list[Event]:
    """Eventos fora de ACTIVE_STATES (cancelado/finalizado/excluído) com o flag
    dirty ainda ligado. O embed do mass-info precisa ser reconstruído pra remover
    a linha deles — mas list_active_events os filtra, então has_pending_work não
    os veria sem esta consulta explícita."""
    return list(db.scalars(
        select(Event).where(
            Event.guild_id == guild_id,
            Event.signup_message_dirty.is_(True),
            ~Event.state.in_(ACTIVE_STATES),
        )
    ))


def has_pending_work(db: Session, guild_id: int, events: list[Event] | None = None) -> bool:
    if events is None:
        events = list_active_events(db, guild_id)
    if any(e.signup_message_dirty for e in events):
        return True
    # Dirty num evento que saiu dos ativos (ex.: acabou de ser cancelado) — o
    # embed ainda tem que ser reconstruído pra sumir com a linha correspondente.
    if _dirty_terminal_events(db, guild_id):
        return True
    if not events:
        return False
    lupa_events = [e for e in events if _lupa_active(e)]
    if not lupa_events:
        return False
    never_synced = [e for e in lupa_events if e.signup_last_synced_at is None]
    if never_synced:
        return True
    stalest = min(_ensure_utc(e.signup_last_synced_at) for e in lupa_events if e.signup_last_synced_at)
    return (_now() - stalest).total_seconds() > STALE_REFRESH_SECONDS


def build_pending_work(db: Session, guild_id: int, events: list[Event] | None = None) -> dict:
    if events is None:
        events = list_active_events(db, guild_id)
    comp_ids = {e.comp_id for e in events if e.comp_id}
    comp_names: dict[int, str] = {}
    if comp_ids:
        comp_names = {c.id: c.name for c in db.scalars(select(Comp).where(Comp.id.in_(comp_ids)))}

    # Uma query pra TODOS os signups dos eventos ativos — alimenta contagem e
    # prévia de parties de uma vez (antes: 1 count por evento + 1 select extra
    # por evento em lupa, a cada tick de ~25s do polling do bot).
    signups_by_event: dict[int, list[EventSignup]] = {}
    if events:
        for s in db.scalars(select(EventSignup).where(EventSignup.event_id.in_([e.id for e in events]))):
            signups_by_event.setdefault(s.event_id, []).append(s)

    out = []
    for e in events:
        lupa = _lupa_active(e)
        ev_signups = signups_by_event.get(e.id, [])
        parties_payload = None
        if lupa and e.comp_id:
            parties, _categories, _flex = _load_party_defs(db, e.comp_id)
            fns = [s.functions[0] for s in ev_signups if s.functions]
            parties_payload = [
                {
                    "party": i + 1, "total": p.total_slots,
                    "filled": min(p.total_slots, sum(1 for fn in fns if fn in p.role_names)),
                }
                for i, p in enumerate(parties)
            ]
        out.append({
            "event_id": e.id,
            "state": e.state.value,
            "title": e.title,
            "message": e.message,
            "scheduled_at": _ensure_utc(e.scheduled_at).isoformat() if e.scheduled_at else None,
            "comp_id": e.comp_id,
            "comp_name": comp_names.get(e.comp_id) if e.comp_id else None,
            "seriousness": e.seriousness.value,
            "type": e.type.value if e.type else None,
            "signup_count": len(ev_signups),
            "lupa_active": lupa,
            "parties": parties_payload,
        })
    return {"events": out}


def mark_massinfo_synced(db: Session, guild_id: int) -> None:
    now = _now()
    for e in list_active_events(db, guild_id):
        e.signup_message_dirty = False
        e.signup_last_synced_at = now
    # Limpa também o dirty preso em eventos terminais (cancelado/finalizado/
    # excluído) — senão has_pending_work devolveria True pra sempre e o bot
    # reeditaria o embed a cada ciclo de 5s.
    for e in _dirty_terminal_events(db, guild_id):
        e.signup_message_dirty = False
    db.flush()
