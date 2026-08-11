"""Resolve um Discord guild → conjunto de Albion guild IDs que ele possui.

A guilda PRIMÁRIA vive em `Guild.albion_guild_id` (backward compat); as
secundárias em `guild_albion_links`. Queries que filtram batalhas/players pela
guilda configurada devem usar a lista completa (primary + links) — senão
membros de uma guilda secundária seriam tratados como "não da guilda"."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.tenancy import Guild, GuildAlbionLink


def _merge(primary_id: str | None, link_ids) -> list[str]:
    out: list[str] = []
    if primary_id:
        out.append(str(primary_id))
    for gid in link_ids:
        if gid not in out:
            out.append(gid)
    return out


def albion_guild_ids(db: Session, guild_id: int) -> list[str]:
    g = db.get(Guild, guild_id)
    if g is None:
        return []
    link_ids = db.scalars(
        select(GuildAlbionLink.albion_guild_id).where(GuildAlbionLink.guild_id == guild_id)
    ).all()
    return _merge(g.albion_guild_id, link_ids)


async def async_albion_guild_ids(db: AsyncSession, guild_id: int) -> list[str]:
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        return []
    link_ids = (await db.scalars(
        select(GuildAlbionLink.albion_guild_id).where(GuildAlbionLink.guild_id == guild_id)
    )).all()
    return _merge(g.albion_guild_id, link_ids)
