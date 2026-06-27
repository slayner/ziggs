"""Base declarativa + mixins comuns a todas as tabelas."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """created_at/updated_at automáticos (UTC, vindo do Postgres)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# IDs do Discord (guild, user, role, channel, message) são snowflakes de 64 bits.
# Usamos BigInteger e guardamos o próprio snowflake como PK quando faz sentido.
Snowflake = BigInteger


def BigInt():
    """BigInteger no Postgres, Integer no SQLite (portável para colunas de prata)."""
    return BigInteger().with_variant(Integer, "sqlite")


def json_type():
    """JSONB no Postgres (produção), JSON genérico no SQLite (dev local)."""
    return JSONB().with_variant(JSON(), "sqlite")


def pk() -> Mapped[int]:
    """
    Chave primária sintética auto-incremento, portável.

    BIGINT no Postgres (escala); INTEGER no SQLite (só INTEGER vira alias de
    rowid e auto-incrementa lá). Use nas PKs geradas pelo banco — NÃO nas PKs que
    são snowflake do Discord (essas usam Snowflake + autoincrement=False).
    """
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
