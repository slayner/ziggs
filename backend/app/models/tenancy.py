"""
Tenancy & identidade.

`guilds` é o tenant (1 servidor de Discord = 1 guilda de Albion). Quase toda
tabela carrega `guild_id` para isolar dados — o equivalente central ao
"1 SQLite por guild" do bot antigo.

`users` é a identidade GLOBAL do Discord (pode estar em várias guildas). O vínculo
com o jogo (nick in-game) vive em `guild_members`, porque um mesmo Discord pode
ter nicks/cargos diferentes por servidor.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, Snowflake, json_type, pk


class PremiumTier(str, enum.Enum):
    FREE = "free"
    PREMIUM = "premium"


class Guild(Base, TimestampMixin):
    __tablename__ = "guilds"

    # PK = guild id do Discord (snowflake).
    id: Mapped[int] = mapped_column(Snowflake, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(255))

    premium_tier: Mapped[PremiumTier] = mapped_column(
        Enum(PremiumTier, name="premium_tier"),
        default=PremiumTier.FREE,
        nullable=False,
    )
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # In-game: a guilda de Albion à qual este servidor pertence (para /register
    # validar se o jogador está na guilda certa).
    albion_guild_id: Mapped[str | None] = mapped_column(String(64))
    albion_guild_name: Mapped[str | None] = mapped_column(String(255))

    # Aliança da guilda acima — descoberta sozinha (sem endpoint dedicado): toda
    # vez que um membro da guilda passa por /register, a API já devolve o
    # AllianceId/Name dele, e como aliança é atributo da guilda, isso já basta
    # pra preencher os campos abaixo sem nenhuma chamada extra à API da Albion.
    albion_alliance_id: Mapped[str | None] = mapped_column(String(64))
    albion_alliance_name: Mapped[str | None] = mapped_column(String(255))

    bot_present: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Saldo do banco da guilda em prata. Pode ficar negativo (ex.: regear pago sem
    # tab suficiente). Debitado automaticamente ao finalizar eventos com regear.
    bank_balance: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False
    )

    # Configurações por servidor (cargos, canais, % de tax/scout/logger, etc.).
    # Espelha o `economy_config` do bot — fica em JSONB para evoluir sem migração.
    settings: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    @property
    def is_premium(self) -> bool:
        if self.premium_tier is not PremiumTier.PREMIUM:
            return False
        if self.premium_until is None:
            return True
        return self.premium_until > datetime.now(self.premium_until.tzinfo)


class User(Base, TimestampMixin):
    """Identidade global do Discord (independente de guilda)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Snowflake, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    global_name: Mapped[str | None] = mapped_column(String(255))
    avatar: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discord_access_token: Mapped[str | None] = mapped_column(String(512))
    current_guild_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    # Preferências pessoais globais (não-por-guilda), ex.: {"focus_efficiency":
    # {familyKey: valor}} da calculadora de craft. Ver app/api/routes/craft.py.
    craft_settings: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    # Perfil customizado (ver app/services/user_profile.py) — desbloqueado só
    # depois de verificar um personagem via /claims (RegisteredCharacter).
    # Aplicado ao perfil de jogador de CADA personagem verificado desse user.
    profile_theme: Mapped[str | None] = mapped_column(String(16))
    profile_avatar_path: Mapped[str | None] = mapped_column(String(255))
    profile_banner_path: Mapped[str | None] = mapped_column(String(255))
    profile_media_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuildMember(Base, TimestampMixin):
    __tablename__ = "guild_members"
    __table_args__ = (UniqueConstraint("guild_id", "user_id"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    in_game_name: Mapped[str | None] = mapped_column(String(64), index=True)
    linked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_roles: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_roles: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    # IDs das roles Discord do membro neste servidor (strings, snowflakes)
    discord_role_ids: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    # True se o membro tem MANAGE_GUILD no servidor (admin/owner) → acesso total
    is_guild_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class GuildRolePermission(Base, TimestampMixin):
    """Mapeia um cargo Discord → permissões no site."""
    __tablename__ = "guild_role_permissions"
    __table_args__ = (UniqueConstraint("guild_id", "discord_role_id"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_role_id: Mapped[int] = mapped_column(Snowflake, nullable=False)
    discord_role_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # {"events.view": true, "events.create": false, "comps.view": true, ...}
    permissions: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
