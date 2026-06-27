"""Configuração via variáveis de ambiente (.env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Banco central Postgres.
    database_url: str = "postgresql+psycopg://ziggs:ziggs@localhost:5432/ziggs"

    # Discord OAuth (login SÓ por Discord).
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/discord/callback"
    discord_scopes: str = "identify guilds"
    discord_bot_token: str = ""

    # Front-end (destino do redirect pós-login).
    frontend_url: str = "http://localhost:5173"

    # Sessão do site (cookie assinado).
    secret_key: str = "dev-only-change-me"
    session_cookie_name: str = "ziggs_session"
    session_max_age: int = 60 * 60 * 24 * 7  # 7 dias

    environment: str = "development"
    bot_api_secret: str = "dev-bot-secret-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
