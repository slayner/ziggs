"""Comandos gerais: /avatar e /banner."""
import os, re, time
import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from typing import Optional

from i18n import t
from localization import loc

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# Cache de config de comandos por guild: {guild_id: (config, timestamp)}
_cmd_cache: dict[int, tuple[dict, float]] = {}
_CMD_TTL = 60  # segundos

# Placeholders selecionáveis no painel de permissões do site, ao lado dos
# cargos reais do servidor — ver update_command_roles em app/api/routes/auth.py.
EVERYONE = "everyone"
ADMIN = "admin"

_MENTION_RE = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")


async def _guild_command_config(guild_id: int) -> dict:
    """{"disabled": frozenset[str], "command_roles": dict[str, list[str]], "language": str} (cache 60s)."""
    now = time.monotonic()
    cached = _cmd_cache.get(guild_id)
    if cached and (now - cached[1]) < _CMD_TTL:
        return cached[0]
    empty = {"disabled": frozenset(), "command_roles": {}, "language": "pt"}
    if not SITE_URL or not API_SECRET:
        return empty
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{SITE_URL}/bot/guild-commands/{guild_id}",
                headers={"Authorization": f"Bearer {API_SECRET}"},
                timeout=aiohttp.ClientTimeout(total=3),
            )
            if r.status == 200:
                data = await r.json()
                cfg = {
                    "disabled": frozenset(data.get("disabled", [])),
                    "command_roles": data.get("command_roles", {}),
                    "language": data.get("language") or "pt",
                }
                _cmd_cache[guild_id] = (cfg, now)
                return cfg
    except Exception:
        pass
    return empty


async def guild_lang_for(guild_id: int) -> str:
    """Versão sem Interaction — usada por comandos de prefixo (ctx.guild.id)."""
    cfg = await _guild_command_config(guild_id)
    return cfg["language"]


async def guild_lang(interaction: discord.Interaction) -> str:
    """Idioma configurado no site pra esse servidor (default "pt"), reaproveitando
    o mesmo cache de 60s de _guild_command_config — sem round-trip extra."""
    if not interaction.guild_id:
        return "pt"
    return await guild_lang_for(interaction.guild_id)


async def _access_status(guild_id: int, member: discord.abc.User, name: str) -> tuple[str, str]:
    """('ok'|'disabled'|'forbidden', idioma) — a regra em si, sem mandar nenhuma
    resposta (quem chama decide como recusar: interaction.response ou ctx.send)."""
    cfg = await _guild_command_config(guild_id)
    lang = cfg["language"]
    if name in cfg["disabled"]:
        return "disabled", lang

    allowed: list[str] = cfg["command_roles"].get(name) or []
    if not allowed or EVERYONE in allowed:
        return "ok", lang

    if isinstance(member, discord.Member):
        if ADMIN in allowed and member.guild_permissions.administrator:
            return "ok", lang
        member_role_ids = {str(r.id) for r in member.roles}
        if member_role_ids & set(allowed):
            return "ok", lang

    return "forbidden", lang


async def check_command_access(interaction: discord.Interaction, name: str) -> bool:
    """Checa se o comando `name` pode ser executado nesta interação. Responde com
    a recusa (ephemeral) e retorna False se bloqueado — senão não responde nada
    e retorna True, deixando o comando seguir normalmente."""
    if not interaction.guild_id:
        return True
    status, lang = await _access_status(interaction.guild_id, interaction.user, name)
    if status == "ok":
        return True
    key = "cmd_disabled" if status == "disabled" else "no_permission"
    await interaction.response.send_message(t(lang, key), ephemeral=True)
    return False


async def check_command_access_ctx(ctx: commands.Context, name: str) -> bool:
    """Mesma checagem de check_command_access, mas pra comandos de prefixo
    (ctx.send em vez de interaction.response)."""
    if not ctx.guild:
        return True
    status, lang = await _access_status(ctx.guild.id, ctx.author, name)
    if status == "ok":
        return True
    key = "cmd_disabled" if status == "disabled" else "no_permission"
    await ctx.send(t(lang, key))
    return False


async def resolve_user_or_guild(
    interaction: Interaction,
    raw: Optional[str],
    *,
    allow_guild: bool = False,
    fuzzy: bool = True,
) -> discord.Member | discord.User | discord.Guild | None:
    """Resolve um alvo digitado livremente: @menção, ID (de usuário, ou de
    servidor quando allow_guild=True), nome de usuário, nome global, ou
    apelido no servidor. `fuzzy=False` restringe a busca por texto a match
    EXATO (sem o fallback "contém") — usado quando precisamos ter certeza de
    que o texto é mesmo uma referência a alguém do Discord, e não, por
    coincidência, um nick de Albion parecido com o nome de um membro.

    Sem texto nenhum, devolve quem chamou o comando. None se não resolver."""
    if raw is None or not raw.strip():
        return interaction.user
    raw = raw.strip()

    m = _MENTION_RE.fullmatch(raw)
    snowflake = m.group(1) if m else (raw if raw.isdigit() else None)

    if snowflake:
        if allow_guild:
            guild = interaction.client.get_guild(int(snowflake))
            if guild:
                return guild
        if interaction.guild:
            member = interaction.guild.get_member(int(snowflake))
            if member:
                return member
        try:
            return await interaction.client.fetch_user(int(snowflake))
        except (discord.NotFound, discord.HTTPException):
            return None

    if not interaction.guild:
        return None
    # "@fulano" digitado à mão (sem escolher a sugestão de menção do Discord)
    # continua sendo uma referência clara ao usuário — trata igual a "fulano".
    nl = raw.lstrip("@").lower()
    members = interaction.guild.members
    for member in members:
        if member.name.lower() == nl or (member.global_name or "").lower() == nl or (member.nick or "").lower() == nl:
            return member
    if not fuzzy:
        return None
    for member in members:
        if nl in member.name.lower() or nl in (member.global_name or "").lower() or nl in (member.nick or "").lower():
            return member
    return None


