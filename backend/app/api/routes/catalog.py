"""Catálogo: armas (global) + funções da guilda (game roles) + sugestões de build + preços."""
from __future__ import annotations

import json as _json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas.catalog import (
    BuildSuggestionOut, FieldSuggestion, GameRoleCreate, GameRoleDetail,
    GameRoleOut, GameRoleUpdate, SuggestRequest, WeaponOut, WeaponSpellOut,
)
from app.domain.suggestions import RoleBuild, suggest_build
from app.models.catalog import GameRole, Weapon, WeaponSpell
from app.models.prices import ItemPriceLatest
from app.models.tenancy import Guild
from app.services.prices import _AVG_SENTINEL, sync_5city_prices

router = APIRouter(prefix="/guilds/{guild_id}/catalog", tags=["catalog"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _detail(role: GameRole, weapon: Weapon | None) -> GameRoleDetail:
    from app.api.schemas.catalog import RegearItem
    return GameRoleDetail(
        id=role.id, name=role.name,
        weapon_id=role.weapon_id,
        weapon_name=weapon.name if weapon else None,
        invisible_function=weapon.invisible_function if weapon else None,
        offhand=role.offhand, helmet=role.helmet, armor=role.armor,
        boots=role.boots, cape=role.cape, food=role.food,
        abilities=role.abilities, play_style=role.play_style, obs=role.obs,
        build_items=[RegearItem(**bi) for bi in (role.build_items or [])],
        color=role.color,
        q_spell=role.q_spell,
        w_spell=role.w_spell,
        passive_spell=role.passive_spell,
        gear_spells=_json.loads(role.gear_spells) if role.gear_spells else None,
    )


async def _get_weapon(db: AsyncSession, weapon_id: int | None) -> Weapon | None:
    return await db.get(Weapon, weapon_id) if weapon_id else None


# ── armas (catálogo global) ───────────────────────────────────────────────────

@router.get("/weapons", response_model=list[WeaponOut])
async def list_weapons(
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    rows = (await db.scalars(select(Weapon).order_by(Weapon.name))).all()
    return [
        WeaponOut(
            id=r.id, item_id=r.item_id, name=r.name,
            invisible_function=r.invisible_function, category=r.category,
        )
        for r in rows
    ]


# ── funções da guilda (game roles) ───────────────────────────────────────────

@router.get("/roles", response_model=list[GameRoleOut])
async def list_roles(
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    rows = (await db.execute(
        select(GameRole.id, GameRole.name, Weapon.invisible_function)
        .join(Weapon, GameRole.weapon_id == Weapon.id, isouter=True)
        .where(GameRole.guild_id == guild.id)
        .order_by(GameRole.name)
    )).all()
    return [
        GameRoleOut(id=r.id, name=r.name, invisible_function=r.invisible_function)
        for r in rows
    ]


@router.get("/roles/{role_id}", response_model=GameRoleDetail)
async def get_role(
    role_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    role = await db.get(GameRole, role_id)
    if role is None or role.guild_id != guild.id:
        raise HTTPException(status_code=404, detail="função não encontrada")
    return _detail(role, await _get_weapon(db, role.weapon_id))


@router.get("/weapons/{base_id}/spells", response_model=list[WeaponSpellOut])
async def get_weapon_spells(
    base_id: str,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    """Retorna os feitiços Q/W/passivo disponíveis para um tipo base de arma."""
    rows = (await db.scalars(
        select(WeaponSpell)
        .where(
            WeaponSpell.weapon_base_id == base_id.upper(),
            ~or_(
                WeaponSpell.spell_id.ilike("%maxload%"),
                WeaponSpell.name.ilike("%max load%"),
                WeaponSpell.spell_id == "PASSIVE_PLATEARMOR_HEALTH_REDUCTION",
                WeaponSpell.spell_id == "PASSIVE_PLATEARMOR_THREATGENERATION",
            ),
        )
        .order_by(WeaponSpell.slot, WeaponSpell.order_idx)
    )).all()
    return rows


@router.post("/roles", response_model=GameRoleDetail, status_code=201)
async def create_role(
    payload: GameRoleCreate,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.manage")),
):
    role = GameRole(
        guild_id=guild.id,
        name=payload.name,
        weapon_id=payload.weapon_id,
        offhand=payload.offhand, helmet=payload.helmet, armor=payload.armor,
        boots=payload.boots, cape=payload.cape, food=payload.food,
        abilities=payload.abilities, play_style=payload.play_style, obs=payload.obs,
        build_items=[bi.model_dump() for bi in payload.build_items],
        color=payload.color,
        q_spell=payload.q_spell, w_spell=payload.w_spell, passive_spell=payload.passive_spell,
        gear_spells=_json.dumps(payload.gear_spells) if payload.gear_spells else None,
    )
    db.add(role)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Já existe uma função com o nome '{payload.name}'")
    return _detail(role, await _get_weapon(db, role.weapon_id))


@router.patch("/roles/{role_id}", response_model=GameRoleDetail)
async def update_role(
    role_id: int,
    payload: GameRoleUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.manage")),
):
    role = await db.get(GameRole, role_id)
    if role is None or role.guild_id != guild.id:
        raise HTTPException(status_code=404, detail="função não encontrada")

    # Detecta mudança de build — só os slots que exigem spec (arma, offhand,
    # capacete, armadura, bota). Capa/food/pot/skill/spell são ajustes que não
    # mudam o que o jogador "faz" — a preferência persistente continua válida.
    build_changed = False
    build_fields = ("weapon_id", "offhand", "helmet", "armor", "boots")
    for field in build_fields:
        val = getattr(payload, field)
        if val is not None and val != getattr(role, field):
            build_changed = True
            setattr(role, field, val)

    # Campos de ajuste (não invalidam preferências) — mas ainda são salvos.
    for field in ("cape", "food", "abilities", "q_spell", "w_spell",
                  "passive_spell", "play_style", "obs", "color", "name"):
        val = getattr(payload, field)
        if val is not None:
            setattr(role, field, val)
    if payload.build_items is not None:
        role.build_items = [bi.model_dump() for bi in payload.build_items]
    if payload.gear_spells is not None:
        role.gear_spells = _json.dumps(payload.gear_spells)

    if build_changed:
        from sqlalchemy import delete as sa_delete
        from app.models.comp_preferences import CompRolePreference
        await db.execute(sa_delete(CompRolePreference).where(
            CompRolePreference.game_role_id == role_id,
        ))

    await db.flush()
    await db.commit()
    return _detail(role, await _get_weapon(db, role.weapon_id))


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.manage")),
):
    role = await db.get(GameRole, role_id)
    if role is None or role.guild_id != guild.id:
        raise HTTPException(status_code=404, detail="função não encontrada")
    await db.delete(role)
    await db.commit()


# ── preços (média histórica 5 cidades) ───────────────────────────────────────

@router.get("/prices")
async def get_prices(
    items: str = Query(description="IDs separados por vírgula"),
    quality: int = 1,  # kept for compat but ignored
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
) -> dict:
    """Retorna a média histórica 5 cidades × qualidades 2-4 para uma lista de itens.

    Qualities 1 e 5 são descartadas da média: 1 (Normal) tem preço inflacionado
    por ser o padrão de craft, 5 (Masterpiece) é raríssima e tem preço atípico.
    Ambas interferem na média. O registro continua sendo capturado e armazenado
    (todas as qualities), só não entra no cálculo do preço médio."""
    item_ids = [i.strip() for i in items.split(",") if i.strip()]
    if not item_ids:
        return {"prices": {}}
    await sync_5city_prices(db, item_ids)
    rows = (await db.scalars(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id.in_(item_ids),
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality.in_([2, 3, 4]),
        )
    )).all()
    # Average across available qualities per item
    by_item: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_item[r.item_id].append(r.sell_price_min)
    return {"prices": {iid: int(sum(vals) / len(vals)) for iid, vals in by_item.items()}}


# ── preços por cidade (captura companion × ADP — mais fresco vence) ──────────

@router.get("/price-quotes")
async def get_price_quotes(
    items: str = Query(description="IDs separados por vírgula"),
    db: AsyncSession = Depends(deps.async_db_session),
) -> dict:
    """Devolve preços por (item_id, city, quality) do nosso banco — captura
    do companion + sync AODP. Preços são globais — sem auth.

    Aceita tanto UniqueName (T4_CLOTH_LEVEL2) quanto game_name ("Rare Fine
    Cloth") — converte tudo pra game_name (formato do DB) antes de buscar."""
    from app.services.prices import _unique_to_game
    raw_ids = [i.strip() for i in items.split(",") if i.strip()]
    if not raw_ids:
        return {"prices": []}
    item_ids = list(dict.fromkeys(_unique_to_game(i) for i in raw_ids))
    out: list[dict] = []
    for i in range(0, len(item_ids), 500):
        chunk = item_ids[i : i + 500]
        for row in (await db.scalars(
            select(ItemPriceLatest).where(ItemPriceLatest.item_id.in_(chunk))
        )).all():
            out.append({
                "item_id": row.item_id,
                "city": row.city,
                "quality": row.quality,
                "sell_price_min": row.sell_price_min,
                "price_date": row.price_date.isoformat() if row.price_date else None,
            })
    return {"prices": out}


# ── sugestão de build (escopo guilda) ────────────────────────────────────────

@router.post("/suggest", response_model=BuildSuggestionOut)
async def suggest(
    payload: SuggestRequest,
    guild: Guild = Depends(deps.tenant_guild),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    """
    Sugere build para uma função invisível olhando TODAS as funções da guilda
    com a mesma invisible_function. Não depende de comp — útil ao criar/editar roles.
    """
    roles = (await db.scalars(
        select(GameRole).where(GameRole.guild_id == guild.id)
    )).all()

    wids = {r.weapon_id for r in roles if r.weapon_id}
    weapons: dict[int, Weapon] = {}
    if wids:
        weapons = {w.id: w for w in (await db.scalars(select(Weapon).where(Weapon.id.in_(wids))))}

    builds = [
        RoleBuild(
            invisible_function=weapons[r.weapon_id].invisible_function if r.weapon_id and r.weapon_id in weapons else None,
            offhand=r.offhand, helmet=r.helmet, armor=r.armor,
            boots=r.boots, cape=r.cape, food=r.food,
            abilities=r.abilities, play_style=r.play_style,
        )
        for r in roles
    ]

    result = suggest_build(payload.target_function, builds)
    return BuildSuggestionOut(
        target_function=result.target_function,
        sample_size=result.sample_size,
        fields={
            k: FieldSuggestion(value=v.value, votes=v.votes, total=v.total)
            for k, v in result.fields.items()
        },
    )