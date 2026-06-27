"""
Cliente OAuth2 do Discord — login SÓ por Discord.

Fluxo:
  1. build_authorize_url(state) -> manda o usuário pro Discord autorizar.
  2. Discord volta com ?code= -> exchange_code(code) troca por access_token.
  3. fetch_user(token) / fetch_guilds(token) leem o perfil e os servidores.

As funções aceitam um httpx.Client injetável para teste (sem rede real).
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.config import get_settings

API = "https://discord.com/api"
AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = f"{API}/oauth2/token"


def build_authorize_url(state: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.discord_client_id,
        "redirect_uri": s.discord_redirect_uri,
        "response_type": "code",
        "scope": s.discord_scopes,
        "state": state,
        "prompt": "consent",
    }
    return f"{AUTHORIZE}?{urlencode(params)}"


def exchange_code(code: str, client: httpx.Client | None = None) -> dict:
    s = get_settings()
    data = {
        "client_id": s.discord_client_id,
        "client_secret": s.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": s.discord_redirect_uri,
    }
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.post(
            TOKEN, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            client.close()


def _get(path: str, token: str, client: httpx.Client | None) -> dict | list:
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.get(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            client.close()


def fetch_user(token: str, client: httpx.Client | None = None) -> dict:
    """GET /users/@me — perfil do dono do token."""
    return _get("/users/@me", token, client)  # type: ignore[return-value]


def fetch_guilds(token: str, client: httpx.Client | None = None) -> list[dict]:
    """GET /users/@me/guilds — servidores em que o usuário está."""
    return _get("/users/@me/guilds", token, client)  # type: ignore[return-value]


def fetch_guild_member(guild_id: str, token: str, client: httpx.Client | None = None) -> dict:
    """GET /users/@me/guilds/{guild_id}/member — membro do usuário em um servidor."""
    return _get(f"/users/@me/guilds/{guild_id}/member", token, client)  # type: ignore[return-value]


def _bot_get(path: str, bot_token: str) -> dict | list:
    with httpx.Client(timeout=5) as c:
        r = c.get(f"{API}{path}", headers={"Authorization": f"Bot {bot_token}"})
        r.raise_for_status()
        return r.json()


def fetch_guild(guild_id: str, bot_token: str) -> dict:
    """GET /guilds/{guild_id} — retorna 200 se o bot está no servidor, 403/404 se não."""
    return _bot_get(f"/guilds/{guild_id}", bot_token)  # type: ignore[return-value]


def fetch_guild_roles(guild_id: str, bot_token: str) -> list[dict]:
    """GET /guilds/{guild_id}/roles — lista de cargos do servidor (usa bot token)."""
    return _bot_get(f"/guilds/{guild_id}/roles", bot_token)  # type: ignore[return-value]
