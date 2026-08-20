"""Normalize weapon display names and collapse battle mount skins.

Revision ID: zv2a3b4c5d6e
Revises: zu1a2b3c4d5e
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "zv2a3b4c5d6e"
down_revision = "zu1a2b3c4d5e"
branch_labels = None
depends_on = None


_TIER = re.compile(r"^(?:Beginner's?|Novice'?s?|Journeyman'?s?|Adept'?s?|Expert'?s?|Master'?s?|Grandmaster'?s?|Elder'?s?)\s+")
_BM_SKIN = re.compile(r"_(?:CRYSTAL|GOLD|SILVER|BRONZE)$")


def upgrade() -> None:
    bind = op.get_bind()
    weapons = sa.table("weapons", sa.column("id"), sa.column("item_id"), sa.column("name"))
    game_roles = sa.table("game_roles", sa.column("weapon_id"))
    spells = sa.table("weapon_spells", sa.column("weapon_base_id"))
    # `alembic upgrade --sql` (offline) entrega um bind mock cujo execute()
    # retorna None — nada a normalizar, só o esquema seria emitido (não há DDL).
    result = bind.execute(sa.select(weapons.c.id, weapons.c.item_id, weapons.c.name))
    if result is None:
        return
    rows = result.mappings()
    canonical: dict[str, int] = {}

    for row in rows:
        item_id = row["item_id"]
        name = _TIER.sub("", row["name"])
        is_battle_mount_skin = item_id.startswith("UNIQUE_MOUNT_") and _BM_SKIN.search(item_id)
        if is_battle_mount_skin:
            name = re.sub(r"^(?:Crystal|Gold|Silver|Bronze)\s+", "", name)
            family = _BM_SKIN.sub("", item_id)
            if family in canonical:
                target_id = canonical[family]
                bind.execute(game_roles.update().where(game_roles.c.weapon_id == row["id"]).values(weapon_id=target_id))
                bind.execute(spells.delete().where(spells.c.weapon_base_id == re.sub(r"^T\d+_", "", item_id)))
                bind.execute(weapons.delete().where(weapons.c.id == row["id"]))
                continue
            canonical[family] = row["id"]
        bind.execute(weapons.update().where(weapons.c.id == row["id"]).values(name=name))


def downgrade() -> None:
    pass
