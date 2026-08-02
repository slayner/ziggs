"""
Market history próprio — histórico agregado de mercado capturado pelos
companions direto do jogo (operação AuctionGetItemAverageStats), armazenado no
NOSSO banco pra não depender do AODP em gráficos.

O pacote do jogo referencia o item por índice numérico; resolvemos pro
UniqueName (T4_BAG, ...) pela numeração de documento do `formatted/items.txt`
do ao-data — o MESMO índice que o pacote carrega e que o `ao-loot-logger` usa
(NÃO o campo `Index` do items.json, que é outra numeração e dava item errado).
Se não resolver, guardamos assim mesmo com um id de fallback e o índice cru em
`albion_id` — nada se perde.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert

from sqlalchemy.orm import Session

from app.models.prices import ItemPriceHistory

log = logging.getLogger(__name__)

_DUMP_DIR = Path(__file__).resolve().parents[2] / "data" / "ao-bin-dump"
_ITEMS_FILE = _DUMP_DIR / "items.json"       # só p/ nomes localizados (PT/ES)
_ITEMS_TXT_FILE = _DUMP_DIR / "items.txt"    # ÍNDICE DE PACOTE, autoritativo
_ITEMS_TXT_URL = (
    "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.txt"
)


def _read_items_txt() -> str:
    """Texto do items.txt: disco; se faltar, baixa 1× do ao-data e cacheia.

    O dump é referência local (gitignored, igual ao items.json). Sem essa
    rede-de-segurança, um backend sem o arquivo resolveria TODO loot como
    `IDX_n` calado. Mesma estratégia do ao-loot-logger (fetch com fallback).
    """
    try:
        return _ITEMS_TXT_FILE.read_text(encoding="utf-8")
    except OSError:
        pass
    import httpx
    resp = httpx.get(_ITEMS_TXT_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        _ITEMS_TXT_FILE.write_text(text, encoding="utf-8")
    except OSError:
        pass  # sem disco de escrita: usa em memória mesmo
    return text


@lru_cache(maxsize=1)
def _items_txt_rows() -> list[tuple[int, str, str]]:
    """Linhas do `formatted/items.txt` do ao-data: (numId, UniqueName, nome_EN).

    O numId é a POSIÇÃO 1-based da linha (a numeração é sequencial pura), e é
    exatamente o índice que o PACOTE de loot/mercado carrega — o mesmo que o
    `ao-loot-logger` resolve. Formato de cada linha: `  4172: T8_X : Nome`.
    """
    try:
        text = _read_items_txt()
    except Exception:
        return []
    out: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        try:
            num = int(parts[0].strip())
        except ValueError:
            continue
        uid = parts[1].strip()
        if not uid:
            continue
        name_en = parts[2].strip() if len(parts) > 2 else uid
        out.append((num, uid, name_en))
    return out


@lru_cache(maxsize=1)
def _index_to_name() -> dict[int, str]:
    """Mapa índice-do-jogo → UniqueName.

    Fonte: `items.txt` (numeração de documento do dump). NÃO usar o campo
    `Index` do `items.json` — é uma numeração interna DIFERENTE (subconjunto
    reordenado do dump) que diverge do índice real do jogo, com delta que
    CRESCE conforme o id (0 no topo, ~600 lá pelo id 4000). Isso saía como item
    errado no lootlog: pacote 4172 = T8_HEAD_LEATHER_SET3, mas `Index==4172` =
    T7_HEAD_CLOTH_SET3. Validado contra 286 eventos de um log do ao-loot-logger.
    """
    return {num: uid for num, uid, _ in _items_txt_rows()}


def resolve_item(albion_id: int) -> str:
    """UniqueName do índice numérico; fallback estável se desconhecido."""
    return _index_to_name().get(albion_id) or f"IDX_{albion_id}"


# ── Catálogo de itens (pra aba de mercado do site) ───────────────────────────

def _categorize(uid: str) -> str:
    """Categoria de mercado (estilo Albion) inferida do UniqueName.

    Ordem importa: _SKIN antes de MOUNT (skins de montaria), _TOOL antes de
    armas (T4_2H_TOOL_*), armaduras antes de recursos (ARMOR_CLOTH vs CLOTH).
    """
    u = uid.upper()
    if "HIDEOUT" in u:
        return "other"
    if "_SKIN" in u or "VANITY" in u or u.startswith("UNIQUE_"):
        return "vanity"
    if "_TOOL" in u:
        return "tools"
    if "FURNITURE" in u or "TROPHY" in u:
        return "furniture"
    if "FARM" in u:
        return "farm"
    if "MOUNT" in u:
        return "mounts"
    if "_CAPE" in u or "_BAG" in u:
        return "accessories"
    if "_HEAD_" in u or "_ARMOR_" in u or "_SHOES_" in u:
        return "armor"
    if "_OFF_" in u:
        return "offhand"
    if "_MAIN_" in u or "_2H_" in u:
        return "weapons"
    if "_MEAL" in u or "_POTION" in u or "_FISH" in u:
        return "consumables"
    if any(k in u for k in (
        "_WOOD", "_ORE", "_FIBER", "_HIDE", "_ROCK", "PLANKS", "METALBAR",
        "LEATHER", "CLOTH", "STONEBLOCK", "_RUNE", "_SOUL", "_RELIC",
        "SHARD", "ESSENCE", "EXTRACT",
    )):
        return "resources"
    return "other"


@lru_cache(maxsize=1)
def get_catalog() -> list[dict[str, str]]:
    """Catálogo enxuto pro frontend: [{id, en, pt, c}], só itens base.

    Variantes @enchant são colapsadas (o frontend oferece seletor de encant e
    monta `id@n` na hora). Itens sem nome localizado (internos) e NONTRADABLE
    ficam de fora.
    """
    try:
        data = json.loads(_ITEMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for entry in data:
        uid = entry.get("UniqueName") or ""
        names = entry.get("LocalizedNames")
        if not uid or "@" in uid or not names:
            continue
        if "NONTRADABLE" in uid.upper():
            continue
        en = names.get("EN-US") or ""
        pt = names.get("PT-BR") or en
        if not en and not pt:
            continue
        out.append({"id": uid, "en": en or pt, "pt": pt, "c": _categorize(uid)})
    return out


@lru_cache(maxsize=1)
def _localized_names() -> dict[str, dict[str, str]]:
    """UniqueName → LocalizedNames do `items.json` (só p/ traduzir PT/ES)."""
    try:
        data = json.loads(_ITEMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for entry in data:
        uid = entry.get("UniqueName")
        if uid:
            out[uid] = entry.get("LocalizedNames") or {}
    return out


@lru_cache(maxsize=1)
def get_index_catalog() -> list[dict[str, Any]]:
    """Catálogo POR ÍNDICE pro companion: [{i, id, en, pt, es}].

    `i` é o índice de PACOTE (numeração de documento do `items.txt`, ver
    `_index_to_name`) — o companion casa `item_index` do loot direto nessa
    chave. Variantes `@enchant` têm índice próprio, então FICAM. Nome EN vem do
    próprio `items.txt`; PT/ES vêm do `items.json` (join por UniqueName), e só
    entram quando diferem do inglês, pra não inflar o download.
    """
    loc = _localized_names()
    out: list[dict[str, Any]] = []
    for num, uid, name_en in _items_txt_rows():
        names = loc.get(uid, {})
        en = names.get("EN-US") or name_en or uid
        row: dict[str, Any] = {"i": num, "id": uid, "en": en}
        for lang, key in (("pt", "PT-BR"), ("es", "ES-ES")):
            val = names.get(key)
            if val and val != en:
                row[lang] = val
        out.append(row)
    return out


def ingest_history(db: Session, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Upserta linhas de histórico reportadas pelo companion.

    Cada row: {albion_id, quality, location, timescale, bucket_ts, item_count,
    silver_amount}. Chave de upsert: (item_id, quality, location, timescale,
    bucket_ts) — reenvio do mesmo bucket sobrescreve (dado do jogo é canônico).

    Retorna (accepted, rejected). rejected = rows sem albion_id/bucket_ts ou com
    item_count <= 0.
    """
    now = datetime.now(timezone.utc)
    accepted = 0
    rejected = 0
    # Cache do objeto por chave única DENTRO deste lote. O companion pode mandar
    # o mesmo bucket 2× (gráfico reaberto, pacote retransmitido); como a sessão
    # é autoflush=False, o select abaixo não enxerga um add pendente da mesma
    # chave — sem este cache, o 2º duplicado viraria um 2º INSERT e estouraria
    # o UNIQUE constraint no commit. Duplicado = last-wins (idempotente).
    pending: dict[tuple, ItemPriceHistory] = {}
    for r in rows:
        albion_id = r.get("albion_id")
        bucket_ts = r.get("bucket_ts")
        item_count = int(r.get("item_count", 0) or 0)
        if albion_id is None or bucket_ts is None or item_count <= 0:
            rejected += 1
            continue
        item_id = resolve_item(int(albion_id))
        region = str(r.get("region") or "west")
        location = str(r.get("location") or "")
        quality = int(r.get("quality", 1) or 1)
        timescale = int(r.get("timescale", 1) or 1)
        silver_amount = int(r.get("silver_amount", 0) or 0)
        bucket_ts = int(bucket_ts)
        key = (item_id, region, quality, location, timescale, bucket_ts)

        obj = pending.get(key)
        if obj is None:
            obj = db.scalar(
                select(ItemPriceHistory).where(
                    ItemPriceHistory.item_id == item_id,
                    ItemPriceHistory.region == region,
                    ItemPriceHistory.quality == quality,
                    ItemPriceHistory.location == location,
                    ItemPriceHistory.timescale == timescale,
                    ItemPriceHistory.bucket_ts == bucket_ts,
                )
            )
            if obj is None:
                obj = ItemPriceHistory(
                    item_id=item_id, albion_id=int(albion_id), region=region, quality=quality,
                    location=location, timescale=timescale, bucket_ts=bucket_ts,
                    item_count=item_count, silver_amount=silver_amount, recorded_at=now,
                )
                db.add(obj)
            pending[key] = obj
        # cria ou atualiza (last-wins) — mesma chave nunca gera 2 linhas.
        obj.item_count = item_count
        obj.silver_amount = silver_amount
        obj.albion_id = int(albion_id)
        obj.recorded_at = now
        accepted += 1
        log.info("market_history: %s q%d @ %s ts%d — %d itens, %d prata",
                 item_id, quality, location, bucket_ts, item_count, silver_amount)
    if accepted:
        db.commit()
    return (accepted, rejected)


