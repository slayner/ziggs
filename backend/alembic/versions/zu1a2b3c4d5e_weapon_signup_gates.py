"""migrate signup gates from role names to weapons

Revision ID: zu1a2b3c4d5e
Revises: zt2a3b4c5d6e
"""
from alembic import op
import sqlalchemy as sa


revision = "zu1a2b3c4d5e"
down_revision = "zt2a3b4c5d6e"
branch_labels = None
depends_on = None


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def upgrade() -> None:
    bind = op.get_bind()
    guilds = sa.table("guilds", sa.column("id"), sa.column("settings", sa.JSON()))
    roles = sa.table("game_roles", sa.column("guild_id"), sa.column("name"), sa.column("weapon_id"))
    # `alembic upgrade --sql` (offline) entrega um bind mock cujo execute()
    # retorna None — nada a migrar, só o esquema seria emitido (não há DDL aqui).
    guild_rows = bind.execute(sa.select(guilds.c.id, guilds.c.settings))
    if guild_rows is None:
        return
    for guild_id, settings in guild_rows:
        legacy = (settings or {}).get("event_role_gates")
        if not legacy:
            continue
        weapon_gates = dict((settings or {}).get("event_weapon_gates") or {})
        by_name: dict[str, set[int]] = {}
        for name, weapon_id in bind.execute(sa.select(roles.c.name, roles.c.weapon_id).where(roles.c.guild_id == guild_id)):
            if weapon_id is not None:
                by_name.setdefault(_key(name), set()).add(weapon_id)
        for name, discord_roles in legacy.items():
            weapons = by_name.get(_key(name), set())
            if not weapons:
                raise RuntimeError(f"cannot migrate signup gate {name!r} in guild {guild_id}: no weapon")
            for weapon_id in weapons:
                key = str(weapon_id)
                weapon_gates[key] = list(dict.fromkeys([*(weapon_gates.get(key) or []), *discord_roles]))
        updated = dict(settings or {})
        updated["event_weapon_gates"] = weapon_gates
        updated.pop("event_role_gates", None)
        bind.execute(guilds.update().where(guilds.c.id == guild_id).values(settings=updated))


def downgrade() -> None:
    # Names are intentionally not reconstructed: several roles can share one weapon.
    pass
