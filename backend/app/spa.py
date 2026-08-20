"""Serving do frontend (SPA) pelo backend, com OG tags POR ROTA.

Por quê: o canal nº 1 de compartilhamento do site é o chat do Discord, e o
crawler de embeds lê as meta tags da URL COLADA — o link natural que o usuário
copia da barra (/k3j9xq2, /am/Fulano, /events/...). O embed rico de batalha já
existia em /battles/embed/{code}, mas ninguém cola esse link; aqui injetamos
as mesmas tags direto na rota natural.

Opt-in por presença: só ativa se frontend/dist/index.html existir (build do
Vite). Em dev com Vite server, nada muda. Registrado DEPOIS de todos os
routers — o catch-all só apanha o que nenhuma rota da API reconheceu.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

_ROOT = Path(__file__).resolve().parents[2]
_DIST = _ROOT / "frontend" / "dist"

_BATTLE_CODE_RE = re.compile(r"^[a-z0-9]{7}$")
_BATTLE_IDS_RE = re.compile(r"^\d+(?:,\d+)*$")
_PLAYER_RE = re.compile(r"^(am|as|eu)/([^/]+)$")
_EVENT_RE = re.compile(r"^(?:eventos|events)/(\d+)/(\d+)(?:/(?:escalacao|escalation))?$")
_PUBLIC_EVENT_RE = re.compile(r"^e/[A-Za-z0-9_-]{32}$")

_REGION_NAME = {"am": "Americas", "as": "Asia", "eu": "Europe"}


async def _og_for_path(path: str, query: str = "") -> tuple[str, str, str | None]:
    """(title, description, image_url|None) da rota — só com a URL, sem DB.

    Batalha por código busca o resumo no banco: /{code} é tanto a URL pública
    da página quanto a URL que o Discord lê para montar o embed. IDs crus
    (/123, /multi?ids=123,456) também resolvem pro grupo correspondente quando
    a batalha já está na base — senão OG genérico (a página SPA resolve na
    hora via /battles/resolve e troca a URL pro código curto).
    """
    m = _PLAYER_RE.match(path)
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(2))
        return (f"{name} · Albion {_REGION_NAME[m.group(1)]}",
                "", None)

    m = _EVENT_RE.match(path)
    if m:
        return (f"Evento #{m.group(2)} — Escalação",
                "", None)

    if _PUBLIC_EVENT_RE.match(path):
        return ("Escalação", "", None)

    # IDs crus do Albion: /123 ou /multi?ids=123,456
    albion_ids: list[str] = []
    if path == "multi":
        ids_param = (parse_qs(query).get("ids") or [""])[0]
        albion_ids = [s.strip() for s in ids_param.split(",") if s.strip()]
    elif _BATTLE_IDS_RE.match(path):
        albion_ids = path.split(",")

    if albion_ids:
        title, image = await _battle_summary_by_albion_ids(albion_ids)
        return (title or "Batalha", "", image)

    if _BATTLE_CODE_RE.match(path):
        title, image = await _battle_summary(path)
        return (title, "", image)

    if path.startswith(("guild/", "alliance/")):
        return ("Perfil de guilda — Ziggs", "", None)

    return ("", "", None)  # rota sem OG específico → index como está


async def _group_summary(db, battle_ids: list[int]) -> tuple[str, str | None]:
    """Título + URL do PNG de preview dado os battle_ids internos do grupo.
    Se alguma batalha ainda é light (não deep-processada), não gera imagem —
    o embed do Discord seria gerado com dados vazios."""
    from app.config import get_settings
    from app.models.battles import Battle
    from app.services import battle_groups
    from app.api.routes.battles import _factions_summary
    from sqlalchemy import select

    if not battle_ids:
        return ("", None)
    battles = (await db.scalars(select(Battle).where(Battle.id.in_(battle_ids)))).all()
    if not battles:
        return ("", None)
    # Se alguma batalha do grupo ainda é light, não tem dados de factions/participants
    # — OG genérico (sem imagem). O embed só vale a pena quando a batalha está pronta.
    if any(b.processing_tier != "deep" for b in battles):
        return ("", None)
    b = battles[0]
    # Tags das factions vs (mesmo formato da imagem de preview)
    all_factions: dict[str, dict] = {}
    for bid in battle_ids:
        for f in await _factions_summary(db, bid):
            key = f["alliance_name"] or f["guild_name"]
            if key in all_factions:
                all_factions[key]["kills"] += f["kills"]
            else:
                all_factions[key] = dict(f)
    top = sorted(all_factions.values(), key=lambda r: r["kills"], reverse=True)[:4]
    tags = []
    for f in top:
        tag = f"[{f['alliance_name']}]" if f["alliance_name"] else f["guild_name"]
        tags.append(tag[:12])
    title = "  vs  ".join(tags) if tags else f"{b.players_total} players · {b.kill_count} kills"
    group = await battle_groups.get_or_create_group(db, battle_ids)
    image = f"{get_settings().frontend_url}/battles/preview/{group.public_id}.png"
    return (title, image)


async def _battle_summary(public_id: str) -> tuple[str, str | None]:
    """Título + URL do PNG de preview da batalha (por código curto)."""
    try:
        from app.db import AsyncSessionLocal
        from app.services import battle_groups

        async with AsyncSessionLocal() as db:
            ids = await battle_groups.get_group_battle_ids(db, public_id)
            if not ids:
                return ("", None)
            return await _group_summary(db, ids)
    except Exception:
        return ("", None)  # DB fora/erro → OG genérico, página abre igual


async def _battle_summary_by_albion_ids(albion_ids: list[str]) -> tuple[str, str | None]:
    """Título + URL do PNG de preview dado albion_ids crus (formato /multi?ids=
    ou /123,456). Resolve só batalhas JÁ na base — não bate na API do Albion
    (SSR precisa ser rápido e sem rede externa). Se alguma não estiver na base,
    devolve OG genérico; a página SPA resolve via /battles/resolve na hora."""
    try:
        from app.db import AsyncSessionLocal
        from app.models.battles import Battle
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            battles = (await db.scalars(
                select(Battle).where(Battle.albion_id.in_(albion_ids))
            )).all()
            if len(battles) != len(albion_ids):
                return ("", None)
            return await _group_summary(db, [b.id for b in battles])
    except Exception:
        return ("", None)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


async def _inject_og(index_html: str, path: str, query: str = "") -> str:
    title, desc, image = await _og_for_path(path, query)
    if not title and not image:
        return index_html
    html = index_html
    html = re.sub(r"<title>[^<]*</title>", f"<title>{_esc(title)}</title>", html, count=1)
    html = re.sub(r'(property="og:title" content=")[^"]*(")', rf"\g<1>{_esc(title)}\g<2>", html, count=1)
    html = re.sub(r'(property="og:description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
    html = re.sub(r'(name="description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
    if image:
        # Remove o og:image padrão (/logo.png) pra o crawler não pegar o 1º.
        # Sem og:image:width/height e sem twitter:card — o Discord busca a
        # imagem e usa as dimensões reais sem forçar um aspect ratio (que
        # amassa a tira 3.3:1 no card grande esperado 1.91:1).
        html = re.sub(r'<meta\s+property="og:image"[^>]*/?>', "", html)
        html = re.sub(r'<meta\s+name="twitter:card"[^>]*/?>', "", html)
        tags = [
            f'<meta property="og:image" content="{_esc(image)}" />',
            f'<meta property="og:image:type" content="image/png" />',
            '<meta name="twitter:card" content="summary_large_image" />',
        ]
        html = html.replace("</head>", "\n".join(tags) + "\n</head>", 1)
    return html


def _preview_dimensions(image_url: str) -> tuple[int, int]:
    """Largura/altura do PNG de preview. A largura é fixa (600); a altura
    vem do arquivo renderizado. Se não conseguir ler, usa o mínimo do
    Discord (315px)."""
    try:
        from pathlib import Path
        # Extrai o public_id da URL: .../battles/preview/{id}.png
        name = image_url.rsplit("/", 1)[-1]  # {id}.png
        public_id = name.replace(".png", "")
        cache_dir = Path(__file__).resolve().parents[1] / "data" / "battle_preview_cache"
        path = cache_dir / f"{public_id}.png"
        if path.exists():
            from PIL import Image as PILImage
            with PILImage.open(path) as img:
                return (img.width, img.height)
    except Exception:
        pass
    return (600, 315)


def install(app: FastAPI) -> None:
    """Registra o serving da SPA se frontend/dist existir. Chamar por ÚLTIMO."""
    index_file = _DIST / "index.html"
    if not index_file.is_file():
        return  # sem build → backend segue API-only (dev com Vite server)

    docs_file = _DIST / "docs.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):  # noqa: ANN202
        # Arquivo estático real do dist (assets/, data/, favicon…)?
        if full_path:
            f = (_DIST / full_path).resolve()
            if f.is_file() and _DIST in f.parents:
                return FileResponse(f)
        # O mesmo processo pode servir o site e docs.*. Não inferimos o host
        # por prefixo: o valor explícito evita capturar staging ou outro
        # subdomínio por acidente. Assets já retornaram acima; qualquer rota
        # restante do host de docs cai no entrypoint correto.
        from app.config import get_settings
        docs_host = get_settings().docs_host.strip().lower()
        request_host = request.headers.get("host", "").split(":", 1)[0].lower()
        if docs_file.is_file() and docs_host and request_host == docs_host:
            return FileResponse(docs_file)
        # Frontend deploys replace dist without restarting the API process.
        return HTMLResponse(await _inject_og(index_file.read_text(encoding="utf-8"), full_path, request.url.query),
                            headers={"Cache-Control": "no-cache"})
