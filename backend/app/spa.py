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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

_ROOT = Path(__file__).resolve().parents[2]
_DIST = _ROOT / "frontend" / "dist"

_BATTLE_CODE_RE = re.compile(r"^[a-z0-9]{7}$")
_BATTLE_IDS_RE = re.compile(r"^\d+(?:,\d+)*$")
_PLAYER_RE = re.compile(r"^(am|as|eu)/([^/]+)$")
_GUILD_RE = re.compile(r"^guild/([^/]+)$")
_ALLIANCE_RE = re.compile(r"^alliance/([^/]+)$")
_EVENT_RE = re.compile(r"^(?:eventos|events)/(\d+)/(\d+)(?:/(?:escalacao|escalation))?$")
_PUBLIC_EVENT_RE = re.compile(r"^e/[A-Za-z0-9_-]{32}$")

_DISCORD_PREFETCH_WAIT_SECONDS = 6


async def _og_for_path(
    path: str, query: str = "", *, wait_for_player_preview: bool = False,
) -> tuple[str, str, str | None]:
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
        from app.config import get_settings
        name = unquote(m.group(2))
        region_code = m.group(1)
        region_map = {"am": "americas", "eu": "europe", "as": "asia"}
        region = region_map.get(region_code, "americas")
        image_version = None
        try:
            from app.api.routes.players import prefetch_player_preview
            image_version = await prefetch_player_preview(
                region,
                name,
                wait_seconds=_DISCORD_PREFETCH_WAIT_SECONDS if wait_for_player_preview else 0,
            )
        except Exception:
            pass
        s = get_settings()
        image_url = f"{s.frontend_url}/players/embed/{region}/{quote(name, safe='')}.png"
        if image_version:
            image_url += f"?v={image_version}"
        return (f"{region_code.upper()}  ·  {name}", "", image_url)

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

    m = _GUILD_RE.match(path)
    if m:
        from app.api.routes.profiles import guild_preview_metadata, _resolve_region_for_guild
        from app.config import get_settings
        from app.models.guild_profiles import GuildProfile
        from app.db import AsyncSessionLocal
        from app.services.profile_warmer import request_refresh
        from sqlalchemy import select
        metadata = await guild_preview_metadata(m.group(1))
        async with AsyncSessionLocal() as db:
            region = await _resolve_region_for_guild(db, m.group(1))
            if region is not None:
                from app.services.albion_gate import LINK_PROFILE
                gp = await db.scalar(select(GuildProfile).where(GuildProfile.albion_id == m.group(1)))
                if gp is None:
                    db.add(GuildProfile(albion_id=m.group(1), name=m.group(1), region=region,
                                        refresh_requested_at=datetime.now(timezone.utc), refresh_priority=LINK_PROFILE))
                    await db.commit()
                    request_refresh()
        if metadata is not None and region is not None:
            name, image_version = metadata
            s = get_settings()
            prefix = {"americas": "AM", "europe": "EU", "asia": "AS"}[region]
            return (f"{prefix}  ·  {name}", "", f"{s.frontend_url}/public/guilds/embed/{quote(m.group(1), safe='')}.png?v={image_version}")

    m = _ALLIANCE_RE.match(path)
    if m:
        from app.config import get_settings
        from app.api.routes.profiles import alliance_preview_metadata, _resolve_region_for_alliance
        from app.models.guild_profiles import AllianceProfile
        from app.db import AsyncSessionLocal
        from app.services.profile_warmer import request_refresh
        from sqlalchemy import select
        s = get_settings()
        metadata = await alliance_preview_metadata(m.group(1))
        async with AsyncSessionLocal() as db:
            region = await _resolve_region_for_alliance(db, m.group(1))
            if region is not None:
                from app.services.albion_gate import LINK_PROFILE
                ap = await db.scalar(select(AllianceProfile).where(AllianceProfile.albion_id == m.group(1)))
                if ap is None:
                    db.add(AllianceProfile(albion_id=m.group(1), name=m.group(1), region=region,
                                           refresh_requested_at=datetime.now(timezone.utc), refresh_priority=LINK_PROFILE))
                    await db.commit()
                    request_refresh()
        if metadata is not None and region is not None:
            name, image_version = metadata
            prefix = {"americas": "AM", "europe": "EU", "asia": "AS"}[region]
            return (f"{prefix}  ·  [{name}]", "", f"{s.frontend_url}/public/alliances/embed/{quote(m.group(1), safe='')}.png?v={image_version}")

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
    from app.api.routes.battles import battle_faction_title
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
    title = await battle_faction_title(db, battle_ids)
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


async def _inject_og(
    index_html: str, path: str, query: str = "", *, wait_for_player_preview: bool = False,
) -> str:
    title, desc, image = await _og_for_path(
        path, query, wait_for_player_preview=wait_for_player_preview,
    )
    if not title and not image:
        return index_html
    html = index_html
    if title:
        html = re.sub(r"<title>[^<]*</title>", f"<title>{_esc(title)}</title>", html, count=1)
        html = re.sub(r'(property="og:title" content=")[^"]*(")', rf"\g<1>{_esc(title)}\g<2>", html, count=1)
    else:
        html = re.sub(r"<title>[^<]*</title>", "<title></title>", html, count=1)
        html = re.sub(r'<meta\s+property="og:title"[^>]*/?>', "", html)
        html = re.sub(r'<meta\s+property="og:site_name"[^>]*/?>', "", html)
    if desc:
        html = re.sub(r'(property="og:description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
        html = re.sub(r'(name="description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
    else:
        html = re.sub(r'<meta\s+property="og:description"[^>]*/?>', "", html)
        html = re.sub(r'<meta\s+name="description"[^>]*/?>', "", html)
    if image:
        # Remove o og:image padrão (/logo.png) pra o crawler não pegar o 1º.
        # Sem og:image:width/height e sem twitter:card — o Discord busca a
        # imagem e usa as dimensões reais sem forçar um aspect ratio (que
        # amassa a tira 3.3:1 no card grande esperado 1.91:1).
        html = re.sub(r'<meta\s+property="og:image"[^>]*/?>', "", html)
        html = re.sub(r'<meta\s+property="og:site_name"[^>]*/?>', "", html)
        html = re.sub(r'<meta\s+name="twitter:card"[^>]*/?>', "", html)
        tags = [
            f'<meta property="og:image" content="{_esc(image)}" />',
            '<meta property="og:image:type" content="image/png" />',
            '<meta property="og:image:width" content="1200" />',
            '<meta property="og:image:height" content="436" />',
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
        is_discord_crawler = "discordbot" in request.headers.get("user-agent", "").lower()
        return HTMLResponse(await _inject_og(
            index_file.read_text(encoding="utf-8"),
            full_path,
            request.url.query,
            wait_for_player_preview=is_discord_crawler,
        ),
                            headers={"Cache-Control": "no-cache"})
