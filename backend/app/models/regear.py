"""Regear por screenshot: fila de pedidos vindos do canal de regear do Discord.

Fluxo: bot-v2 lê attachments no canal `regear_channel_id` → POST /regear/ingest
→ reconhecimento (OCR nome → API de killboard do Albion) → cria RegearRequest
pending. Logística revisa no site, edita `final_total` (sobrescreve o sugerido) e
aprova (`status=paid`) → débito cai em `guild.bank_balance`.

`detected_items` é JSON: [{item_id, name, quality, slot, category, eligible,
unit_price, total_price}]. `recognition_status`: recognized (API achou) | manual
(não achou, logística preenche) | error.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BigInt, Snowflake, TimestampMixin, json_type, pk

if TYPE_CHECKING:
    from app.models.events import Event


class RegearRequest(Base, TimestampMixin):
    __tablename__ = "regear_requests"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Evento vinculado (landmark). Prints postadas na thread de regear do evento
    # ficam atreladas a ele aqui. Em modos tab (leftover/guild_backed) os aprovados
    # somam em _calc_payout.total_regear e pendentes bloqueiam finalize; em modos
    # banco (full/none) o regear é independente (debita bank no approve). SET NULL
    # no delete do evento — o regear permanece pendente na fila geral, só perde o tag.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Quem postou a screenshot (snowflake Discord; nullable se o bot não mandou).
    requester_user_id: Mapped[int | None] = mapped_column(Snowflake)
    requester_name: Mapped[str | None] = mapped_column(String(255))

    # Prova: caminho relativo da imagem salva + id da msg no Discord (idempotência).
    screenshot_path: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_msg_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # Canal Discord de onde a screenshot veio — a guilda pode ter vários canais
    # de regear, cada um com sua própria % (ver regear_config.RegearSettings.
    # channels). Guardado pra fila de retry reaplicar a % certa depois, sem
    # depender do canal ainda existir na config atual da guilda.
    channel_id: Mapped[str | None] = mapped_column(String(64))

    # Resultado do reconhecimento.
    ocr_name: Mapped[str | None] = mapped_column(String(255))
    albion_event_id: Mapped[str | None] = mapped_column(String(64))
    death_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recognition_status: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    # Nº de tentativas de reconhecimento ( ingest + fila de retry ). Capa a fila
    # pra não martelar a API de killboard indefinidamente em pedidos sem morte
    # casável (morte PvE/antiga fora da janela).
    recognition_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")

    # Itens detectados + valuation. JSON: lista de dicts (ver schema RegearItemOut).
    detected_items: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    base_total: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    suggested_total: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    coverage_pct: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    # final_total editado pela logística sobrescreve o sugerido. NULL = usa suggested.
    final_total: Mapped[int | None] = mapped_column(BigInt())

    # pending | paid | denied | removed
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    handled_by_user_id: Mapped[int | None] = mapped_column(Snowflake)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    # Evento vinculado (landmark). Lazy: só carrega se _to_out tocar o título.
    event: Mapped["Event | None"] = relationship(
        "Event", foreign_keys=[event_id], viewonly=True, lazy="joined"
    )