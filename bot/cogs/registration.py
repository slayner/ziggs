"""
Sistema de registro de jogadores.

Liga o usuário do Discord ao seu NICK de jogo (Albion).

Comandos:
  · /register <nick> [usuario] [cargo]
        Sem `usuario` → auto-cadastro: verifica se o jogador está na guild do servidor
        e atribui o cargo de membro automaticamente.
        Com `usuario` (council/logistic/caller/officer) → cadastro admin, igual ao anterior.

Tarefas automáticas:
  · Lembrete (a cada REMINDER_INTERVAL_HOURS) no economylogs com não-cadastrados.
  · Verificação de guilda (GUILD_CHECK_INTERVAL_HOURS) para auto-cadastrados: se o jogador
    saiu da guild, remove o cargo de membro mas mantém o link discord→nick.
"""
import os
import asyncio
from typing import Literal, Optional

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from utils import make_embed, send_err, send_ok, send_warn
import utils

import database
from database import (
    get_activated_guild_ids,
    is_server_activated,
    load_economy_config,
    get_configured_role_ids,
    register_user, get_registration, get_registered_user_ids,
    remove_all_registrations_for_user,
    register_self, get_self_registered_users, update_guild_membership,
)
from cogs.economy import has_configured_role

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

REMINDER_INTERVAL_HOURS = 6
GUILD_CHECK_INTERVAL_HOURS = 12
SCOUT_RELIABILITY_MIN = 2      # mínimo de eventos pagos como scout para ser "confiável"
_REMINDER_MAX_LIST = 50

_ALBION_HOST = 'gameinfo.albiononline.com'
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_API_TIMEOUT = 10


