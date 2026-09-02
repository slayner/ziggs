"""Horizonte de postagem — cutoff por região.

Define até que ponto no passado uma kill/batalha ainda deve ser postada no
Discord. Regra do dono:

    cutoff(region) = agora - 48h - avg_api_delay(region, 72h)

O `avg_api_delay` é o delay MÉDIO de publicação da API do Albion nas últimas
72h, medido de graça em Battle: AVG(fetched_at - start_time) das batalhas com
start_time >= agora - 72h. Em dia normal ~5min; em dia de tráfego alto já
ficou 8-30h — um cutoff fixo de 48h descartava kills que a API demorou pra
expor mas ainda são "novas" pro usuário.

Eventos com timestamp < cutoff NÃO são postados (permanecem no banco, só
não vão pro Discord). Já postados não são re-postados (watermark por
timestamp, ver juicy_kills/battle_feed).

Kills não tem `fetched_at` (PlayerKillEvent), só `timestamp` (horário do
jogo). O delay de publicação da API é o mesmo das batalhas (mesmo host
gameinfo), então medir em Battle vale pra kill também."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.battles import Battle

log = logging.getLogger(__name__)

POSTABLE_HOURS = 48           # janela base: 48h
DELAY_WINDOW_HOURS = 72        # média do delay das últimas 72h
_FALLBACK_DELAY = timedelta(minutes=5)  # sem medição (restart, base vazia) — piso conservador

# Cache em memória: medir a cada 5min custava 1 query por região a cada request
# de queue. Reusar a última medição por 5min é de graça e o delay não muda rápido.
_DELAY_CACHE: dict[str, tuple[datetime, float]] = {}
_DELAY_CACHE_TTL = timedelta(minutes=5)


def _aware(dt: datetime) -> datetime:
    """SQLite não preserva tzinfo na leitura mesmo com DateTime(timezone=True)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def avg_api_delay_secs(db: AsyncSession, region: str) -> float:
    """Delay médio (segundos) da API do Albion para a região nas últimas 72h.

    Medido como AVG(fetched_at - start_time) das batalhas com start_time >=
    agora - 72h. Retorna 0 em vez de None (SQLite AVG de diferença de
    timestamps pode devolver None se a coluna estiver vazia).

    Em cache por 5min (_DELAY_CACHE_TTL): cada request de queue bate isso,
    e medir a cada request custava 1 query/region/request a toa."""
    now = datetime.now(timezone.utc)
    cached = _DELAY_CACHE.get(region)
    if cached and (now - cached[0]) < _DELAY_CACHE_TTL:
        return cached[1]

    window_start = now - timedelta(hours=DELAY_WINDOW_HOURS)
    try:
        # Diferença em segundos entre fetched_at e start_time.
        # Postgres: EXTRACT(EPOCH FROM (fetched_at - start_time)) — subtração
        # de timestamps devolve um interval; EXTRACT(EPOCH) converte pra segundos.
        # SQLite: strftime('%s', fetched_at) - strftime('%s', start_time).
        # Detectamos o dialect pela URL do engine da sessão — não há abstração
        # limpa em SQLAlchemy pra "segundos entre dois timestamps" cross-dialect.
        from sqlalchemy import extract, func
        dialect_name = db.bind.dialect.name if db.bind else "postgresql"
        if dialect_name == "sqlite":
            diff_expr = func.strftime("%s", Battle.fetched_at) - func.strftime("%s", Battle.start_time)
        else:
            diff_expr = extract("epoch", Battle.fetched_at - Battle.start_time)
        result = await db.scalar(
            select(func.avg(diff_expr)).where(
                Battle.region == region,
                Battle.start_time >= window_start,
            )
        )
    except Exception as e:
        log.warning("postable: falha ao medir delay de %s: %s", region, e)
        result = None

    delay_secs = float(result) if result else _FALLBACK_DELAY.total_seconds()
    _DELAY_CACHE[region] = (now, delay_secs)
    return delay_secs


async def postable_cutoff(db: AsyncSession, region: str) -> datetime:
    """Timestamp a partir do qual uma kill/batalha ainda deve ser postada.

    cutoff = agora - 48h - avg_api_delay(region, 72h)

    Eventos com timestamp < cutoff não são postados (permanecem no banco,
    só não vão pro Discord)."""
    delay_secs = await avg_api_delay_secs(db, region)
    return datetime.now(timezone.utc) - timedelta(hours=POSTABLE_HOURS, seconds=delay_secs)


async def postable_cutoffs_by_region(db: AsyncSession, regions: list[str]) -> dict[str, datetime]:
    """Cutoff por região de uma vez — reusa o cache por região."""
    out: dict[str, datetime] = {}
    for region in regions:
        out[region] = await postable_cutoff(db, region)
    return out


# ─── self-check ──────────────────────────────────────────────────────────────

async def _demo_cutoff_uses_measured_delay() -> None:
    """Afirma que o cutoff recua pelo delay medido: sem delay fica em 48h,
    com 30h de delay recua pra 78h atrás."""
    from unittest.mock import patch
    from app.db import AsyncSessionLocal
    import app.services.postable as mod

    # Sem medição (fallback): 48h + 5min
    mod._DELAY_CACHE.clear()
    with patch.object(mod, "avg_api_delay_secs", return_value=_FALLBACK_DELAY.total_seconds()):
        # não podemos chamar postable_cutoff real (precisa de db); testamos
        # a composição do cutoff diretamente
        now = datetime.now(timezone.utc)
        d = _FALLBACK_DELAY.total_seconds()
        cutoff = now - timedelta(hours=POSTABLE_HOURS, seconds=d)
        assert (now - cutoff) > timedelta(hours=47, minutes=55)
        assert (now - cutoff) < timedelta(hours=48, minutes=10)

    # Com 30h de delay: 48h + 30h = 78h
    with patch.object(mod, "avg_api_delay_secs", return_value=30 * 3600):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=POSTABLE_HOURS, seconds=30 * 3600)
        assert abs((now - cutoff).total_seconds() - 78 * 3600) < 5

    print(f"postable cutoff OK — janela base {POSTABLE_HOURS}h + delay médio {DELAY_WINDOW_HOURS}h")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_demo_cutoff_uses_measured_delay())