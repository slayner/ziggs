"""Saldo de prata por membro (EconomyBalance) — get-or-create compartilhado
entre as rotas do bot (/balance /pay /addmoney /removemoney, ver auth.py) e o
débito de déficit de guild_backed em events.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.tenancy import Guild, GuildMember

FORFEIT_GRACE_DAYS = 7


def get_or_create_balance(db: Session, guild_id: int, discord_user_id: int) -> EconomyBalance:
    bal = db.scalar(select(EconomyBalance).where(
        EconomyBalance.guild_id == guild_id, EconomyBalance.discord_user_id == discord_user_id,
    ))
    if bal is None:
        bal = EconomyBalance(guild_id=guild_id, discord_user_id=discord_user_id)
        db.add(bal)
        db.flush()
    return bal


def set_member_left(db: Session, guild_id: int, user_id: int) -> None:
    """Marca que o membro saiu do Discord — chamado pelo bot em on_member_remove."""
    m = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user_id,
    ))
    if m is not None:
        m.left_at = datetime.now(timezone.utc)
        db.flush()


def clear_member_left(db: Session, guild_id: int, user_id: int) -> None:
    """Limpa left_at — chamado pelo bot em on_member_join (membro voltou)."""
    m = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user_id,
    ))
    if m is not None and m.left_at is not None:
        m.left_at = None
        db.flush()


def forfeit_due(db: Session, guild_id: int) -> list[dict]:
    """Confisca saldos de membros que saíram há mais de FORFEIT_GRACE_DAYS dias.
    Move balance → guild.bank_balance, cria EconomyTransaction, limpa left_at.
    Devolve lista de {user_id, amount} pro bot logar."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=FORFEIT_GRACE_DAYS)
    members = db.scalars(select(GuildMember).where(
        GuildMember.guild_id == guild_id,
        GuildMember.left_at.is_not(None),
        GuildMember.left_at <= cutoff,
    )).all()
    if not members:
        return []
    guild = db.get(Guild, guild_id)
    out: list[dict] = []
    for m in members:
        bal = get_or_create_balance(db, guild_id, m.user_id)
        amount = bal.balance
        if amount > 0:
            bal.balance = 0
            if guild is not None:
                guild.bank_balance += amount
            db.add(EconomyTransaction(
                guild_id=guild_id, kind="forfeit",
                actor_discord_id=0,
                from_user_id=m.user_id, to_user_id=None, total_earned_user_id=None,
                amount=amount,
            ))
            out.append({"user_id": m.user_id, "amount": amount})
        m.left_at = None
    db.flush()
    return out
