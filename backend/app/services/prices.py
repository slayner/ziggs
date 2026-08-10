"""
Serviço de preços históricos do Albion Online.

Fonte: west.albion-online-data.com (API pública do projeto albiondata).
Estratégia de preços:
  - Usa o endpoint /stats/history com time-scale=24 (dados diários).
  - Para cada item: média ponderada (por item_count) dos últimos HISTORY_DAYS dias,
    por cidade; depois média simples entre as cidades com dados.
  - As 5 cidades usadas: Lymhurst, Fort Sterling, Thetford, Bridgewatch, Martlock.
  - Cache em ItemPriceLatest com city=_AVG_SENTINEL, TTL de 4h.

Funções legacy (sync_prices / get_price) mantidas para compatibilidade.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

from app.models.catalog import GameRole
from app.models.prices import ItemPrice, ItemPriceLatest

# ── mapeamento UniqueName ↔ game_name ─────────────────────────────────────────
# O game_name é o ID canônico do sistema de preços (nome em inglês do jogo).
# Carregado uma vez do seed. Se o arquivo não existir, as funções devolvem
# o input sem conversão (fallback — quebra silenciosa, não crash).

def _load_item_names() -> dict[str, str]:
    p = Path(__file__).resolve().parent.parent.parent / "data" / "item_names.json"
    if not p.exists():
        log.warning("item_names.json não encontrado — conversão de IDs desativada")
        return {}
    return json.loads(p.read_bytes())


_ITEM_NAMES: dict[str, str] = {}
_GAME_NAMES: dict[str, str] = {}  # reverso: game_name → unique_name


def _ensure_maps() -> None:
    global _ITEM_NAMES, _GAME_NAMES
    if _ITEM_NAMES:
        return
    _ITEM_NAMES = _load_item_names()
    # Reverso: quando há colisão (235 game_names mapeiam pra >1 UniqueName,
    # ex: "Rare Hemp" ← T4_FIBER_LEVEL2 e T4_FIBER_LEVEL2@2), prefere a versão
    # COM @enchant — é o que a AODB usa. Sem isto, encantados viravam flat
    # e a AODB devolvia vazio.
    for uid, gname in _ITEM_NAMES.items():
        existing = _GAME_NAMES.get(gname)
        if existing is None or ("@" in uid and "@" not in existing):
            _GAME_NAMES[gname] = uid


def _unique_to_game(uid: str) -> str:
    """UniqueName → game_name. Ex: T4_FIBER_LEVEL2@2 → 'Rare Hemp'."""
    _ensure_maps()
    return _ITEM_NAMES.get(uid, uid)


def _game_to_unique(gname: str) -> str:
    """game_name → UniqueName. Ex: 'Rare Hemp' → T4_FIBER_LEVEL2@2."""
    _ensure_maps()
    return _GAME_NAMES.get(gname, gname)


def _is_unconverted(uid: str) -> bool:
    """True se o id ainda parece UniqueName (não foi convertido pra game_name).
    Formato: T<n>_<...>. Usado pra REJEITAR writes de ids que falharam a
    conversão — gravar T_ no latest cria órfãos que nenhuma rota de leitura
    encontra (leitores buscam por game_name)."""
    return uid.startswith("T") and "_" in uid and uid[1:2].isdigit()

# Teto duro por tier para descartar dados obviamente contaminados.
_TIER_CAP = {1: 1_000_000, 2: 2_000_000, 3: 5_000_000, 4: 10_000_000, 5: 20_000_000,
             6: 30_000_000, 7: 50_000_000, 8: 100_000_000}

def _tier_from_game_id(game_id: str) -> int:
    """Extrai o tier (1-8) de um game_name ou UniqueName."""
    s = game_id.strip()
    if s.startswith("T") and s[1:2].isdigit():
        return int(s[1])
    return 8  # fallback conservador (teto alto)

# ── constantes ────────────────────────────────────────────────────────────────

_BASE_URL = "https://west.albion-online-data.com/api/v2/stats/prices"
_HISTORY_URL = "https://west.albion-online-data.com/api/v2/stats/history"
_DEFAULT_CITY = "Caerleon"
_PRICE_TTL = timedelta(hours=1)
_HISTORY_TTL = timedelta(hours=4)
_BATCH_SIZE = 50

CITIES = ["Lymhurst", "Fort Sterling", "Thetford", "Bridgewatch", "Martlock"]
HISTORY_DAYS = 7
_AVG_SENTINEL = "_5city_avg_"

VALID_SLOTS = ("offhand", "helmet", "armor", "boots", "cape", "food")

# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite não preserva tzinfo na leitura mesmo com DateTime(timezone=True)
    — mesmo problema tratado em claim_checker/battle_tracker/etc."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Companion: ingest de preços capturados via packet capture (Fase 2) ────────


async def upsert_companion_prices(
    db: AsyncSession,
    rows: list[dict[str, Any]],
    source_install: str | None = None,
) -> tuple[int, int]:
    """Insere preços reportados por companions (packet capture do mercado).

    Bulk SQL nativo (Postgres): 1 INSERT append-only no histórico + 1
    ON CONFLICT DO UPDATE no latest por chunk — NÃO faz N SELECTs + N INSERTs
    ORM (o caminho legado em _upsert_latest). Semântica "age mais próxima vence"
    preservada via WHERE no conflict (price_date mais recente prevalece).

    Retorna (accepted, rejected). rejected = rows sem item_id ou price == 0.
    """
    now = datetime.now(timezone.utc)
    history_rows: list[dict[str, Any]] = []
    latest_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    rejected = 0
    for r in rows:
        iid = r.get("item_id")
        price = r.get("sell_price_min")
        if not iid or not price:
            rejected += 1
            continue
        # Guarda: id que não foi convertido pra game_name (ainda T_xxx) cria
        # órfão no latest — leitores buscam por game_name e nunca encontram.
        if _is_unconverted(iid):
            rejected += 1
            continue
        pd_raw = r.get("price_date") or r.get("sell_price_min_date") or now.isoformat()
        if isinstance(pd_raw, datetime):
            pd_raw = pd_raw.isoformat()
        pd_dt = _parse_dt(pd_raw)
        city = r.get("city", _DEFAULT_CITY)
        quality = int(r.get("quality", 1) or 1)
        history_rows.append({
            "item_id": iid, "city": city, "quality": quality,
            "sell_price_min": int(price), "price_date": pd_dt, "recorded_at": now,
            "source_install": source_install,
        })
        # Dedup por (item_id, city, quality): fica o de price_date mais recente
        # (mesma regra do _upsert_latest). O companion manda N ordens do mesmo
        # item num lote; sem isto o ON CONFLICT teria que desempatar no SQL.
        key = (iid, city, quality)
        prev = latest_by_key.get(key)
        if prev is None or pd_dt > prev["price_date"]:
            latest_by_key[key] = {
                "item_id": iid, "city": city, "quality": quality,
                "sell_price_min": int(price), "price_date": pd_dt, "recorded_at": now,
            }
    if not history_rows:
        return (0, rejected)

    # Histórico append-only: 1 INSERT multi-values (bulk_insert_mappings).
    await db.run_sync(lambda s: s.bulk_insert_mappings(ItemPrice, history_rows))

    # Latest: 1 upsert nativo por chunk. WHERE preserva "age mais próxima vence"
    # — só sobrescreve se o novo price_date for estritamente mais recente que o
    # existente. Sem o SELECT por row do caminho legado.
    latest_rows = list(latest_by_key.values())
    stmt = _pg_insert(ItemPriceLatest).values(latest_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ItemPriceLatest.item_id, ItemPriceLatest.city, ItemPriceLatest.quality],
        set_={"sell_price_min": stmt.excluded.sell_price_min,
              "price_date": stmt.excluded.price_date,
              "recorded_at": stmt.excluded.recorded_at},
        where=ItemPriceLatest.price_date < stmt.excluded.price_date,
    )
    await db.execute(stmt)
    await db.commit()
    log.info("upsert_companion_prices: %d rows (%d chaves únicas) install=%s",
             len(history_rows), len(latest_rows), source_install or "?")
    return (len(history_rows), rejected)


# ── API legado (uma cidade, spot price) ───────────────────────────────────────


async def _fetch_from_api(
    item_ids: list[str],
    city: str = _DEFAULT_CITY,
    quality: int = 1,
) -> list[dict[str, Any]]:
    ids_str = ",".join(item_ids)
    url = f"{_BASE_URL}/{ids_str}.json?locations={city}&qualities={quality}"
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _upsert_latest(
    db: AsyncSession,
    data: list[dict[str, Any]],
    now: datetime,
    source_install: str | None = None,
) -> None:
    # Cache do objeto por chave única DENTRO deste lote. O companion manda o
    # mesmo (item_id, city, quality) várias vezes num lote só (o mercado tem N
    # ordens do mesmo item); como a sessão é autoflush=False, o select abaixo
    # não enxerga um add pendente da mesma chave — sem este cache, o 2º vira um
    # 2º INSERT e estoura o UNIQUE constraint no commit.
    pending: dict[tuple[str, str, int], ItemPriceLatest] = {}
    for row in data:
        if not row.get("sell_price_min"):
            continue
        item_id: str = row["item_id"]
        if _is_unconverted(item_id):
            continue
        city: str = row["city"]
        quality: int = row["quality"]
        price: int = row["sell_price_min"]
        price_date = _parse_dt(row["sell_price_min_date"])

        # Atribuição só no histórico append-only. Em item_prices_latest o
        # último a escrever vence, então guardar a fonte lá diria pouco.
        db.add(ItemPrice(
            item_id=item_id, city=city, quality=quality,
            sell_price_min=price, price_date=price_date, recorded_at=now,
            source_install=source_install,
        ))
        key = (item_id, city, quality)
        existing = pending.get(key) or await db.scalar(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id == item_id,
                ItemPriceLatest.city == city,
                ItemPriceLatest.quality == quality,
            )
        )
        if existing:
            pending[key] = existing
            # "Age mais próxima vence": a fonte (nossa captura via companion vs.
            # AODP) não importa — quem tem o price_date mais recente prevalece.
            # Um preço vindo de dado mais velho não sobrescreve um mais fresco.
            if price_date < _aware(existing.price_date):
                continue
            existing.sell_price_min = price
            existing.price_date = price_date
            existing.recorded_at = now
        else:
            obj = ItemPriceLatest(
                item_id=item_id, city=city, quality=quality,
                sell_price_min=price, price_date=price_date, recorded_at=now,
            )
            db.add(obj)
            pending[key] = obj
    await db.flush()


async def sync_prices(
    db: AsyncSession,
    item_ids: list[str],
    city: str = _DEFAULT_CITY,
    quality: int = 1,
    force: bool = False,
) -> None:
    if not item_ids:
        return
    now = datetime.now(timezone.utc)
    stale_before = now - _PRICE_TTL
    if not force:
        rows = (await db.scalars(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id.in_(item_ids),
                ItemPriceLatest.city == city,
                ItemPriceLatest.quality == quality,
            )
        )).all()
        fresh = {r.item_id for r in rows if _aware(r.recorded_at) >= stale_before}
        to_fetch = [i for i in item_ids if i not in fresh]
    else:
        to_fetch = list(item_ids)
    if not to_fetch:
        return
    # Libera read tx antes do HTTP (gather de _fetch_from_api chama AODP).
    await db.commit()
    tasks = [
        _fetch_from_api(to_fetch[i:i + _BATCH_SIZE], city, quality)
        for i in range(0, len(to_fetch), _BATCH_SIZE)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            await _upsert_latest(db, result, now)


async def get_price(
    db: AsyncSession,
    item_id: str,
    city: str = _DEFAULT_CITY,
    quality: int = 1,
) -> int:
    await sync_prices(db, [item_id], city, quality)
    row = await db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == city,
            ItemPriceLatest.quality == quality,
        )
    )
    return row.sell_price_min if row else 0


# ── API 5 cidades — média histórica ──────────────────────────────────────────


async def _fetch_history(item_ids: list[str]) -> list[dict]:
    """Busca histórico diário das 5 cidades para uma lista de itens (qualidades 1-4).

    item_ids vêm em game_name; ADP usa UniqueName. Converte antes de chamar.
    """
    adp_ids = [_game_to_unique(i) for i in item_ids]
    ids_str = ",".join(adp_ids)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_HISTORY_URL}/{ids_str}.json",
            params={
                "locations": ",".join(CITIES),
                "qualities": "1,2,3,4",
                "time-scale": 24,
            },
        )
        resp.raise_for_status()
        return resp.json()


def _iqr_trim(vals: list[int]) -> list[int]:
    """Descarta outliers além de [Q1-1.5·IQR, Q3+1.5·IQR].

    # ponytail: IQR sobre as ~5 médias por cidade — capto a "cidade troll"
    # (1 listing absurdo num mercado fino) sem cortar variação real. Com 5
    # pontos IQR é grosso; upgrade path = IQR cruzando dia×cidade (~35 pts)
    # se trolls persistentes num mesmo mercado viessem além disso.
    Se todos caem fora (caso patológico) devolve o conjunto original."""
    if len(vals) < 4:
        return vals
    s = sorted(vals)
    q1 = s[len(s) // 4]
    q3 = s[(len(s) * 3) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return vals
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    trimmed = [v for v in vals if lo <= v <= hi]
    return trimmed or vals


def _compute_5city_avg(history_data: list[dict]) -> dict[tuple[str, int], int]:
    """
    Calcula a média de preço por (item_id, quality) a partir do histórico das 5 cidades.

    Por cidade: média ponderada de avg_price pelos últimos HISTORY_DAYS dias.
    Entre cidades: mediana sobre as cidades com dados, **após IQR-trim**.
    Cidades sem dados no período são ignoradas.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    city_values: dict[tuple[str, int], list[int]] = {}

    for rec in history_data:
        iid = rec.get("item_id")
        q = rec.get("quality", 1)
        if not iid:
            continue
        # ADP devolve UniqueName; converte pra game_name (formato do banco)
        iid = _unique_to_game(iid)
        if _is_unconverted(iid):
            continue

        recent = [
            d for d in (rec.get("data") or [])
            if d.get("avg_price") and (d.get("item_count") or 0) > 0
            and _parse_dt(d["timestamp"]) >= cutoff
        ]
        if not recent:
            continue

        total_count = sum(d["item_count"] for d in recent)
        if total_count == 0:
            continue
        weighted_avg = sum(d["avg_price"] * d["item_count"] for d in recent) / total_count
        city_values.setdefault((iid, q), []).append(int(weighted_avg))

    return {
        key: round(statistics.median(trimmed))
        for key, vals in city_values.items()
        if vals
        for trimmed in (_iqr_trim(vals),)
    }


