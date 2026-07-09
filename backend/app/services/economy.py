"""Saldo de prata por membro (EconomyBalance) — get-or-create compartilhado
entre as rotas do bot (/balance /pay /addmoney /removemoney, ver auth.py) e o
débito de déficit de guild_backed em events.py."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.economy import EconomyBalance


def get_or_create_balance(db: Session, guild_id: int, discord_user_id: int) -> EconomyBalance:
    bal = db.scalar(select(EconomyBalance).where(
        EconomyBalance.guild_id == guild_id, EconomyBalance.discord_user_id == discord_user_id,
    ))
    if bal is None:
        bal = EconomyBalance(guild_id=guild_id, discord_user_id=discord_user_id)
        db.add(bal)
        db.flush()
    return bal
