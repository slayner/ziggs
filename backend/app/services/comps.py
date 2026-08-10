"""
Serviço de composições: cria/edita/lê comps e gera sugestões de build.

Mantém as rotas finas. Toda escrita também grava no audit log. Quem chama dá o
commit. Validação de tenant: role_ids precisam pertencer à MESMA guilda da comp.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas.comps import (
    BuildSuggestionOut, CompCreate, CompRead, CompSummary, CompUpdate,
    FieldSuggestionOut, PartyIn, PartyRead, RoleRead, SlotRead,
)
from app.domain.suggestions import RoleBuild, suggest_build
from app.models.audit import AuditLog
from app.models.catalog import GameRole, Weapon
from app.models.comps import Comp, CompParty, CompSlot, CompSlotRole
from app.models.events import Event


class ServiceError(Exception):
    """Erro de regra de negócio (vira 400 na rota)."""


# --- helpers de leitura -----------------------------------------------------
def _role_read(role: GameRole, weapon: Weapon | None) -> RoleRead:
    import json as _json
    return RoleRead(
        id=role.id, name=role.name, weapon_id=role.weapon_id,
        invisible_function=weapon.invisible_function if weapon else None,
        offhand=role.offhand, helmet=role.helmet, armor=role.armor,
        boots=role.boots, cape=role.cape, food=role.food,
        abilities=role.abilities, play_style=role.play_style, obs=role.obs,
        q_spell=role.q_spell, w_spell=role.w_spell, passive_spell=role.passive_spell,
        gear_spells=_json.loads(role.gear_spells) if getattr(role, 'gear_spells', None) else None,
        build_items=role.build_items or [],
    )


def _comp_read(db: Session, comp: Comp) -> CompRead:
    # Pré-carrega as funções referenciadas + suas armas, em 1 ida ao banco.
    role_ids = {
        sr.game_role_id
        for party in comp.parties for slot in party.slots for sr in slot.roles
    }
    roles: dict[int, GameRole] = {}
    weapons: dict[int, Weapon] = {}
    if role_ids:
        for r in db.scalars(select(GameRole).where(GameRole.id.in_(role_ids))):
            roles[r.id] = r
        wids = {r.weapon_id for r in roles.values() if r.weapon_id}
        if wids:
            for w in db.scalars(select(Weapon).where(Weapon.id.in_(wids))):
                weapons[w.id] = w

    parties = [
        PartyRead(
            id=party.id, position=party.position, name=party.name,
            slots=[
                SlotRead(
                    id=slot.id, position=slot.position, label=slot.label,
                    notes=slot.notes, fn=getattr(slot, 'fn', None),
                    roles=[
                        _role_read(roles[sr.game_role_id],
                                   weapons.get(roles[sr.game_role_id].weapon_id))
                        for sr in slot.roles if sr.game_role_id in roles
                    ],
                )
                for slot in party.slots
            ],
        )
        for party in comp.parties
    ]
    return CompRead(
        id=comp.id, name=comp.name, description=comp.description,
        archived=comp.archived, parties=parties,
    )


def _load_full(db: Session, guild_id: int, comp_id: int) -> Comp | None:
    return db.scalar(
        select(Comp)
        .where(Comp.id == comp_id, Comp.guild_id == guild_id)
        .options(
            selectinload(Comp.parties)
            .selectinload(CompParty.slots)
            .selectinload(CompSlot.roles)
        )
    )


# --- escrita ----------------------------------------------------------------
def _validate_role_ids(db: Session, guild_id: int, role_ids: set[int]) -> None:
    if not role_ids:
        return
    found = set(
        db.scalars(
            select(GameRole.id).where(
                GameRole.id.in_(role_ids), GameRole.guild_id == guild_id
            )
        )
    )
    missing = role_ids - found
    if missing:
        raise ServiceError(f"funções inexistentes nesta guilda: {sorted(missing)}")


def _build_parties(comp: Comp, parties: list[PartyIn]) -> None:
    """Preenche comp.parties a partir do input (positions automáticas)."""
    for p_idx, p in enumerate(parties):
        party = CompParty(position=p_idx, name=p.name)
        for s_idx, s in enumerate(p.slots):
            slot = CompSlot(position=s_idx, label=s.label, notes=s.notes, fn=s.fn)
            for r_idx, role_id in enumerate(dict.fromkeys(s.role_ids)):  # dedupe, mantém ordem
                slot.roles.append(CompSlotRole(game_role_id=role_id, position=r_idx))
            party.slots.append(slot)
        comp.parties.append(party)


def _merge_parties(comp: Comp, parties: list[PartyIn]) -> None:
    """Diff inteligente: preserva slots que não mudaram (mesma posição +
    mesmas roles) pra não órfãar EventAssignment. Slots que mudaram de role
    são atualizados in-place; slots que sumiram são deletados (ondelete SET NULL
    nos assignments); slots novos são criados.

    Match é por (party_position, slot_position) — o frontend sempre envia na
    ordem visual, e position é o índice estável que não muda entre edits
    (o CompBuilder não reordena slots arrastando, só adiciona/remove)."""
    # Indexa parties existentes por position.
    existing_parties: dict[int, CompParty] = {p.position: p for p in comp.parties}

    for p_idx, p_in in enumerate(parties):
        party = existing_parties.get(p_idx)
        if party is None:
            # Party nova — cria do zero.
            party = CompParty(position=p_idx, name=p_in.name)
            comp.parties.append(party)
        else:
            party.name = p_in.name

        # Indexa slots existentes por position dentro da party.
        existing_slots: dict[int, CompSlot] = {s.position: s for s in party.slots}
        seen_slot_positions: set[int] = set()

        for s_idx, s_in in enumerate(p_in.slots):
            seen_slot_positions.add(s_idx)
            slot = existing_slots.get(s_idx)
            if slot is None:
                # Slot novo — cria.
                slot = CompSlot(position=s_idx, label=s_in.label, notes=s_in.notes, fn=s_in.fn)
                party.slots.append(slot)
            else:
                # Slot existe — atualiza metadados.
                slot.label = s_in.label
                slot.notes = s_in.notes
                slot.fn = s_in.fn

            # Merge roles: dedupe input mantendo ordem.
            new_role_ids = list(dict.fromkeys(s_in.role_ids))
            existing_role_ids = {sr.game_role_id for sr in slot.roles}
            # Remove roles que sumiram.
            for sr in list(slot.roles):
                if sr.game_role_id not in new_role_ids:
                    slot.roles.remove(sr)
            # Adiciona roles novas (preservando position).
            for r_idx, role_id in enumerate(new_role_ids):
                if role_id not in existing_role_ids:
                    slot.roles.append(CompSlotRole(game_role_id=role_id, position=r_idx))

        # Remove slots que sumiram dessa party.
        for slot in list(party.slots):
            if slot.position not in seen_slot_positions:
                party.slots.remove(slot)

    # Remove parties que sumiram.
    seen_party_positions = {p_idx for p_idx in range(len(parties))}
    for party in list(comp.parties):
        if party.position not in seen_party_positions:
            comp.parties.remove(party)


def create_comp(
    db: Session, guild_id: int, payload: CompCreate, actor_id: int | None
) -> CompRead:
    all_role_ids = {
        rid for p in payload.parties for s in p.slots for rid in s.role_ids
    }
    _validate_role_ids(db, guild_id, all_role_ids)

    comp = Comp(
        guild_id=guild_id, name=payload.name,
        description=payload.description, created_by=actor_id,
    )
    _build_parties(comp, payload.parties)
    db.add(comp)
    db.flush()  # garante comp.id

    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="comp.create", entity="comp", entity_id=str(comp.id),
        after={"name": comp.name},
    ))
    return _comp_read(db, comp)


def list_comps(db: Session, guild_id: int, include_archived: bool) -> list[CompSummary]:
    q = select(Comp).where(Comp.guild_id == guild_id).options(
        selectinload(Comp.parties)
    )
    if not include_archived:
        q = q.where(Comp.archived.is_(False))
    out = []
    for c in db.scalars(q.order_by(Comp.name)):
        out.append(CompSummary(
            id=c.id, name=c.name, description=c.description,
            archived=c.archived, party_count=len(c.parties),
        ))
    return out


def get_comp(db: Session, guild_id: int, comp_id: int) -> CompRead | None:
    comp = _load_full(db, guild_id, comp_id)
    return _comp_read(db, comp) if comp else None


def update_comp(
    db: Session, guild_id: int, comp_id: int, payload: CompUpdate,
    actor_id: int | None,
) -> CompRead | None:
    comp = _load_full(db, guild_id, comp_id)
    if comp is None:
        return None

    before = {"name": comp.name, "archived": comp.archived}
    if payload.name is not None:
        comp.name = payload.name
    if payload.description is not None:
        comp.description = payload.description
    if payload.archived is not None:
        comp.archived = payload.archived

    if payload.parties is not None:
        all_role_ids = {
            rid for p in payload.parties for s in p.slots for rid in s.role_ids
        }
        _validate_role_ids(db, guild_id, all_role_ids)
        _merge_parties(comp, payload.parties)
        db.flush()
        # Se a comp ficou sem nenhum slot (todas as parties vazias), deleta.
        # O ondelete=SET NULL no Event.comp_id zera a comp dos eventos que a
        # usavam; CompRolePreference é CASCADE e some junto. O frontend recebe
        # None e volta pra lista de comps.
        has_slots = any(
            any(slot.roles for slot in party.slots)
            for party in comp.parties
        )
        if not has_slots:
            db.add(AuditLog(
                guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
                action="comp.delete", entity="comp", entity_id=str(comp.id),
                before={"name": comp.name, "reason": "empty after save"},
            ))
            _cleanup_events_on_comp_delete(db, comp.id)
            db.delete(comp)
            db.flush()
            return None

    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="comp.update", entity="comp", entity_id=str(comp.id),
        before=before, after={"name": comp.name, "archived": comp.archived},
    ))
    db.flush()
    return _comp_read(db, comp)


def _cleanup_events_on_comp_delete(db: Session, comp_id: int) -> None:
    """Zera functions_released dos eventos que usavam a comp deletada.
    O ondelete=SET NULL zera comp_id no banco, mas functions_released
    precisa ser False pra o bot aceitar signup sem roles."""
    for ev in db.scalars(select(Event).where(Event.comp_id == comp_id)):
        ev.functions_released = False
        ev.signup_message_dirty = True


def delete_comp(
    db: Session, guild_id: int, comp_id: int, actor_id: int | None
) -> bool:
    comp = db.scalar(
        select(Comp).where(Comp.id == comp_id, Comp.guild_id == guild_id)
    )
    if comp is None:
        return False
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="comp.delete", entity="comp", entity_id=str(comp.id),
        before={"name": comp.name},
    ))
    _cleanup_events_on_comp_delete(db, comp_id)
    db.delete(comp)
    return True


# --- sugestão ---------------------------------------------------------------
def _roles_to_builds(roles: list[tuple[GameRole, Weapon | None]]) -> list[RoleBuild]:
    return [
        RoleBuild(
            invisible_function=w.invisible_function if w else None,
            offhand=r.offhand, helmet=r.helmet, armor=r.armor, boots=r.boots,
            cape=r.cape, food=r.food, abilities=r.abilities,
            play_style=r.play_style,
        )
        for r, w in roles
    ]


def suggest(
    db: Session, guild_id: int, comp_id: int, target_function: str, scope: str
) -> BuildSuggestionOut:
    """Sugere build para um slot da função `target_function`."""
    if scope == "guild":
        role_ids = set(db.scalars(
            select(GameRole.id).where(GameRole.guild_id == guild_id)
        ))
    else:  # 'comp'
        comp = _load_full(db, guild_id, comp_id)
        if comp is None:
            raise ServiceError("comp não encontrada")
        role_ids = {
            sr.game_role_id
            for p in comp.parties for s in p.slots for sr in s.roles
        }

    pairs: list[tuple[GameRole, Weapon | None]] = []
    if role_ids:
        roles = {r.id: r for r in db.scalars(
            select(GameRole).where(GameRole.id.in_(role_ids))
        )}
        wids = {r.weapon_id for r in roles.values() if r.weapon_id}
        weapons = {}
        if wids:
            weapons = {w.id: w for w in db.scalars(
                select(Weapon).where(Weapon.id.in_(wids))
            )}
        pairs = [(r, weapons.get(r.weapon_id)) for r in roles.values()]

    result = suggest_build(target_function, _roles_to_builds(pairs))
    return BuildSuggestionOut(
        target_function=result.target_function,
        sample_size=result.sample_size,
        fields={
            k: FieldSuggestionOut(value=v.value, votes=v.votes, total=v.total)
            for k, v in result.fields.items()
        },
    )
