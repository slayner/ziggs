"""Saldo de prata por membro Discord (comandos /balance /pay /addmoney
/removemoney /leaderboard /economystats do bot) — porta simplificada do
sistema de economia do bot legado (bot/cogs/economy.py), sem banco da guild
(não pedido) nem os outros rankings (attendance/mvp/kills/deaths, dependem
de dados de CTA que não existem aqui)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Snowflake, json_type, pk


class EconomyBalance(Base):
    __tablename__ = "economy_balances"
    __table_args__ = (UniqueConstraint("guild_id", "discord_user_id", name="uq_economy_balance_member"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    balance: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Só cresce em créditos (add_money/pay recebido) — nunca abatido por
    # remove_money, igual ao bot legado (mede "quanto essa pessoa já ganhou",
    # não o saldo atual).
    total_earned: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)


class EconomyTransaction(Base):
    """Registro de cada mudança de saldo (/pay /addmoney /removemoney) — o `id`
    é o número mostrado no rodapé dos embeds do bot e usado pelo /undo.

    Modelo genérico o bastante pra cobrir os 3 tipos com a MESMA lógica de
    reversão (ver bot_economy_undo em app/api/routes/auth.py), sem precisar de
    um branch por `kind`:
    - pay:    from_user_id=quem pagou, to_user_id=quem recebeu, total_earned_user_id=to_user_id
    - add:    from_user_id=None,       to_user_id=alvo,         total_earned_user_id=to_user_id
    - remove: from_user_id=alvo,       to_user_id=None,         total_earned_user_id=None
    `from_user_id` perde `amount` de saldo, `to_user_id` ganha `amount` de saldo
    — desfazer é só inverter os dois (e o total_earned, se houver).

    `event_id` é preenchido apenas em transações de evento (event_payout,
    event_deficit) — liga a transação ao Event que a gerou, permitindo o
    portal do membro mostrar o título do evento e um link clicável."""
    __tablename__ = "economy_transactions"

    id: Mapped[int] = pk()
    request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    undo_request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_discord_id: Mapped[int] = mapped_column(Snowflake, nullable=False)
    from_user_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    to_user_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    total_earned_user_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    amount: Mapped[int] = mapped_column(BigInt(), nullable=False)
    undone: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Só preenchido em transações de evento (event_payout, event_deficit).
    # SET NULL on delete — a transação sobrevive mesmo se o evento for apagado.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payout_context: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
