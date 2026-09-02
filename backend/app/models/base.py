"""Base declarativa + mixins comuns a todas as tabelas.

PostgreSQL 16 local em dev. SQLite foi removido (ver app/db.py).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
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
Snowflake = BigInteger


def BigInt():
    """BigInteger para colunas de prata/fama (escala)."""
    return BigInteger()


def json_type():
    """JSONB no Postgres."""
    return JSONB()


def pk() -> Mapped[int]:
    """Chave primária sintética auto-incremento (BIGSERIAL)."""
    return mapped_column(BigInteger, primary_key=True)