def get_history(
    db: Session,
    item_id: str,
    region: str = "west",
    quality: int = 1,
    timescale: int = 1,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """Buckets do histórico pra um item (da região), ordenados por tempo
    (crescente).

    avg_price = silver_amount / item_count por bucket. Se `location` é None,
    agrega todas as cidades somando quantidades e prata (média ponderada).
    """
    q = select(ItemPriceHistory).where(
        ItemPriceHistory.item_id == item_id,
        ItemPriceHistory.region == region,
        ItemPriceHistory.quality == quality,
        ItemPriceHistory.timescale == timescale,
    )
    if location is not None:
        q = q.where(ItemPriceHistory.location == location)
    rows = db.scalars(q).all()

    # Agrega por bucket_ts (soma entre cidades quando location=None).
    buckets: dict[int, dict[str, int]] = {}
    for r in rows:
        b = buckets.setdefault(r.bucket_ts, {"item_count": 0, "silver_amount": 0})
        b["item_count"] += r.item_count
        b["silver_amount"] += r.silver_amount

    out = []
    for ts in sorted(buckets):
        b = buckets[ts]
        count = b["item_count"] or 1
        out.append({
            "bucket_ts": ts,
            "item_count": b["item_count"],
            "avg_price": round(b["silver_amount"] / count),
        })
    return out


# ── self-check ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    m = _index_to_name()
    assert len(m) > 1000, f"mapa de índices muito pequeno: {len(m)}"
    # Round-trip: pega um índice conhecido e confirma que resolve.
    some_idx = next(iter(m))
    assert resolve_item(some_idx) == m[some_idx]
    assert resolve_item(-999999).startswith("IDX_"), "fallback deveria ser IDX_"

    # TRAVA de regressão: o índice de pacote é a numeração do items.txt, NÃO o
    # campo `Index` do items.json. Par medido contra um log do ao-loot-logger.
    assert resolve_item(4172) == "T8_HEAD_LEATHER_SET3", (
        f"índice de pacote quebrou — 4172 deu {resolve_item(4172)!r}, "
        "esperado T8_HEAD_LEATHER_SET3 (voltou pro esquema `Index` do json?)"
    )
    cat = get_index_catalog()
    by_i = {r["i"]: r for r in cat}
    assert by_i[4172]["id"] == "T8_HEAD_LEATHER_SET3"
    assert by_i[562]["id"] == "T7_POTION_REVIVE", by_i[562]["id"]
    print(f"market_history self-check OK — {len(m)} índices; catálogo {len(cat)}; "
          f"4172 -> {resolve_item(4172)}")
