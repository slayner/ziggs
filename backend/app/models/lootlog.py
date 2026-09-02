"""Submissões de log do lootlogger por CTA.

Fluxo: o jogador envia o .csv pelo bot-v2 e o backend armazena as coletas aqui.
A área de revisão é SÓ-ADMIN no site. Cada submissão conta isolada; o peso do
logger = valor total dos itens que ele logou.

A fatia `logger_percent` da tab do CTA é separada pra loggers e dividida pelo
peso — ver `services/lootlog.compute_logger_weights` + o hook em
`services/events._calc_payout`.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Snowflake, TimestampMixin, json_type, pk


class LootLogSubmission(Base, TimestampMixin):
    __tablename__ = "lootlog_submissions"
    __table_args__ = (
        # 1 submissão por (guilda, CTA, logger) — reenvio sobrescreve.
        UniqueConstraint("guild_id", "event_id", "submitter_user_id",
                         name="uq_lootlog_guild_event_submitter"),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Quem logou (snowflake Discord). Identificado só pra pagar a fatia do
    # logger — a área de revisão mostra o nome, mas o loot em si é anônimo
    # (IGNs da vítima/looter vêm do CSV, são dados públicos do killboard).
    submitter_user_id: Mapped[int | None] = mapped_column(Snowflake, index=True)
    submitter_name: Mapped[str | None] = mapped_column(String(255))

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Coletas parseadas: [{ts, item_id, item_name, quantity, looted_by,
    # looted_by_guild, looted_from}]. Anônimo no sentido de não atrela o looter
    # a um Discord user p/ payout — só conta p/ o peso do logger.
    loot_rows: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)

    # Soma de quantity*unit_price das coletas, precificadas no ingest via
    # services/loot.get_price (cache + API albion-online-data). 0 se a API falhou.
    silver_total: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    # Texto cru do arquivo enviado (idêntico ao .csv do lootlogger). A área admin
    # do site mostra isto como bloco copiável — sem parse, sem valor, só o arquivo.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)