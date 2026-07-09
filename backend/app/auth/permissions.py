"""
Permissões efetivas de um membro na guilda.

Admin de servidor (is_guild_admin) tem tudo. Senão, soma as permissões dos
cargos Discord dele via GuildRolePermission (configurado em /auth/guild-discord-roles).
Único lugar que calcula isso — usado tanto por /auth/my-permissions (o que a UI
mostra) quanto por deps.require_permission (o que a API de fato aplica).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import GuildMember, GuildRolePermission

PERMISSION_KEYS = [
    "events.view", "events.create", "events.manage",
    "comps.view",  "comps.create",  "comps.manage",
    "nodes.view",  "nodes.manage",
    "guild.admin",
    "escalacao.manage",
]
ALL_TRUE  = {k: True  for k in PERMISSION_KEYS}
ALL_FALSE = {k: False for k in PERMISSION_KEYS}


def compute_permissions(db: Session, member: GuildMember) -> dict[str, bool]:
    if member.is_guild_admin:
        return dict(ALL_TRUE)

    role_ids = {int(r) for r in (member.discord_role_ids or [])}
    if not role_ids:
        return dict(ALL_FALSE)

    role_perms = db.scalars(select(GuildRolePermission).where(
        GuildRolePermission.guild_id == member.guild_id,
        GuildRolePermission.discord_role_id.in_(role_ids),
    )).all()

    effective = dict(ALL_FALSE)
    for rp in role_perms:
        for k, v in rp.permissions.items():
            if v and k in effective:
                effective[k] = True
    return effective


def has_permission(db: Session, member: GuildMember, key: str) -> bool:
    return compute_permissions(db, member)[key]
