"""Serviço de inscrições em eventos (signups) — substitui `cta_function_logs`
+ Google Sheets do bot antigo. Aplica o gate (`event_gates.py`) e mantém o
outbox flag (`Event.signup_message_dirty`) que o polling do bot-v2 consome
(ver `/bot/events/*` em `app/api/routes/auth.py`).

Mensagem única por guilda (não uma por evento) — replica o mass-info do bot
antigo, que é um único embed rolando com todos os CTAs ativos."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.states import EventState
from app.models.audit import AuditLog
from app.models.catalog import GameRole
from app.models.comp_preferences import CompRolePreference
from app.models.comps import Comp, CompParty, CompSlot, CompSlotRole
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


def signup_block_reason(
    state: EventState, comp_id: int | None, signup_mode: str = "signup",
) -> str | None:
    """Retorna o motivo de um evento não aceitar signup, se houver."""
    if state not in ACTIVE_STATES:
        return "evento não aceita inscrições neste estado"
    if signup_mode != "signup":
        return "evento é apenas anúncio"
    return None

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


def get_pending_function_prompts(settings: dict | None) -> list[dict]:
    return list((settings or {}).get("pending_function_prompts") or [])


def get_pending_function_prompt_deletes(settings: dict | None) -> list[dict]:
    return list((settings or {}).get("pending_function_prompt_deletes") or [])


def ack_function_prompts(db: Session, guild_id: int) -> None:
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return
    settings = dict(g.settings or {})
    if settings.get("pending_function_prompts"):
        settings["pending_function_prompts"] = []
        g.settings = settings
        db.flush()


def record_function_prompt_messages(
    db: Session, guild_id: int, messages: list[dict],
) -> None:
    """Persiste DMs para removê-las em review ou antes de uma nova troca de comp."""
    g = db.get(Guild, guild_id)
    if g is None:
        return
    settings = dict(g.settings or {})
    active = list(settings.get("function_prompt_messages") or [])
    pending = list(settings.get("pending_function_prompt_deletes") or [])
    active_ids = {str(item.get("message_id")) for item in active}
    pending_ids = {str(item.get("message_id")) for item in pending}
    for item in messages:
        event_id = int(item["event_id"])
        message_id = str(item["message_id"])
        record = {
            "event_id": event_id,
            "user_id": int(item["user_id"]),
            "message_id": message_id,
        }
        ev = db.get(Event, event_id)
        if ev is None or ev.guild_id != guild_id or ev.state not in ACTIVE_STATES:
            if message_id not in pending_ids:
                pending.append(record)
                pending_ids.add(message_id)
        elif message_id not in active_ids:
            active.append(record)
            active_ids.add(message_id)
    settings["function_prompt_messages"] = active
    settings["pending_function_prompt_deletes"] = pending
    g.settings = settings
    db.flush()


def queue_function_prompt_deletes(db: Session, guild_id: int, event_id: int) -> None:
    """Move as DMs rastreadas do evento para o outbox de exclusão."""
    g = db.get(Guild, guild_id)
    if g is None:
        return
    settings = dict(g.settings or {})
    active = list(settings.get("function_prompt_messages") or [])
    due = [item for item in active if int(item.get("event_id") or 0) == event_id]
    if not due:
        return
    pending = list(settings.get("pending_function_prompt_deletes") or [])
    pending_ids = {str(item.get("message_id")) for item in pending}
    pending.extend(item for item in due if str(item.get("message_id")) not in pending_ids)
    settings["function_prompt_messages"] = [
        item for item in active if int(item.get("event_id") or 0) != event_id
    ]
    settings["pending_function_prompt_deletes"] = pending
    g.settings = settings
    db.flush()


def ack_function_prompt_deletes(
    db: Session, guild_id: int, message_ids: set[str],
) -> None:
    g = db.get(Guild, guild_id)
    if g is None:
        return
    settings = dict(g.settings or {})
    pending = list(settings.get("pending_function_prompt_deletes") or [])
    remaining = [
        item for item in pending if str(item.get("message_id")) not in message_ids
    ]
    if remaining != pending:
        settings["pending_function_prompt_deletes"] = remaining
        g.settings = settings
        db.flush()


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
) -> tuple[list[event_gates.PartyDef], dict[str, str]]:
    """(parties, {nome_da_funcao: categoria}) — categoria vem do CompSlot.fn
    (tipo de função que o usuário definiu no CompBuilder: tank/healer/support/
    dps/pierce/battlemount/...), usada pro bot agrupar o picker.

    Cache: 2 queries (Comp selectinload + GameRole batch) por clique de botão.
    Durante clique-massa (30 pessoas em 10s) a comp não muda — TTL 30s corta
    de ~60 queries pra 2 por janela. Invalidação por TTL: se o admin editar a
    comp no meio, expira em até 30s."""
    if comp_id is None:
        return [], {}
    now = time.monotonic()
    cached = _party_defs_cache.get(comp_id)
    if cached and (now - cached[1]) < _PARTY_DEFS_TTL:
        return cached[0]
    result = _load_party_defs_uncached(db, comp_id)
    _party_defs_cache[comp_id] = (result, now)
    return result


_party_defs_cache: dict[int, tuple[tuple[list, dict], float]] = {}
_PARTY_DEFS_TTL = 30.0  # segundos


def _load_party_defs_uncached(
    db: Session, comp_id: int,
) -> tuple[list[event_gates.PartyDef], dict[str, str]]:
    """(parties, {nome_da_role: categoria}) — categoria vem do CompSlot.fn
    (tipo de função que o usuário definiu no CompBuilder: tank/healer/support/
    dps/pierce/battlemount/...). Se o slot não tem fn, cai em "other"."""
    comp = db.scalar(
        select(Comp)
        .where(Comp.id == comp_id)
        .options(selectinload(Comp.parties).selectinload(CompParty.slots).selectinload(CompSlot.roles))
    )
    if comp is None:
        return [], {}

    game_role_ids = {
        csr.game_role_id
        for party in comp.parties for slot in party.slots for csr in slot.roles
    }
    game_roles: dict[int, GameRole] = {}
    if game_role_ids:
        game_roles = {
            r.id: r for r in db.scalars(select(GameRole).where(GameRole.id.in_(game_role_ids)))
        }

    # Categoria de cada role = fn do slot onde ela aparece. Um role pode
    # aparecer em slots com fn diferente — usa o primeiro fn não-nulo que
    # encontrar (em prática a comp agrupa roles do mesmo fn no mesmo slot).
    categories: dict[str, str] = {}
    for party in comp.parties:
        for slot in party.slots:
            slot_fn = slot.fn or FALLBACK_CATEGORY
            for csr in slot.roles:
                role = game_roles.get(csr.game_role_id)
                if role and role.name not in categories:
                    categories[role.name] = slot_fn

    parties: list[event_gates.PartyDef] = []
    for party in comp.parties:
        names: set[str] = set()
        for slot in party.slots:
            for csr in slot.roles:
                role = game_roles.get(csr.game_role_id)
                if role:
                    names.add(role.name)
        parties.append(event_gates.PartyDef(total_slots=len(party.slots), role_names=names))
    return parties, categories

    parties: list[event_gates.PartyDef] = []
    for party in comp.parties:
        names: set[str] = set()
        for slot in party.slots:
            for csr in slot.roles:
                role = game_roles.get(csr.game_role_id)
                if role:
                    names.add(role.name)
        parties.append(event_gates.PartyDef(total_slots=len(party.slots), role_names=names))
    return parties, categories


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


def get_role_profile(db: Session, guild_id: int, comp_id: int | None, user_id: int) -> list[str]:
    """Return the active role profile for one player and composition."""
    if comp_id is None:
        return []
    comp_roles = list(db.scalars(
        select(GameRole)
        .join(CompSlotRole, CompSlotRole.game_role_id == GameRole.id)
        .join(CompSlot, CompSlot.id == CompSlotRole.slot_id)
        .join(CompParty, CompParty.id == CompSlot.party_id)
        .where(CompParty.comp_id == comp_id, GameRole.guild_id == guild_id)
        .order_by(CompParty.position, CompSlot.position, CompSlotRole.position, GameRole.name)
    ).unique())
    role_ids = {r.id for r in comp_roles}
    if not role_ids:
        return []
    saved = set(db.scalars(select(CompRolePreference.game_role_id).where(
        CompRolePreference.guild_id == guild_id,
        CompRolePreference.comp_id == comp_id,
        CompRolePreference.user_id == user_id,
        CompRolePreference.game_role_id.in_(role_ids),
    )))
    return [r.name for r in comp_roles if r.id in saved]


def _save_role_profile(
    db: Session, guild_id: int, comp_id: int | None, user_id: int, functions: list[str],
) -> None:
    """Replace the active profile with the roles selected in this CTA."""
    if comp_id is None:
        return
    roles = list(db.scalars(
        select(GameRole)
        .join(CompSlotRole, CompSlotRole.game_role_id == GameRole.id)
        .join(CompSlot, CompSlot.id == CompSlotRole.slot_id)
        .join(CompParty, CompParty.id == CompSlot.party_id)
        .where(CompParty.comp_id == comp_id, GameRole.guild_id == guild_id)
    ).unique())
    by_name = {r.name: r.id for r in roles}
    selected_ids = {by_name[f] for f in functions if f in by_name}
    existing = list(db.scalars(select(CompRolePreference).where(
        CompRolePreference.guild_id == guild_id,
        CompRolePreference.comp_id == comp_id,
        CompRolePreference.user_id == user_id,
    )))
    for row in existing:
        if row.game_role_id not in selected_ids:
            db.delete(row)
    existing_ids = {row.game_role_id for row in existing}
    for role_id in selected_ids - existing_ids:
        db.add(CompRolePreference(
            guild_id=guild_id, comp_id=comp_id, user_id=user_id, game_role_id=role_id,
        ))


def get_eligible_functions(
    db: Session, guild_id: int, event_id: int,
    discord_user_id: int, discord_role_ids: set[int],
    event_role_gates: dict[str, list[str]],
) -> tuple[list[str], str | None, EventSignup | None, dict[str, str], int | None]:
    """(funções elegíveis pra ESTE usuário agora, motivo_da_recusa, sua
    inscrição atual, {nome_da_funcao: categoria}, min_builds). Não existe
    limite máximo."""
    ev = _get_event(db, guild_id, event_id)
    if reason := signup_block_reason(ev.state, ev.comp_id, ev.signup_mode):
        raise ServiceError(reason)
    if ev.assignment_mode == "admin_assign":
        current = db.scalar(select(EventSignup).where(
            EventSignup.event_id == event_id,
            EventSignup.user_id == discord_user_id,
        ))
        return [], None, current, {}, None
    parties, categories = _load_party_defs(db, ev.comp_id)
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
    return functions, reason, current, categories, min_builds


def validate_role_minimum(functions: list[str], min_builds: int | None) -> None:
    if min_builds is None:
        return
    if len(functions) < min_builds:
        raise ServiceError(f"escolha ao menos {min_builds} roles")


def upsert_signup(
    db: Session, guild_id: int, event_id: int,
    user_id: int, user_name: str | None, functions: list[str],
    discord_role_ids: set[int], event_role_gates: dict[str, list[str]],
) -> EventSignup:
    ev = _get_event(db, guild_id, event_id)
    eligible, _reason, _current, _categories, min_builds = get_eligible_functions(
        db, guild_id, event_id, user_id, discord_role_ids, event_role_gates,
    )
    # Corta pra interseção com o que É elegível agora — resistência a um
    # botão desatualizado (o usuário viu a lista antes de outra pessoa
    # preencher a vaga entretanto).
    trimmed = [f for f in functions if f in eligible]
    if set(trimmed) != set(functions):
        raise ServiceError("uma ou mais roles não estão mais disponíveis")
    validate_role_minimum(trimmed, min_builds)

    # _current já é a linha deste usuário (veio do load de get_eligible_functions)
    # — sem segunda query pelo mesmo registro.
    row = _current
    before = list(row.functions or []) if row is not None else None
    if row is None:
        row = EventSignup(event_id=event_id, guild_id=guild_id, user_id=user_id)
        db.add(row)
    row.user_name = user_name
    row.functions = trimmed
    # Confirmar presença antes da liberação manda functions=[] e não deve apagar
    # um perfil já conhecido. Depois da liberação, [] é uma escolha explícita.
    if ev.functions_released or functions:
        _save_role_profile(db, guild_id, ev.comp_id, user_id, trimmed)

    db.add(AuditLog(
        guild_id=guild_id, actor_id=user_id, actor_type="bot", source="bot",
        action="signup.upsert", entity="event_signup", entity_id=None,
        event_id=event_id,
        before={"functions": before} if before is not None else None,
        after={"functions": trimmed, "user_name": user_name},
    ))
    ev.signup_message_dirty = True
    db.flush()
    if ev.autofill_mode == "on_signup":
        from app.services import event_escalation
        db.expire(ev, ["signups"])
        event_escalation.autofill_signup(
            db, guild_id, event_id, user_id, user_name, trimmed,
        )
    return row


def remove_signup(db: Session, guild_id: int, event_id: int, user_id: int) -> None:
    ev = _get_event(db, guild_id, event_id)
    if reason := signup_block_reason(ev.state, ev.comp_id, ev.signup_mode):
        raise ServiceError(reason)
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
            parties, _categories = _load_party_defs(db, e.comp_id)
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
            "signup_mode": e.signup_mode,
            "assignment_mode": e.assignment_mode,
            "autofill_mode": e.autofill_mode,
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
