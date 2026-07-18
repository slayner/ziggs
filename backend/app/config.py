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
    companion_api_secret: str = "dev-companion-secret-change-me"

    # Kill-switch dos fetchers de background (battle/player/price trackers,
    # profile_warmer, claim/registration checker, weapon_stats, snapshots…).
    # True = NÃO inicia nenhuma task de polling no lifespan — serve pra rodar
    # o servidor em dado móvel limitado sem que os trackers suguem a rede.
    # Rotas sob demanda (UI) continuam funcionando, só disparam request externo
    # quando você clica. Setar no .env: DISABLE_BACKGROUND_FETCHERS=true
    disable_background_fetchers: bool = False

    def model_post_init(self, __context) -> None:
        # Trava de segurança: fora de development, subir com os secrets de
        # exemplo tornaria o cookie de sessão FORJÁVEL (os defaults estão
        # públicos no repo) e o /bot/* aberto pra qualquer um. Falha no boot
        # em vez de rodar silenciosamente inseguro.
        if self.environment != "development":
            weak = {"dev-only-change-me", "dev-bot-secret-change-me", "dev-companion-secret-change-me", ""}
            if self.secret_key in weak or self.bot_api_secret in weak or self.companion_api_secret in weak:
                raise RuntimeError(
                    "SECRET_KEY/BOT_API_SECRET/COMPANION_API_SECRET com valor default de dev em ambiente "
                    f"{self.environment!r} — defina secrets reais no .env antes de subir."
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()
