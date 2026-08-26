"""Escalação: assentar os inscritos nos slots da comp de um evento.

Diferente de `event_signups` (auto-inscrição por função, sem build) e de
`EventParticipant` (presença/payout), aqui o admin move cada jogador pra um
`CompSlot` e escolhe qual `GameRole` (flex) ele vai jogar. O bypass é por design:
não há checagem de que as funções escolhidas pelo inscrito batem com o slot —
o admin decide. Mudanças setam `Event.signup_message_dirty` (mesmo outbox do
mass-info do bot-v2)."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.permissions import has_permission
from app.domain.states import EventState
from app.models.audit import AuditLog
from app.models.catalog import GameRole, Weapon
from app.models.comps import Comp, CompParty, CompSlot
from app.models.events import Event, EventAssignment
from app.models.registration import BotRegistration
from app.models.tenancy import GuildMember
from app.services import event_gates
from app.services.events import ServiceError


def _load_comp_tree(db: Session, guild_id: int, comp_id: int | None) -> Comp | None:
    if comp_id is None:
        return None
    return db.scalar(
        select(Comp).where(Comp.id == comp_id, Comp.guild_id == guild_id)
        .options(selectinload(Comp.parties).selectinload(CompParty.slots).selectinload(CompSlot.roles))
    )


def _load_roles_and_weapons(
    db: Session, comp: Comp | None,
) -> tuple[dict[int, GameRole], dict[int, Weapon]]:
    if comp is None:
        return {}, {}
    role_ids = {
        csr.game_role_id
        for party in comp.parties for slot in party.slots for csr in slot.roles
    }
    roles: dict[int, GameRole] = {}
    if role_ids:
        roles = {r.id: r for r in db.scalars(select(GameRole).where(GameRole.id.in_(role_ids)))}
    weapon_ids = {r.weapon_id for r in roles.values() if r.weapon_id}
    weapons: dict[int, Weapon] = {}
    if weapon_ids:
        weapons = {w.id: w for w in db.scalars(select(Weapon).where(Weapon.id.in_(weapon_ids)))}
    return roles, weapons


def _parse_gear_spells(raw: str | None) -> dict:
    # gear_spells é Text JSON: {"helmet_Q": "SPELL_ID", ...}. Vira dict no payload.
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): (v if v is None else str(v)) for k, v in parsed.items()}


def _role_out(role: GameRole, weapons: dict[int, Weapon]) -> dict:
    w = weapons.get(role.weapon_id) if role.weapon_id else None
    return {
        "id": role.id,
        "name": role.name,
        "weapon_id": role.weapon_id,
        "invisible_function": w.invisible_function if w else None,
        "weapon_name": w.name if w else None,
        "offhand": role.offhand,
        "helmet": role.helmet,
        "armor": role.armor,
        "boots": role.boots,
        "cape": role.cape,
        "food": role.food,
        "play_style": role.play_style,
        "obs": role.obs,
        "build_items": role.build_items or [],
        "color": role.color,
        "q_spell": role.q_spell,
        "w_spell": role.w_spell,
        "passive_spell": role.passive_spell,
        "gear_spells": _parse_gear_spells(role.gear_spells),
    }


def _get_event(db: Session, guild_id: int, event_id: int) -> Event:
    ev = db.scalar(
        select(Event).where(Event.id == event_id, Event.guild_id == guild_id)
        .options(selectinload(Event.signups), selectinload(Event.assignments))
    )
    if ev is None:
        raise ServiceError("evento não encontrado")
    return ev


def build_public_escalation(db: Session, token: str) -> dict:
    ev = db.scalar(select(Event).where(Event.escalation_token == token))
    if ev is None:
        raise ServiceError("evento não encontrado")
    return build_escalation(db, ev.guild_id, ev.id, None)


def build_escalation(
    db: Session, guild_id: int, event_id: int, member: GuildMember | None,
) -> dict:
    ev = _get_event(db, guild_id, event_id)
    comp = _load_comp_tree(db, guild_id, ev.comp_id)
    roles, weapons = _load_roles_and_weapons(db, comp)

    parties: list[dict] = []
    valid_slot_ids: set[int] = set()
    if comp is not None:
        for party in comp.parties:
            slots = []
            for slot in party.slots:
                valid_slot_ids.add(slot.id)
                slots.append({
                    "id": slot.id,
                    "position": slot.position,
                    "label": slot.label,
                    "fn": slot.fn,
                    "notes": slot.notes,
                    "roles": [
                        _role_out(roles[csr.game_role_id], weapons)
                        for csr in slot.roles
                        if csr.game_role_id in roles
                    ],
                })
            parties.append({
                "id": party.id,
                "position": party.position,
                "name": party.name,
                "slots": slots,
            })

    # Órfãos (comp_slot_id None ou fora da árvore atual) são escondidos, não
    # deletados — a comp pode ser reeditada e o ciclo de vida é do ondelete=SET NULL.
    # Resolve nomes de personagem do /register (BotRegistration) — fallback é o
    # user_name que veio do Discord (display_name do servidor) no momento do signup.
    signup_user_ids = {s.user_id for s in ev.signups}
    assignment_user_ids = {a.user_id for a in ev.assignments}
    all_user_ids = signup_user_ids | assignment_user_ids
    char_names: dict[int, str] = {}
    if all_user_ids:
        regs = db.scalars(
            select(BotRegistration).where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.discord_user_id.in_(all_user_ids),
                BotRegistration.active.is_(True),
            )
        ).all()
        # Se o mesmo user tem mais de um registro ativo (multi-char), pega o
        # primeiro — não há como saber qual personagem ele vai jogar só pelo ID.
        for r in regs:
            char_names.setdefault(r.discord_user_id, r.albion_player_name)

    def _display_name(uid: int, fallback: str | None) -> str | None:
        return char_names.get(uid) or fallback

    assignments = [
        {
            "slot_id": a.comp_slot_id,
            "user_id": a.user_id,
            "user_name": _display_name(a.user_id, a.user_name),
            "game_role_id": a.game_role_id,
            "locked": a.locked,
        }
        for a in ev.assignments
        if a.comp_slot_id is not None and a.comp_slot_id in valid_slot_ids
    ]

    enlisted = [
        {
            "user_id": s.user_id,
            "user_name": _display_name(s.user_id, s.user_name),
            "functions": list(s.functions or []),
            # Identidade do signup: pares (weapon_id, fn) + chaves prontas pra
            # casar com os pares de cada slot na UI de escalação.
            "weapon_fns": [
                {"weapon_id": int(e["weapon_id"]), "fn": e.get("fn")}
                for e in (s.weapon_fns or [])
                if isinstance(e, dict) and e.get("weapon_id") is not None
            ],
        }
        for s in ev.signups
    ]
    # Enriquece weapon_fns com item_id + weapon_name para a UI renderizar os
    # ícones de arma sem precisar do weapon_id -> item_id das roles (que pode
    # ser None em roles legadas).
    signup_wids = {p["weapon_id"] for e in enlisted for p in e["weapon_fns"]}
    signup_weapons: dict[int, Weapon] = {}
    if signup_wids:
        signup_weapons = {w.id: w for w in db.scalars(select(Weapon).where(Weapon.id.in_(signup_wids)))}
    for entry in enlisted:
        for p in entry["weapon_fns"]:
            w = signup_weapons.get(p["weapon_id"])
            if w:
                p["item_id"] = w.item_id
                p["weapon_name"] = w.name
    for entry in enlisted:
        entry["keys"] = [
            event_gates.pair_key(p["weapon_id"], p["fn"]) for p in entry["weapon_fns"]
        ]

    can_manage = bool(member is not None and (
        member.is_guild_admin or has_permission(db, member, "escalacao.manage")
    ))

    comp_name = comp.name if comp is not None else None
    return {
        "event": {
            "id": ev.id,
            "guild_id": str(ev.guild_id),
            "title": ev.title,
            "scheduled_at": ev.scheduled_at,
            "state": ev.state.value,
            "comp_id": ev.comp_id,
            "comp_name": comp_name,
            "functions_released": ev.functions_released,
            "assignment_mode": ev.assignment_mode,
            "autofill_mode": ev.autofill_mode,
        },
        "parties": parties,
        "assignments": assignments,
        "enlisted": enlisted,
        "can_manage": can_manage,
    }


def assign(
    db: Session, guild_id: int, event_id: int,
    slot_id: int, user_id: int, user_name: str | None, game_role_id: int,
    actor_id: int | None = None,
    *, locked: bool = True, actor_source: str = "site", autofill_run_id: str | None = None,
) -> EventAssignment:
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        raise ServiceError("evento não encontrado")
    if ev.comp_id is None:
        raise ServiceError("evento sem comp vinculado")
    comp = _load_comp_tree(db, guild_id, ev.comp_id)
    if comp is None:
        raise ServiceError("comp não encontrada")

    slot = next(
        (s for p in comp.parties for s in p.slots if s.id == slot_id), None,
    )
    if slot is None:
        raise ServiceError("slot inválido")
    flex_ids = {csr.game_role_id for csr in slot.roles}
    if game_role_id not in flex_ids:
        raise ServiceError("game_role não é flex desse slot")
    role = db.get(GameRole, game_role_id)
    if role is None or role.guild_id != guild_id:
        raise ServiceError("game_role não pertence à guilda do evento")

    # Bump: se o slot já tem outro jogador, ele volta pra piscina de inscritos.
    occupant = db.scalar(select(EventAssignment).where(
        EventAssignment.event_id == event_id,
        EventAssignment.comp_slot_id == slot_id,
    ))
    bumped = None
    if occupant is not None and occupant.user_id != user_id:
        bumped = {"user_id": occupant.user_id, "slot_id": slot_id}
        db.delete(occupant)
        db.flush()

    # Upsert pelo user (1 assento por jogador): move o jogador pra o slot novo,
    # liberando o slot antigo automaticamente.
    row = db.scalar(select(EventAssignment).where(
        EventAssignment.event_id == event_id, EventAssignment.user_id == user_id,
    ))
    prev_slot = row.comp_slot_id if row is not None else None
    if row is None:
        row = EventAssignment(event_id=event_id, guild_id=guild_id, user_id=user_id)
        db.add(row)
    row.comp_slot_id = slot_id
    row.user_name = user_name
    row.locked = locked
    row.autofill_run_id = autofill_run_id
    row.game_role_id = game_role_id

    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type=actor_source, source=actor_source,
        action="escalacao.assign", entity="event_assignment", entity_id=str(row.id),
        event_id=event_id,
        before={"prev_slot": prev_slot, "bumped": bumped},
        after={"slot_id": slot_id, "user_id": user_id, "game_role_id": game_role_id,
               "locked": locked, "autofill_run_id": autofill_run_id},
    ))
    ev.signup_message_dirty = True
    db.flush()
    return row


def _slot_pairs(slot: CompSlot, roles: dict[int, GameRole]) -> set[tuple[int, str]]:
    """Pares (weapon_id, fn_key) que este slot aceita."""
    pairs: set[tuple[int, str]] = set()
    for csr in slot.roles:
        role = roles.get(csr.game_role_id)
        if role is not None and role.weapon_id is not None:
            pairs.add((role.weapon_id, event_gates.fn_key(slot.fn)))
    return pairs


def _signup_pairs(signup, role_pairs: dict[int, set[tuple[int, str]]], roles: dict[int, GameRole]) -> set[tuple[int, str]]:
    """Pares desejados por um inscrito. `weapon_fns` é a fonte; eventos
    legados (weapon_fns vazio) derivam dos nomes de GameRole contra as roles
    desta comp."""
    wanted: set[tuple[int, str]] = set()
    for entry in (signup.weapon_fns or []):
        if isinstance(entry, dict) and entry.get("weapon_id") is not None:
            try:
                wanted.add((int(entry["weapon_id"]), event_gates.fn_key(entry.get("fn"))))
            except (TypeError, ValueError):
                break
    if not wanted:
        names = {event_gates.function_key(n) for n in (signup.functions or [])}
        for role_id, pairs in role_pairs.items():
            role = roles.get(role_id)
            if role is not None and event_gates.function_key(role.name) in names:
                wanted |= pairs
    return wanted


def _autofill_plan(db: Session, guild_id: int, ev: Event) -> list[dict]:
    if ev.comp_id is None:
        return []
    comp = _load_comp_tree(db, guild_id, ev.comp_id)
    if comp is None:
        return []
    role_ids = {
        csr.game_role_id for party in comp.parties
        for slot in party.slots for csr in slot.roles
    }
    roles = {
        r.id: r for r in db.scalars(select(GameRole).where(GameRole.id.in_(role_ids)))
    } if role_ids else {}
    # role -> pares (weapon, fn) nos slots onde aparece — alimenta o fallback
    # legado (nome -> par) do _signup_pairs.
    role_pairs: dict[int, set[tuple[int, str]]] = {}
    for party in comp.parties:
        for slot in party.slots:
            for csr in slot.roles:
                role = roles.get(csr.game_role_id)
                if role is not None and role.weapon_id is not None:
                    role_pairs.setdefault(csr.game_role_id, set()).add(
                        (role.weapon_id, event_gates.fn_key(slot.fn))
                    )
    occupied = {
        a.comp_slot_id for a in ev.assignments
        if a.comp_slot_id is not None
    }
    assigned_users = {a.user_id for a in ev.assignments}
    plan: list[dict] = []
    for signup in ev.signups:
        if signup.user_id in assigned_users:
            continue
        wanted = _signup_pairs(signup, role_pairs, roles)
        if not wanted:
            continue
        match = None
        for party in comp.parties:
            for slot in party.slots:
                if slot.id in occupied:
                    continue
                # Slot compatível = alguma flex do slot forma um par desejado.
                # A atribuição persiste a GameRole CONCRETA (a primeira flex
                # compatível, na ordem do slot) — vários roles podem
                # compartilhar o mesmo par.
                flex = next((
                    csr for csr in slot.roles
                    if roles.get(csr.game_role_id) is not None
                    and roles[csr.game_role_id].weapon_id is not None
                    and (roles[csr.game_role_id].weapon_id, event_gates.fn_key(slot.fn)) in wanted
                ), None)
                if flex is not None:
                    match = (slot, flex)
                    break
            if match is not None:
                break
        if match is None:
            continue
        slot, flex = match
        plan.append({
            "slot_id": slot.id, "user_id": signup.user_id,
            "user_name": signup.user_name, "game_role_id": flex.game_role_id,
            "game_role_name": roles[flex.game_role_id].name,
        })
        occupied.add(slot.id)
        assigned_users.add(signup.user_id)
    return plan


def autofill_signup(
    db: Session, guild_id: int, event_id: int, user_id: int,
    user_name: str | None, *, force: bool = False,
) -> EventAssignment | None:
    """Fills one free slot for a signup, never replacing a locked assignment."""
    ev = _get_event(db, guild_id, event_id)
    if (not force and ev.autofill_mode != "on_signup") or ev.autofill_mode == "off":
        return None
    if ev.assignment_mode == "admin_assign":
        return None
    existing = db.scalar(select(EventAssignment).where(
        EventAssignment.event_id == event_id, EventAssignment.user_id == user_id,
    ))
    if existing is not None:
        return None
    # Include the just-updated signup in the same deterministic planner.
    plan = _autofill_plan(db, guild_id, ev)
    candidate = next((p for p in plan if p["user_id"] == user_id), None)
    if candidate is None:
        return None
    run_id = str(uuid.uuid4())
    return assign(
        db, guild_id, event_id, candidate["slot_id"], user_id, user_name,
        candidate["game_role_id"], actor_id=user_id, locked=False, actor_source="bot",
        autofill_run_id=run_id,
    )


def preview_autofill(db: Session, guild_id: int, event_id: int) -> list[dict]:
    ev = _get_event(db, guild_id, event_id)
    return _autofill_plan(db, guild_id, ev)


def autofill_event(db: Session, guild_id: int, event_id: int, actor_id: int | None = None) -> dict:
    """Fills free slots from signups; administrative rows remain locked."""
    ev = _get_event(db, guild_id, event_id)
    run_id = str(uuid.uuid4())
    assigned = 0
    for candidate in _autofill_plan(db, guild_id, ev):
        assign(
            db, guild_id, event_id, candidate["slot_id"], candidate["user_id"],
            candidate["user_name"], candidate["game_role_id"], actor_id=actor_id,
            locked=False, actor_source="site", autofill_run_id=run_id,
        )
        assigned += 1
    return {"assigned": assigned, "run_id": run_id if assigned else None}


def undo_autofill(
    db: Session, guild_id: int, event_id: int, run_id: str, actor_id: int | None,
) -> int:
    ev = _get_event(db, guild_id, event_id)
    if ev.state in (EventState.REVIEW, EventState.FINALIZED, EventState.CANCELLED, EventState.DELETED):
        raise ServiceError("autofill só pode ser desfeito antes do callout")
    rows = list(db.scalars(select(EventAssignment).where(
        EventAssignment.event_id == event_id,
        EventAssignment.autofill_run_id == run_id,
        EventAssignment.locked.is_(False),
    )))
    for row in rows:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
            action="escalacao.autofill_undo", entity="event_assignment", entity_id=str(row.id),
            event_id=event_id, before={"user_id": row.user_id, "slot_id": row.comp_slot_id},
        ))
        db.delete(row)
    ev.signup_message_dirty = True
    db.flush()
    return len(rows)


def unassign_slot(db: Session, guild_id: int, event_id: int, slot_id: int, actor_id: int | None = None) -> None:
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(select(EventAssignment).where(
        EventAssignment.event_id == event_id, EventAssignment.comp_slot_id == slot_id,
    ))
    if row is not None:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
            action="escalacao.unassign_slot", entity="event_assignment", entity_id=str(row.id),
            event_id=event_id, before={"user_id": row.user_id, "slot_id": slot_id},
        ))
        db.delete(row)
        ev.signup_message_dirty = True
        db.flush()


def unassign_user(db: Session, guild_id: int, event_id: int, user_id: int, actor_id: int | None = None) -> None:
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(select(EventAssignment).where(
        EventAssignment.event_id == event_id, EventAssignment.user_id == user_id,
    ))
    if row is not None:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
            action="escalacao.unassign_user", entity="event_assignment", entity_id=str(row.id),
            event_id=event_id, before={"user_id": user_id, "slot_id": row.comp_slot_id},
        ))
        db.delete(row)
        ev.signup_message_dirty = True
        db.flush()
