"""Carrinhos de craft compartilháveis via link (Feature 3).

Carrinho = lista de items que um usuário montou na calculadora e quer
compartilhar. O POST devolve um código curto; o GET recupera pelo código.
Sem auth — qualquer um pode criar/ler. O código é URL-safe e aleatório.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_type, pk


class CraftCart(Base):
    __tablename__ = "craft_carts"

    id: Mapped[int] = pk()
    # Código curto (~8 chars) gerado por secrets.token_urlsafe(6). Indexado e
    # único — é a chave de lookup pública (não o id sequencial).
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    # JSON array: [{uniqueName, qty, useFocus, placeLabel, journalId, transmuteTargetId}, ...]
    items: Mapped[list] = mapped_column(json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
