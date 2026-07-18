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

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

_ROOT = Path(__file__).resolve().parents[2]
_DIST = _ROOT / "frontend" / "dist"

_BATTLE_CODE_RE = re.compile(r"^[a-z0-9]{7}$")
_BATTLE_IDS_RE = re.compile(r"^\d+(?:,\d+)*$")
_PLAYER_RE = re.compile(r"^(am|as|eu)/([^/]+)$")
_EVENT_RE = re.compile(r"^(?:eventos|events)/(\d+)/(\d+)(?:/(?:escalacao|escalation))?$")

_REGION_NAME = {"am": "Americas", "as": "Asia", "eu": "Europe"}


def _og_for_path(path: str) -> tuple[str, str, str | None]:
    """(title, description, image_url|None) da rota — só com a URL, sem DB.

    Batalha por código busca o resumo no banco (é o link mais colado no
    Discord e o PNG de preview já existe); o resto deriva título da URL.
    """
    m = _PLAYER_RE.match(path)
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(2))
        return (f"{name} · Albion {_REGION_NAME[m.group(1)]}",
                "Perfil de jogador — batalhas, K/D e armas no Ziggs.", None)

    m = _EVENT_RE.match(path)
    if m:
        return (f"Evento #{m.group(2)} — Escalação",
                "Escalação e inscrições do evento no Ziggs.", None)

    if _BATTLE_IDS_RE.match(path) or path == "multi":
        return ("Batalha — Ziggs", "Detalhes da batalha: kills, fama e composições.", None)

    if _BATTLE_CODE_RE.match(path):
        title, image = _battle_summary(path)
        return (title, "Detalhes da batalha: kills, fama e composições.", image)

    if path.startswith(("guild/", "alliance/")):
        return ("Perfil de guilda — Ziggs", "Batalhas e jogadores da guilda no Albion Online.", None)

    return ("", "", None)  # rota sem OG específico → index como está


def _battle_summary(public_id: str) -> tuple[str, str | None]:
    """Título + URL do PNG de preview (mesma lógica do /battles/embed/)."""
    try:
        from app.config import get_settings
        from app.db import SessionLocal
        from app.models.battles import Battle
        from app.services import battle_groups

        db = SessionLocal()
        try:
            ids = battle_groups.get_group_battle_ids(db, public_id)
            if not ids:
                return ("", None)
            b = db.get(Battle, ids[0])
            if not b:
                return ("", None)
            title = f"{b.players_total} players · {b.kill_count} kills"
            if b.cluster:
                title += f" · {b.cluster}"
            image = f"{get_settings().frontend_url}/battles/preview/{public_id}.png"
            return (title, image)
        finally:
            db.close()
    except Exception:
        return ("", None)  # DB fora/erro → OG genérico, página abre igual


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _inject_og(index_html: str, path: str) -> str:
    title, desc, image = _og_for_path(path)
    if not title:
        return index_html
    html = index_html
    html = re.sub(r"<title>[^<]*</title>", f"<title>{_esc(title)} · Ziggs</title>", html, count=1)
    html = re.sub(r'(property="og:title" content=")[^"]*(")', rf"\g<1>{_esc(title)}\g<2>", html, count=1)
    html = re.sub(r'(property="og:description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
    html = re.sub(r'(name="description" content=")[^"]*(")', rf"\g<1>{_esc(desc)}\g<2>", html, count=1)
    if image:
        html = html.replace("</head>", f'<meta property="og:image" content="{_esc(image)}" />\n</head>', 1)
    return html


def install(app: FastAPI) -> None:
    """Registra o serving da SPA se frontend/dist existir. Chamar por ÚLTIMO."""
    index_file = _DIST / "index.html"
    if not index_file.is_file():
        return  # sem build → backend segue API-only (dev com Vite server)

    index_html = index_file.read_text(encoding="utf-8")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):  # noqa: ANN202
        # Arquivo estático real do dist (assets/, data/, favicon…)?
        if full_path:
            f = (_DIST / full_path).resolve()
            if f.is_file() and _DIST in f.parents:
                return FileResponse(f)
        return HTMLResponse(_inject_og(index_html, full_path))
