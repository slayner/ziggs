"""
Histórico de preços de itens do Albion Online (fonte: albiondata API).

`item_prices` é append-only — nunca deletamos, só inserimos.
`item_prices_latest` é um upsert: sempre tem só 1 linha por (item_id, city, quality),
facilitando lookups rápidos sem precisar de MAX(recorded_at).

Cidades principais: Caerleon, Bridgewatch, Fort Sterling, Lymhurst, Martlock, Thetford.
Qualidade 1 = Normal (a mais comum nos drops de ZvZ).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, TimestampMixin, pk


class ItemPrice(Base):
    """Registro histórico de preço — append-only, nunca deletado."""
    __tablename__ = "item_prices"

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(48), nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sell_price_min: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemPriceLatest(Base):
    """Último preço conhecido — upsert a cada sync, 1 linha por (item_id, city, quality)."""
    __tablename__ = "item_prices_latest"
    __table_args__ = (UniqueConstraint("item_id", "city", "quality"),)

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(48), nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sell_price_min: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemPriceHistory(Base):
    """Histórico agregado de mercado (fonte primária: captura própria via
    companion do market-history do jogo — AuctionGetItemAverageStats).

    Cada linha é um bucket do gráfico do próprio jogo: quantidade negociada +
    prata total num timestamp, por item/qualidade/local/escala de tempo. Isto é
    o que nos torna independentes do AODP pra gráficos — o dado vem direto do
    servidor do Albion.

    `item_id` = UniqueName resolvido do índice numérico (via ao-bin-dump). O
    índice cru fica em `albion_id` — se o mapeamento falhar, o dado não se perde
    e pode ser re-resolvido depois. `timescale`: 0=24h, 1=7d, 2=4semanas.
    `silver_amount` é o total; o preço médio = silver_amount / item_count.
    """
    __tablename__ = "item_price_history"
    __table_args__ = (
        UniqueConstraint("item_id", "region", "quality", "location", "timescale", "bucket_ts"),
    )

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    albion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Servidor do Albion (west/east/europe — mesma nomenclatura do AODP e do
    # seletor de servidor do site). Mercados são separados por região e os
    # cluster ids se REPETEM entre elas; sem isto, dados de regiões diferentes
    # colidiam no mesmo bucket.
    region: Mapped[str] = mapped_column(String(16), default="west", nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # LocationId numérico (cluster) reportado pelo companion.
    location: Mapped[str] = mapped_column(String(48), nullable=False)
    timescale: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Timestamp cru do bucket (formato do pacote) — chave estável do bucket.
    bucket_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_count: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    silver_amount: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketSnapshot(Base):
    """Resumo de mercado pré-computado por item do catálogo — preço atual,
    margem (variação 7d) e demanda (itens vendidos 7d).

    Mantido quente pelo background task market_snapshot.run_forever(): a UI
    NUNCA dispara consulta externa — só lê daqui. Fonte por item: histórico
    próprio (companion) quando existe, senão a varredura AODP.
    """
    __tablename__ = "market_snapshot"
    __table_args__ = (UniqueConstraint("item_id", "region"),)

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Mercados são separados por servidor do Albion (west/east/europe) — 1 linha
    # por (item, região).
    region: Mapped[str] = mapped_column(String(16), default="west", nullable=False)
    # Preço médio mais recente (qualidade 1). 0 = sem dado em fonte nenhuma.
    price: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Variação % do preço na janela de 7 dias (margem). 0 quando sem base.
    change_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Itens vendidos na janela de 7 dias (demanda).
    demand: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(8), default="aodp", nullable=False)  # ziggs|aodp
    # Quando o preço foi VISTO pela última vez (timestamp do bucket mais
    # recente, não da varredura). Itens com price_ts velho (>3d) somem da
    # lista de pesquisa do site até um preço novo aparecer.
    price_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GoldPriceSnapshot(Base):
    """Cotação prata↔ouro por região (fonte: AODP gold.json) — histórico
    próprio no nosso banco em vez de depender do dashboard buscar direto da
    AODP a cada visita (services/gold_price.py faz backfill completo desde
    2017 + poll periódico). Append-only, 1 linha por (region, recorded_at)."""
    __tablename__ = "gold_price_snapshots"
    __table_args__ = (UniqueConstraint("region", "recorded_at"),)

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False)  # americas|europe|asia
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