class RegistrationCog(commands.Cog):
    """Cadastro Discord → nick de jogo (renomeia só no /register admin)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def cog_load(self):
        self.reminder_loop.start()
        self.guild_check_loop.start()
        print("✓ Registration Cog carregada")

    def cog_unload(self):
        self.reminder_loop.cancel()
        self.guild_check_loop.cancel()
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    # ------------------------------------------------------------------
    # HTTP session (Albion API)
    # ------------------------------------------------------------------
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_API_TIMEOUT),
                headers={'User-Agent': _UA},
            )
        return self._session

    async def _fetch_json(self, url: str):
        try:
            s = await self._get_session()
            async with s.get(url) as r:
                if r.status != 200:
                    return None, f"HTTP {r.status}"
                return await r.json(content_type=None), None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    async def _verify_guild_membership(self, nick: str, guild_set: set) -> tuple[bool, str | None]:
        """Verifica se o jogador `nick` está em uma das guilds configuradas.
        Retorna (is_member, player_id | None)."""
        import urllib.parse
        if not guild_set:
            return False, None
        q = urllib.parse.quote(nick.strip())
        data, err = await self._fetch_json(
            f"https://{_ALBION_HOST}/api/gameinfo/search?q={q}")
        if not isinstance(data, dict):
            return False, None
        players = data.get('players') or []
        nl = nick.strip().lower()
        exact = [p for p in players if (p.get('Name') or '').lower() == nl]
        if not exact:
            return False, None
        for p in exact:
            if (p.get('GuildName') or '').lower() in guild_set:
                return True, p.get('Id')
        return False, None

    async def _get_player_guild(self, player_id: str) -> str | None:
        """Retorna o nome da guild atual do jogador via API (None se erro)."""
        if not player_id:
            return None
        data, _ = await self._fetch_json(
            f"https://{_ALBION_HOST}/api/gameinfo/players/{player_id}")
        if isinstance(data, dict):
            return (data.get('GuildName') or '').strip().lower() or None
        return None

    # ------------------------------------------------------------------
    # Permissão
    # ------------------------------------------------------------------
    async def _check_roles_or_reply(self, ctx: commands.Context, *role_keys: str) -> bool:
        if ctx.author.id == OWNER_ID:
            return True
        if await has_configured_role(ctx.author, *role_keys):
            return True
        await send_err(ctx, "Você não tem permissão para usar este comando.")
        return False

    # ------------------------------------------------------------------
    # /register  (admin + auto-cadastro)
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="register",
        description="Cadastra um nick no Albion. Sem @usuário = auto-registro (verifica guild).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        nick="Nick no Albion (auto-registro) ou nick do usuário a cadastrar (admin)",
        usuario="(Admin) Usuário do Discord a cadastrar",
        cargo="(Admin) Cargo a atribuir: trial ou membro",
    )
    async def register(self, ctx: commands.Context, nick: str,
                       usuario: Optional[discord.Member] = None,
                       cargo: Optional[Literal['trial', 'membro']] = None):
        await ctx.defer(ephemeral=True)

        nick = (nick or '').strip()
        if not nick:
            await send_err(ctx, "O nick não pode ficar vazio.")
            return
        if len(nick) > 32:
            await send_err(ctx, "O nick é muito longo (máx. 32 caracteres).")
            return

        if usuario is not None:
            # ---- Modo admin ----
            if not await self._check_roles_or_reply(
                ctx, 'role_council', 'role_logistic', 'role_caller', 'role_officer'
            ):
                return
            effective_cargo = cargo or 'membro'
            prev = await get_registration(usuario.id)
            await register_user(usuario.id, nick, registered_by=ctx.author.id)

            rename_note = await self._try_rename(usuario, nick)
            verb = "atualizado" if prev else "cadastrado"
            msg = f"✅ **{usuario.mention} {verb}** como `{nick}` ({effective_cargo})."
            if rename_note:
                msg += f"\n{rename_note}"

            mcog = self.bot.cogs.get('MentoriaCog')
            if mcog:
                role_note, post_note = await mcog.assign_and_post(usuario, effective_cargo)
                if role_note:
                    msg += f"\n{role_note}"
                msg += f"\n{post_note}"
            else:
                msg += "\n⚠️ (cog de mentoria não carregada — cargo/post não aplicados)"

            await ctx.send(msg, ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(ctx)
            except Exception:
                pass
            return

        # ---- Modo auto-registro ----
        cfg = await load_economy_config()
        guild_set = {g.strip().lower()
                     for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}
        if not guild_set:
            await send_err(ctx, "A guild deste servidor não está configurada. "
                                "Peça à staff para configurar em `/setup`.")
            return

        await ctx.send("🔍 Verificando sua conta no Albion…", ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

        is_member, _pid = await self._verify_guild_membership(nick, guild_set)
        if not is_member:
            guild_names = ', '.join(f'**{g}**' for g in guild_set)
            await ctx.send(
                f"❌ O personagem **{nick}** não foi encontrado nas guilds configuradas "
                f"({guild_names}).\n"
                f"Verifique o nick exato e tente novamente.",
                ephemeral=True,
            )
            try:
                await utils.schedule_ephemeral_from_ctx(ctx)
            except Exception:
                pass
            return

        prev = await get_registration(ctx.author.id)
        await register_self(ctx.author.id, nick)

        msg = f"✅ **Auto-registro concluído!** Seu nick `{nick}` foi vinculado."
        if prev:
            msg += f"\n*(cadastro anterior atualizado)*"

        mcog = self.bot.cogs.get('MentoriaCog')
        if mcog:
            try:
                role_note, post_note = await mcog.assign_and_post(ctx.author, 'membro')
                if role_note:
                    msg += f"\n{role_note}"
                msg += f"\n{post_note}"
            except Exception as e:
                msg += f"\n⚠️ Cargo/mentoria: {e}"
        else:
            msg += "\n⚠️ (cog de mentoria não carregada — cargo não aplicado automaticamente)"

        await ctx.send(msg, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helper de rename (admin)
    # ------------------------------------------------------------------
    async def _try_rename(self, member: discord.Member, nick: str) -> str | None:
        if member.guild and member.id == member.guild.owner_id:
            return "⚠️ (não renomeei: é o dono do servidor — o Discord não permite)"
        if (member.nick or "") == nick:
            return None
        try:
            await member.edit(nick=nick, reason="Registro de nick de jogo")
            return None
        except discord.Forbidden:
            return ("⚠️ (não consegui renomear: faltam permissões ou o cargo do bot "
                    "está abaixo do dele)")
        except discord.HTTPException as e:
            return f"⚠️ (falha ao renomear: {e})"

    # ------------------------------------------------------------------
    # /registerremove
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="registerremove",
        description="Remove todas as contas/nicks associados a um usuário do Discord.",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Usuário do Discord cujas contas serão removidas")
    async def registerremove(self, ctx: commands.Context, user: discord.Member):
        await ctx.defer(ephemeral=True)
        if not await self._check_roles_or_reply(
            ctx, 'role_council', 'role_logistic', 'role_caller', 'role_officer'
        ):
            return
        removed = await remove_all_registrations_for_user(user.id)
        reply = (f"✅ Removidas todas as contas registradas de {user.mention}."
                 if removed else f"ℹ️ Não havia contas registradas para {user.mention}.")
        await ctx.send(reply, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ==================================================================
    # Lembrete: usuários com cargo mas sem cadastro
    # ==================================================================
    @tasks.loop(hours=REMINDER_INTERVAL_HOURS)
    async def reminder_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._reminder_once()
                except Exception as e:
                    print(f"✗ reminder_loop [{gid}]: {e}")

    async def _reminder_once(self):
        cfg = await load_economy_config()
        chan_id = cfg.get('channel_economylogs')
        if not chan_id:
            return
        channel = self.bot.get_channel(chan_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        guild = channel.guild
        role_ids = await get_configured_role_ids()
        if not role_ids:
            return
        registered = await get_registered_user_ids()

        missing = []
        for member in guild.members:
            if member.bot:
                continue
            if member.id in registered:
                continue
            if any(r.id in role_ids for r in member.roles):
                missing.append(member)

        if not missing:
            return

        missing.sort(key=lambda m: m.display_name.lower())
        shown = missing[:_REMINDER_MAX_LIST]
        lines = [f"• {m.mention} (`{m.display_name}`)" for m in shown]
        if len(missing) > len(shown):
            lines.append(f"… e mais **{len(missing) - len(shown)}**.")

        embed = make_embed('warn', title="Jogadores com cargo mas SEM cadastro",
            desc=(f"**{len(missing)}** usuário(s) com cargo da guild ainda não foram "
                  f"cadastrados. Use `/register <nick>` (auto) ou `/register <nick> @user`.\n\n"
                  + "\n".join(lines)))
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print("✗ Sem permissão para postar lembrete no economylogs")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Verificação de saída da guild (auto-cadastrados)
    # ==================================================================
    @tasks.loop(hours=GUILD_CHECK_INTERVAL_HOURS)
    async def guild_check_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._guild_check_once(gid)
                except Exception as e:
                    print(f"✗ guild_check_loop [{gid}]: {e}")

    async def _guild_check_once(self, guild_id: int):
        """Para cada auto-cadastrado, verifica se ainda está na guild. Se saiu, remove cargo."""
        cfg = await load_economy_config()
        guild_set = {g.strip().lower()
                     for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}
        if not guild_set:
            return

        role_member_id = cfg.get('role_member') or cfg.get('role_lead')
        role_trial_id  = cfg.get('role_trial')

        users = await get_self_registered_users()
        if not users:
            return

        discord_guild = self.bot.get_guild(guild_id)

        import urllib.parse
        for u in users:
            uid  = u['user_id']
            nick = u.get('nick') or ''
            if not nick:
                continue

            # Evita spammar a API — pequeníssima pausa entre requests
            await asyncio.sleep(0.5)

            # Busca o personagem e verifica a guild
            try:
                q = urllib.parse.quote(nick.strip())
                data, _ = await self._fetch_json(
                    f"https://{_ALBION_HOST}/api/gameinfo/search?q={q}")
                if not isinstance(data, dict):
                    continue
                players = data.get('players') or []
                nl = nick.strip().lower()
                exact = [p for p in players if (p.get('Name') or '').lower() == nl]
                if not exact:
                    # Personagem não encontrado (pode ter sido deletado)
                    continue
                # Prefere o que está na nossa guild
                found_in_guild = any(
                    (p.get('GuildName') or '').lower() in guild_set for p in exact
                )
            except Exception as e:
                print(f"✗ guild_check_loop: erro ao verificar {nick}: {e}")
                continue

            await update_guild_membership(uid, found_in_guild)

            if not found_in_guild and discord_guild:
                member = discord_guild.get_member(uid)
                if member:
                    # Remove cargos de membro/trial mas mantém o link nick
                    roles_to_remove = []
                    if role_member_id:
                        r = discord_guild.get_role(role_member_id)
                        if r and r in member.roles:
                            roles_to_remove.append(r)
                    if role_trial_id:
                        r = discord_guild.get_role(role_trial_id)
                        if r and r in member.roles:
                            roles_to_remove.append(r)
                    if roles_to_remove:
                        try:
                            await member.remove_roles(
                                *roles_to_remove,
                                reason=f"Saiu da guild no jogo ({nick})",
                            )
                            print(f"✓ {nick} ({uid}) saiu da guild — cargos removidos")
                        except discord.Forbidden:
                            print(f"✗ Sem permissão para remover cargo de {uid}")
                        except discord.HTTPException as e:
                            print(f"✗ Erro ao remover cargo de {uid}: {e}")

    @guild_check_loop.before_loop
    async def before_guild_check_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))