def dedupe_members(
    users: list[discord.Member | discord.User],
    roles: list[discord.Role],
) -> list[discord.Member]:
    """Junta menções diretas de usuário com membros de cargos mencionados, sem
    repetir ninguém e sem incluir bots — usado por comandos que aceitam
    múltiplos alvos de uma vez (ex.: /addmoney)."""
    seen: dict[int, discord.Member] = {}
    for u in users:
        if isinstance(u, discord.Member) and not u.bot:
            seen[u.id] = u
    for role in roles:
        for m in role.members:
            if not m.bot:
                seen[m.id] = m
    return list(seen.values())


def extract_mention_targets(guild: discord.Guild, text: str) -> list[discord.Member]:
    """Mesma ideia de dedupe_members, mas a partir de um texto livre (opção
    string de slash command, que não tem .mentions/.role_mentions prontos como
    uma Message tem) — extrai @menções e @cargos via regex e expande."""
    users = [guild.get_member(int(m.group(1))) for m in _MENTION_RE.finditer(text)]
    roles = [guild.get_role(int(m.group(1))) for m in _ROLE_MENTION_RE.finditer(text)]
    return dedupe_members([u for u in users if u], [r for r in roles if r])


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="avatar", description=loc("Mostra o avatar de um usuário ou servidor", "cmd_desc_avatar"))
    @app_commands.describe(alvo=loc("ID de servidor, @menção, ID/nome de usuário ou apelido (padrão: você mesmo)", "opt_desc_avatar_banner_alvo"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"))
    async def avatar(self, interaction: Interaction, alvo: Optional[str] = None) -> None:
        if not await check_command_access(interaction, "avatar"):
            return
        lang = await guild_lang(interaction)

        target = await resolve_user_or_guild(interaction, alvo, allow_guild=True)
        if target is None:
            await interaction.response.send_message(t(lang, "not_found_target", alvo=alvo), ephemeral=True)
            return

        if isinstance(target, discord.Guild):
            if not target.icon:
                await interaction.response.send_message(t(lang, "guild_no_icon", name=target.name), ephemeral=True)
                return
            embed = discord.Embed(color=discord.Color.blurple())
            embed.set_author(name=target.name, icon_url=target.icon.url)
            embed.set_image(url=target.icon.url)
            await interaction.response.send_message(embed=embed)
            return

        user = target
        url = user.display_avatar.url
        embed = discord.Embed(color=getattr(user, "color", discord.Color.blurple()))
        embed.set_author(name=str(user), icon_url=url)
        embed.set_image(url=url)
        # Link para avatar global quando em servidor (display_avatar pode ser server-specific)
        if isinstance(user, discord.Member) and user.avatar and user.avatar.url != url:
            embed.description = t(lang, "global_avatar_link", url=user.avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="banner", description=loc("Mostra o banner de um usuário ou servidor", "cmd_desc_banner"))
    @app_commands.describe(alvo=loc("ID de servidor, @menção, ID/nome de usuário ou apelido (padrão: você mesmo)", "opt_desc_avatar_banner_alvo"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"))
    async def banner(self, interaction: Interaction, alvo: Optional[str] = None) -> None:
        if not await check_command_access(interaction, "banner"):
            return
        lang = await guild_lang(interaction)

        target = await resolve_user_or_guild(interaction, alvo, allow_guild=True)
        if target is None:
            await interaction.response.send_message(t(lang, "not_found_target", alvo=alvo), ephemeral=True)
            return

        if isinstance(target, discord.Guild):
            if not target.banner:
                await interaction.response.send_message(t(lang, "no_banner", name=target.name), ephemeral=True)
                return
            embed = discord.Embed(color=discord.Color.blurple())
            embed.set_author(name=target.name, icon_url=target.icon.url if target.icon else None)
            embed.set_image(url=target.banner.url)
            await interaction.response.send_message(embed=embed)
            return

        # fetch_user traz o banner (objeto de cache não inclui)
        fetched = await self.bot.fetch_user(target.id)
        if not fetched.banner:
            name = target.display_name if isinstance(target, discord.Member) else target.name
            await interaction.response.send_message(t(lang, "no_banner", name=name), ephemeral=True)
            return
        embed = discord.Embed(color=getattr(target, "color", discord.Color.blurple()))
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        embed.set_image(url=fetched.banner.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