def _battle_history_prices(history_data: list[dict]) -> dict[str, int]:
    """Preco por item baseado em vendas reais, com cobertura em >=3 cidades."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    cities: dict[tuple[str, int], set[str]] = {}
    caps: dict[str, int] = {}
    for rec in history_data:
        uid = rec.get("item_id")
        if not uid:
            continue
        iid = _unique_to_game(uid)
        if _is_unconverted(iid):
            continue
        recent = any(
            d.get("avg_price") and (d.get("item_count") or 0) > 0
            and _parse_dt(d["timestamp"]) >= cutoff
            for d in (rec.get("data") or [])
        )
        location = rec.get("location") or rec.get("city")
        if recent and location:
            key = (iid, int(rec.get("quality", 1) or 1))
            cities.setdefault(key, set()).add(location)
            caps[iid] = _TIER_CAP.get(_tier_from_game_id(uid), _TIER_CAP[8])

    by_item: dict[str, dict[int, int]] = {}
    for (iid, quality), price in _compute_5city_avg(history_data).items():
        if len(cities.get((iid, quality), ())) >= 3 and 0 < price <= caps.get(iid, _TIER_CAP[8]):
            by_item.setdefault(iid, {})[quality] = price

    return {
        iid: qualities.get(1, min(qualities.values()))
        for iid, qualities in by_item.items()
    }


def _battle_spot_prices(rows: list[dict]) -> dict[str, int]:
    """Fallback conservador: mediana do menor preco em pelo menos 3 cidades."""
    by_item_city: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for row in rows:
        price = int(row.get("sell_price_min") or 0)
        uid = row.get("item_id") or ""
        city = row.get("city") or ""
        quality = int(row.get("quality", 1) or 1)
        if not uid or not city or price <= 0 or quality == 5:
            continue
        if price > _TIER_CAP.get(_tier_from_game_id(uid), _TIER_CAP[8]):
            continue
        iid = _unique_to_game(uid)
        if not _is_unconverted(iid):
            by_item_city.setdefault(iid, {}).setdefault(city, []).append((quality, price))

    result = {}
    for iid, cities in by_item_city.items():
        city_prices = []
        for values in cities.values():
            normal = [price for quality, price in values if quality == 1]
            city_prices.append(min(normal or [price for _, price in values]))
        if len(city_prices) >= 3:
            result[iid] = round(statistics.median(city_prices))
    return result


async def _upsert_avg(db: AsyncSession, item_id: str, quality: int, price: int, now: datetime) -> None:
    existing = await db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality == quality,
        )
    )
    if existing:
        existing.sell_price_min = price
        existing.price_date = now
        existing.recorded_at = now
    else:
        db.add(ItemPriceLatest(
            item_id=item_id, city=_AVG_SENTINEL, quality=quality,
            sell_price_min=price, price_date=now, recorded_at=now,
        ))


async def sync_5city_prices(
    db: AsyncSession,
    item_ids: list[str],
    quality: int = 1,  # kept for compat but ignored — always fetches q1-4
    force: bool = False,
) -> None:
    """Garante que todos os item_ids têm média 5 cidades recente (< _HISTORY_TTL) no banco.

    item_ids pode vir em UniqueName (callers legacy: regear, events) ou game_name
    (callers novos: companion). Converte tudo pra game_name antes de operar."""
    if not item_ids:
        return
    # Normaliza pra game_name (formato do DB). Se já é game_name, passa direto.
    item_ids = [_unique_to_game(i) for i in item_ids]

    now = datetime.now(timezone.utc)
    stale_before = now - _HISTORY_TTL

    if not force:
        # use quality=1 as proxy: if q1 is fresh, all qualities were fetched together
        rows = (await db.scalars(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id.in_(item_ids),
                ItemPriceLatest.city == _AVG_SENTINEL,
                ItemPriceLatest.quality == 1,
            )
        )).all()
        fresh = {r.item_id for r in rows if _aware(r.recorded_at) >= stale_before}
        to_fetch = [i for i in item_ids if i not in fresh]
    else:
        to_fetch = list(item_ids)

    if not to_fetch:
        return

    # Libera read tx antes do HTTP (gather de _fetch_history chama AODP).
    await db.commit()
    tasks = [
        _fetch_history(to_fetch[i:i + _BATCH_SIZE])
        for i in range(0, len(to_fetch), _BATCH_SIZE)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if not isinstance(result, list):
            continue
        avgs = _compute_5city_avg(result)
        for (iid, q), price in avgs.items():
            await _upsert_avg(db, iid, q, price, now)

    await db.flush()


_BATTLE_SENTINEL = "_battle_spot_"
BATTLE_PRICE_TTL = timedelta(hours=8)  # preços de loot de batalha: reusar cache < 8h, re-buscar se mais velho


async def _fetch_spot_prices(item_ids: list[str]) -> list[dict[str, Any]]:
    """Preço atual (não histórico) — aceita id com @enchant direto, diferente
    de /stats/history (que devolve [] pra qualquer id com @enchant).

    Materiais brutos/refinados também usam UniqueName no ADP."""
    adp_ids = [_game_to_unique(i) for i in item_ids]
    ids_str = ",".join(adp_ids)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_BASE_URL}/{ids_str}.json",
            params={"locations": ",".join(CITIES), "qualities": "1,2,3,4"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_battle_prices(db: AsyncSession, item_ids: list[str]) -> dict[str, int]:
    """Preco de loot: vendas historicas; spot so como fallback conservador."""
    unique = list(dict.fromkeys(item_ids))
    if not unique:
        return {}
    # Callers mandam UniqueNames (Type da API de batalhas). Converte pra
    # game_name pra bater com o cache do DB. Devolve o dict com as chaves
    # ORIGINAIS (UniqueNames) pra manter compatibilidade com os callers.
    # Ids que não converteram (item fora do mapa) ficam como T_xxx — não
    # buscamos nem gravamos, só devolvem 0 pro caller.
    game_ids = [_unique_to_game(i) for i in unique]
    market_game_ids = [gid for gid in game_ids if not _is_unconverted(gid)]
    if not market_game_ids:
        return {}
    # Lê do cache e FECHA a transação antes do HTTP — uma read transaction aberta
    # durante o fetch bloqueia o auto-checkpoint do WAL e contentiona com outros
    # writers. Copiar pra dict e commitar solta a tx antes da rede.
    # Age: preços com < BATTLE_PRICE_TTL (8h) são reusados; o resto é re-buscado
    # no AODP e re-gravado. Antes era cache permanente (nunca reconsultado), o
    # que deixava preços stale pra sempre e causava fetches de 50 itens a cada
    # ciclo do battle_price_reprocessor (5s) — 10 req/min no AODP só pra
    # reprocessar o mesmo lote.
    now = datetime.now(timezone.utc)
    ttl_before = now - BATTLE_PRICE_TTL
    rows = (await db.scalars(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id.in_(market_game_ids),
            ItemPriceLatest.city == _BATTLE_SENTINEL,
        )
    )).all()
    cached = {}
    stale_ids = set()
    for r in rows:
        cached[r.item_id] = r.sell_price_min
        ra = _aware(r.recorded_at)
        if ra is None or ra < ttl_before:
            stale_ids.add(r.item_id)
    await db.commit()  # fecha a read-only tx antes do HTTP
    # Dedup: vários UniqueNames podem mapear pro mesmo game_name.
    # Sem isto, o ON CONFLICT DO UPDATE recebe chaves duplicadas no mesmo
    # INSERT e estoura CardinalityViolation.
    missing = list(dict.fromkeys(i for i in market_game_ids if i not in cached or i in stale_ids))
    if missing:
        now = datetime.now(timezone.utc)
        t_fetch0 = time.monotonic()
        # Spot (nao historico): o endpoint /stats/prices aceita @enchant direto,
        # o /stats/history retorna vazio pra varios itens @enchant. Qualidade 5
        # (Masterpiece) e' rarissima e inflaciona; o resto entra, inclusive q1
        # (recursos e a maioria do loot real).
        tasks = [_fetch_spot_prices(missing[i:i + _BATCH_SIZE]) for i in range(0, len(missing), _BATCH_SIZE)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        t_fetch = time.monotonic() - t_fetch0
        by_item: dict[str, list[int]] = {}
        for result in results:
            if not isinstance(result, list):
                continue
            for row in result:
                if not row.get("sell_price_min"):
                    continue
                q = row.get("quality", 1)
                if q == 5:
                    continue
                uid = row["item_id"]
                if int(row["sell_price_min"]) > _TIER_CAP.get(_tier_from_game_id(uid), _TIER_CAP[8]):
                    continue
                by_item.setdefault(_unique_to_game(uid), []).append(int(row["sell_price_min"]))
        rows = []
        for item_id in missing:
            vals = sorted(v for v in by_item.get(item_id, []) if v > 0)
            if not vals:
                price = 0
            elif len(vals) == 1:
                price = vals[0] if vals[0] <= 500_000 else 0
            elif len(vals) == 2:
                price = min(vals)
            else:
                s = sorted(vals)
                n = len(s)
                q1 = s[n // 4]
                q3 = s[3 * n // 4]
                iqr = q3 - q1
                if iqr > 0:
                    lo = q1 - 1.5 * iqr
                    hi = q3 + 1.5 * iqr
                    filtered = [v for v in s if lo <= v <= hi]
                    if filtered:
                        price = round(statistics.median(filtered))
                    else:
                        price = round(statistics.median(s))
                else:
                    price = round(statistics.median(s))
            cached[item_id] = price
            rows.append({"item_id": item_id, "city": _BATTLE_SENTINEL, "quality": 1,
                         "sell_price_min": price, "price_date": now, "recorded_at": now})
        if rows:
            t_c0 = time.monotonic()
            # ON CONFLICT DO UPDATE (não DO NOTHING): preços stale re-buscados
            # precisam sobrescrever o cache antigo. Antes era DO NOTHING, que
            # mantinha o preço stale pra sempre mesmo após re-buscar.
            stmt = _pg_insert(ItemPriceLatest).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ItemPriceLatest.item_id, ItemPriceLatest.city, ItemPriceLatest.quality],
                set_={"sell_price_min": stmt.excluded.sell_price_min,
                      "price_date": stmt.excluded.price_date,
                      "recorded_at": stmt.excluded.recorded_at},
            )
            await db.execute(stmt)
            await db.commit()
            t_commit = time.monotonic() - t_c0
            if t_fetch > 2.0 or t_commit > 1.0:
                log.warning("get_battle_prices: LENTO — %d itens, fetch=%.1fs commit=%.1fs",
                            len(missing), t_fetch, t_commit)
            else:
                log.debug("get_battle_prices: %d itens, fetch=%.1fs commit=%.1fs",
                           len(missing), t_fetch, t_commit)
    # Mapeia de volta: game_name → UniqueName (chaves que os callers esperam)
    game_to_uid = dict(zip(game_ids, unique))
    result = {}
    for gid, price in cached.items():
        uid = game_to_uid.get(gid, gid)
        result[uid] = price
    return result


async def get_5city_avg_price(db: AsyncSession, item_id: str, quality: int = 1) -> int:
    """Retorna a média histórica 5 cidades para um item (busca se o cache estiver expirado)."""
    await sync_5city_prices(db, [item_id], quality)
    row = await db.scalar(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id == item_id,
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality == quality,
        )
    )
    return row.sell_price_min if row else 0


# ── calculadora de regear ─────────────────────────────────────────────────────


class RegearItemEstimate:
    def __init__(
        self,
        slot: str,
        item_id: str,
        name: str,
        quality: int,
        quantity: int,
        unit_price: int,
    ) -> None:
        self.slot = slot
        self.item_id = item_id
        self.name = name
        self.quality = quality
        self.quantity = quantity
        self.unit_price = unit_price
        self.total_price = unit_price * quantity


class RegearEstimate:
    def __init__(
        self,
        participant_id: int,
        user_name: str | None,
        game_role_id: int | None,
        game_role_name: str | None,
        items: list[RegearItemEstimate],
    ) -> None:
        self.participant_id = participant_id
        self.user_name = user_name
        self.game_role_id = game_role_id
        self.game_role_name = game_role_name
        self.items = items
        self.total = sum(i.total_price for i in items)
        self.price_basis = f"Média histórica 5 cidades ({HISTORY_DAYS}d)"
        self.calculated_at = datetime.now(timezone.utc).isoformat()


async def estimate_regear(
    db: AsyncSession,
    participant_id: int,
    guild_id: int,
    event_id: int,
) -> RegearEstimate:
    """
    Calcula o regear de um participante com base na sua função no evento.
    Preços: média histórica dos últimos 7 dias nas 5 principais cidades.
    """
    from app.models.events import EventParticipant

    participant = await db.get(EventParticipant, participant_id)
    if participant is None or participant.event_id != event_id or participant.guild_id != guild_id:
        raise ValueError("participante não encontrado")

    base = RegearEstimate(
        participant_id=participant_id,
        user_name=participant.user_name,
        game_role_id=participant.game_role_id,
        game_role_name=None,
        items=[],
    )

    if participant.game_role_id is None:
        return base

    role = await db.get(GameRole, participant.game_role_id)
    if role is None:
        return base

    base.game_role_name = role.name
    build_items: list[dict] = role.build_items or []

    item_ids = [
        bi["item_id"] for bi in build_items
        if bi.get("item_id") and bi.get("slot") in VALID_SLOTS
    ]

    if item_ids:
        # Libera read tx antes do HTTP (sync_5city_prices chama AODP).
        await db.commit()
        await sync_5city_prices(db, item_ids)

    results: list[RegearItemEstimate] = []
    for bi in build_items:
        iid = bi.get("item_id")
        slot = bi.get("slot", "")
        if not iid or slot not in VALID_SLOTS:
            continue

        quality = int(bi.get("quality", 1))
        quantity = int(bi.get("quantity", 1))

        row = await db.scalar(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id == iid,
                ItemPriceLatest.city == _AVG_SENTINEL,
                ItemPriceLatest.quality == quality,
            )
        )
        unit_price = row.sell_price_min if row else 0
        results.append(RegearItemEstimate(
            slot=slot,
            item_id=iid,
            name=bi.get("name", iid),
            quality=quality,
            quantity=quantity,
            unit_price=unit_price,
        ))

    base.items = results
    base.total = sum(i.total_price for i in results)
    return base


# ── regear por screenshot: sugestão de preço anti-troll ───────────────────────

import re as _re

_BASE_RE = _re.compile(r"^T\d+_")
_ENCH_RE = _re.compile(r"@\d+$")

# Slot da API de eventos do Albion (Victim.Equipment) e slot do catálogo (build_items)
# → categoria canônica usada na config de eligibilidade de regear da guilda.
SLOT_TO_CATEGORY = {
    "MainHand": "weapon", "weapon": "weapon",
    "OffHand": "offhand", "offhand": "offhand",
    "Head": "helmet", "helmet": "helmet",
    "Armor": "armor", "armor": "armor",
    "Shoes": "boots", "boots": "boots",
    "Cape": "cape", "cape": "cape",
    "Mount": "mount", "mount": "mount",
    "Bag": "bag", "bag": "bag",
    "Potion": "potion", "potion": "potion",
    "Food": "food", "food": "food",
}
REGEAR_CATEGORIES = (
    "weapon", "offhand", "helmet", "armor", "boots", "cape", "mount", "bag", "food", "potion",
)


def item_base_id(item_id: str) -> str:
    """T5_HEAD_PLATE_SET1@2 → HEAD_PLATE_SET1 (sem tier, sem enchant)."""
    return _ENCH_RE.sub("", _BASE_RE.sub("", item_id or ""))


def slot_category(slot: str) -> str | None:
    return SLOT_TO_CATEGORY.get(slot) if slot else None


async def suggest_regear_price(
    db: AsyncSession,
    items: list[dict],
    coverage_pct: int,
    enabled_categories: set[str] | None = None,
    disabled_item_bases: set[str] | None = None,
) -> dict:
    """Sugere preço de regear para uma lista de itens detectados na screenshot.

    `items`: [{"item_id","quality","slot"}] (slot = MainHand|Head|... ou helmet|armor|...).
    `coverage_pct`: 0-100 — % do valor médio que a guilda paga (ex.: 50).
    `enabled_categories`: categorias ligadas na config; None = todas.
    `disabled_item_bases`: override por item-base desligado.

    Preço: média 5 cidades (IQR-trim, 7d). Itens @enchant que o histórico não
    resolve caem no pipeline de spot mediano (get_battle_prices). Filtra itens
    não-elegíveis (categoria desligada ou override) — aparecem marcados mas
    não somam. suggested_total = round(base_total * coverage_pct/100).
    """
    enabled_categories = enabled_categories or set(REGEAR_CATEGORIES)
    disabled_item_bases = disabled_item_bases or set()
    pct = max(0, min(100, int(coverage_pct)))

    out_items: list[dict] = []
    eligible_ids: list[str] = []
    for it in items:
        iid = it.get("item_id") or ""
        slot = it.get("slot") or ""
        cat = slot_category(slot)
        eligible = bool(cat and cat in enabled_categories and item_base_id(iid) not in disabled_item_bases)
        rec = {
            "item_id": iid, "name": iid, "quality": int(it.get("quality", 1) or 1),
            "slot": slot, "category": cat, "eligible": eligible, "unit_price": 0, "total_price": 0,
        }
        out_items.append(rec)
        if eligible and iid:
            eligible_ids.append(iid)

    # 5 cidades (history) primeiro — @enchant volta 0, resolve depois no spot.
    if eligible_ids:
        await sync_5city_prices(db, list(dict.fromkeys(eligible_ids)))

    # Lê cache _AVG_SENTINEL; coleta os que falharam (enchanted) pro spot mediano.
    need_spot: list[str] = []
    for rec in out_items:
        if not rec["eligible"] or not rec["item_id"]:
            continue
        row = await db.scalar(
            select(ItemPriceLatest).where(
                ItemPriceLatest.item_id == rec["item_id"],
                ItemPriceLatest.city == _AVG_SENTINEL,
                ItemPriceLatest.quality == rec["quality"],
            )
        )
        price = row.sell_price_min if row else 0
        if price > 0:
            rec["unit_price"] = price
            rec["total_price"] = price
        else:
            need_spot.append(rec["item_id"])

    if need_spot:
        # Libera read tx antes do HTTP (get_battle_prices chama AODP).
        await db.commit()
        spot = await get_battle_prices(db, list(dict.fromkeys(need_spot)))
        for rec in out_items:
            if rec["eligible"] and rec["unit_price"] == 0 and rec["item_id"] in spot:
                p = spot[rec["item_id"]]
                rec["unit_price"] = p
                rec["total_price"] = p

    base_total = sum(r["total_price"] for r in out_items if r["eligible"])
    suggested_total = round(base_total * pct / 100)
    return {
        "items": out_items,
        "base_total": base_total,
        "suggested_total": suggested_total,
        "coverage_pct": pct,
        "price_basis": f"Média 5 cidades IQR-trim ({HISTORY_DAYS}d) × {pct}% cobertura",
    }


# ── self-check ────────────────────────────────────────────────────────────────

def _demo_iqr() -> None:
    """Afirma que IQR-trim difere da média crua quando há um outlier troll."""
    # 4 cidades ~1000 + 1 cidade troll 50000 → média crua viesa, trim remove.
    vals = [980, 1000, 1020, 1010, 50000]
    trimmed = _iqr_trim(vals)
    mean_raw = sum(vals) / len(vals)
    mean_trim = sum(trimmed) / len(trimmed)
    assert 50000 not in trimmed, "troll não foi removido"
    assert abs(mean_trim - 1002.5) < 5, f"trim {mean_trim} longe do esperado"
    assert mean_raw > 10000, "média crua deveria estar viesada pelo troll"
    print(f"iqr ok: raw={mean_raw:.0f} trim={mean_trim:.0f} kept={trimmed}")


async def _demo_freshest_wins() -> None:
    """Afirma que _upsert_latest mantém o preço de dado mais recente, venha da
    nossa captura (companion) ou do AODP — 'age mais próxima vence'."""
    from datetime import datetime, timezone

    class _Row:
        def __init__(self, price, pdate):
            self.item_id, self.city, self.quality = "T4_BAG", "Martlock", 1
            self.sell_price_min, self.price_date, self.recorded_at = price, pdate, pdate

    class _FakeDB:
        def __init__(self, existing):
            self.row = existing
            self.added = []
        def add(self, obj):
            self.added.append(obj)
        async def scalar(self, _q):
            return self.row
        async def flush(self):
            pass

    old = datetime(2026, 7, 1, tzinfo=timezone.utc)
    new = datetime(2026, 7, 17, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    # Existente é VELHO; chega um preço NOVO → sobrescreve.
    row = _Row(100, old)
    db = _FakeDB(row)
    await _upsert_latest(db, [{"item_id": "T4_BAG", "city": "Martlock", "quality": 1,
                         "sell_price_min": 250, "sell_price_min_date": new.isoformat()}], now)
    assert row.sell_price_min == 250, "preço mais fresco deveria vencer"

    # Existente é NOVO; chega um preço VELHO → mantém o novo.
    row = _Row(250, new)
    db = _FakeDB(row)
    await _upsert_latest(db, [{"item_id": "T4_BAG", "city": "Martlock", "quality": 1,
                         "sell_price_min": 100, "sell_price_min_date": old.isoformat()}], now)
    assert row.sell_price_min == 250, "dado velho não deveria sobrescrever o fresco"
    print("freshest-wins OK")


if __name__ == "__main__":
    _demo_iqr()
    asyncio.run(_demo_freshest_wins())
    # item_base_id sanity
    assert item_base_id("T5_HEAD_PLATE_SET1@2") == "HEAD_PLATE_SET1"
    assert item_base_id("T8_MOUNT_OX") == "MOUNT_OX"
    assert slot_category("MainHand") == "weapon"
    assert slot_category("Head") == "helmet"
    print("prices self-check OK")
