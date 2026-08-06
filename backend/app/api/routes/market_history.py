"""Leitura pública do market history próprio (fonte pra futura aba de itens).

Dado de mercado é público — sem auth. A captura vem dos companions
(companion.py: /companion/market-history/submit); aqui só lemos.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.api import deps
from app.db import get_session
from app.models.prices import MarketSnapshot
from app.services import market_history as svc

router = APIRouter(prefix="/market-history", tags=["market-history"])

# Servidores do Albion — mesma nomenclatura do seletor de servidor do site.
_VALID_REGIONS = {"west", "east", "europe"}


def _region(raw: str) -> str:
    return raw if raw in _VALID_REGIONS else "west"


class HistoryBucket(BaseModel):
    bucket_ts: int
    item_count: int
    avg_price: int


class HistoryOut(BaseModel):
    item_id: str
    quality: int
    timescale: int
    location: str | None
    buckets: list[HistoryBucket]


class CatalogItem(BaseModel):
    id: str
    en: str
    pt: str
    c: str


@router.get("/catalog")
def catalog() -> list[CatalogItem]:
    """Catálogo completo de itens (base, sem @enchant) com categoria de
    mercado. Estático por deploy — o frontend cacheia em memória."""
    return [CatalogItem(**it) for it in svc.get_catalog()]


class SnapshotRow(BaseModel):
    id: str
    price: int
    change_pct: float
    demand: int
    source: str


@router.get("/snapshot")
async def snapshot(
    region: str = Query("west"),
    db: AsyncSession = Depends(deps.async_db_session),
) -> list[SnapshotRow]:
    """Resumo de mercado pré-computado (preço, margem 7d, demanda 7d) da região
    pedida, mantido quente pelo varredor de fundo — leitura pura, sem consulta
    externa. Trocar de servidor no site = novo fetch com a região nova.

    Só devolve itens com preço VISTO nos últimos 3 dias: item sem preço
    recente some da lista de pesquisa do site até um preço novo ser detectado
    (o varredor atualiza price_ts a cada ciclo)."""
    from datetime import datetime, timedelta, timezone

    fresh_after = datetime.now(timezone.utc) - timedelta(days=3)
    rows = (await db.scalars(select(MarketSnapshot).where(
        MarketSnapshot.region == _region(region),
        MarketSnapshot.price_ts.is_not(None),
    ))).all()
    out = []
    for r in rows:
        ts = r.price_ts
        if ts is None:
            continue
        if ts.tzinfo is None:  # SQLite não preserva tz na leitura
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < fresh_after:
            continue
        out.append(SnapshotRow(id=r.item_id, price=r.price, change_pct=r.change_pct,
                               demand=r.demand, source=r.source))
    return out


# ponytail: item_history chama svc.get_history (sync, recebe Session) — não dá
# pra passar AsyncSession. Migra quando o service migrar.
@router.get("/{item_id}")
def item_history(
    item_id: str,
    region: str = Query("west"),
    quality: int = Query(1, ge=1, le=5),
    timescale: int = Query(1, ge=0, le=2),
    location: str | None = Query(None),
    db=Depends(get_session),
) -> HistoryOut:
    """Histórico agregado de um item (da região). `location` None = todas as cidades."""
    buckets = svc.get_history(db, item_id, _region(region), quality, timescale, location)
    return HistoryOut(
        item_id=item_id, quality=quality, timescale=timescale,
        location=location, buckets=[HistoryBucket(**b) for b in buckets],
    )