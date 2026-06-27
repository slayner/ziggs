"""
/stats — perfil de QUALQUER jogador do Albion (via API pública gameinfo) + uma
seção extra se o jogador for da NOSSA guilda.

Global (API):  guilda · aliança · fama PvP · fama PvE · ratio (pvpfame/deathfame).
Na guilda (nossos dados): saldo de prata/energia e attendance — quando aplicável.
"""
import os
import re
import asyncio
import urllib.parse

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands, ui

from database import (
    is_server_activated, load_economy_config,
    get_registration, get_registration_by_nick,
    get_all_registrations_by_nick,
    get_user_balance, get_user_energy, has_energy_history,
    get_user_attendance_count,
)
from utils import format_silver, make_embed, send_err

_HOST = 'gameinfo.albiononline.com'
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_API_RETRIES = 6
_API_DELAY = 2
_API_TIMEOUT = 10


def _num(v) -> str:
    try:
        return format_silver(int(v or 0))
    except (TypeError, ValueError):
        return "0"


# ==================================================================
# Dropdown para múltiplos personagens com o mesmo nome
# ==================================================================
class PlayerSelect(ui.Select):
    def __init__(self, cog: "PerfilCog", name: str, reg, member, guild_set: set,
                 players: list):
        self.cog       = cog
        self.name      = name
        self.reg       = reg
        self.member    = member
        self.guild_set = guild_set
        self.players   = players
        options = []
        for i, p in enumerate(players[:25]):
            gname = (p.get('GuildName') or '').strip()
            atag  = (p.get('AllianceTag') or p.get('AllianceName') or '').strip()
            desc  = f"[{atag}] {gname}" if (atag and gname) else (gname or 'sem guilda')
            options.append(discord.SelectOption(
                label=(p.get('Name') or f'Personagem {i+1}')[:100],
                value=str(i),
                description=desc[:100],
            ))
        super().__init__(
            placeholder="Múltiplos personagens encontrados — escolha um…",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        idx    = int(self.values[0])
        player = self.players[idx]
        d, _   = await self.cog._fetch_json(
            f"https://{_HOST}/api/gameinfo/players/{player.get('Id')}")
        detail = d if isinstance(d, dict) else player
        embed  = await self.cog._build_embed(
            self.name, self.reg, self.member, self.guild_set, detail, 'ok')
        await interaction.edit_original_response(content=None, embed=embed, view=None)


class PlayerSelectView(ui.View):
    def __init__(self, cog, name, reg, member, guild_set, players):
        super().__init__(timeout=120)
        self.add_item(PlayerSelect(cog, name, reg, member, guild_set, players))


# ==================================================================
# Cog
# ==================================================================
class PerfilCog(commands.Cog, name="PerfilCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        print("✓ Perfil Cog carregada")

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_API_TIMEOUT),
                headers={'User-Agent': _UA},
            )
        return self._session

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    async def _fetch_json(self, url: str):
        try:
            s = await self._get_session()
            async with s.get(url) as r:
                if r.status != 200:
                    return None, f"HTTP {r.status}"
                return await r.json(content_type=None), None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    async def _latest_event_ts(self, player_id: str, kind: str) -> str:
        """Timestamp ISO da kill/morte mais recente, ou '' se não houver."""
        data, _ = await self._fetch_json(
            f"https://{_HOST}/api/gameinfo/players/{player_id}/{kind}?limit=1&offset=0")
        if isinstance(data, list) and data:
            return data[0].get('TimeStamp') or ''
        return ''

    async def _sort_by_lastkill(self, players: list) -> list:
        """Ordena lista de jogadores por lastkill DESC via API."""
        timestamps = await asyncio.gather(
            *(self._latest_event_ts(p.get('Id', ''), 'kills') for p in players)
        )
        paired = sorted(zip(timestamps, players), key=lambda x: x[0], reverse=True)
        return [p for _, p in paired]

    async def _find_player(self, name: str, guild_set: set):
        """Busca o jogador. Retorna (summary, error, all_exact).
        Se len(all_exact) > 1, o chamador deve mostrar dropdown."""
        q = urllib.parse.quote(name.strip())
        data, err = await self._fetch_json(f"https://{_HOST}/api/gameinfo/search?q={q}")
        if not isinstance(data, dict):
            return None, err, []
        players = data.get('players') or []
        nl = name.strip().lower()
        exact = [p for p in players if (p.get('Name') or '').lower() == nl]
        cand = exact or players
        if not cand:
            return None, None, []
        if len(exact) > 1:
            return None, None, exact
        if guild_set:
            for p in cand:
                if (p.get('GuildName') or '').lower() in guild_set:
                    return p, None, []
        return cand[0], None, []

    @commands.hybrid_command(
        name="stats",
        aliases=["profile", "perfil"],
        description="Perfil de um jogador do Albion (e dados extras se for da guild).",
    )
    @app_commands.guild_only()
    @app_commands.describe(jogador="Nick do jogador (em branco = o seu nick cadastrado)")
    async def stats(self, ctx: commands.Context, *, jogador: str = None):
        await ctx.defer()

        raw = (jogador or '').strip()
        if not raw:
            reg = await get_registration(ctx.author.id)
            name = (reg.get('nick') if reg else None) or ''
            if not name:
                await send_err(ctx, "Você não está cadastrado. Informe um nick "
                                    "(`/stats <jogador>`), marque alguém, ou use `/register`.")
                return
        else:
            mention = re.match(r'<@!?(\d+)>$', raw)
            if mention:
                uid = int(mention.group(1))
                reg = await get_registration(uid)
                if not reg or not (reg.get('nick') or '').strip():
                    await send_err(ctx, f"<@{uid}> não está registrado no bot.")
                    return
                name = reg['nick'].strip()
            else:
                name = raw

        cfg = await load_economy_config()
        guild_set = {g.strip().lower()
                     for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}

        reg    = await get_registration_by_nick(name)
        member = ctx.guild.get_member(reg['user_id']) if (reg and ctx.guild) else None

        summary, ferr, all_exact = None, None, []
        for attempt in range(_API_RETRIES):
            summary, ferr, all_exact = await self._find_player(name, guild_set)

            if all_exact:
                sorted_players = await self._sort_by_lastkill(all_exact)
                view = PlayerSelectView(self, name, reg, member, guild_set, sorted_players)
                await ctx.send(
                    f"Múltiplos personagens com o nome **{name}**. Qual deseja ver?",
                    view=view,
                )
                return

            if summary or ferr is None:
                break
            if attempt < _API_RETRIES - 1:
                await asyncio.sleep(_API_DELAY)

        detail, status = None, 'error'
        if summary:
            d, _ = await self._fetch_json(
                f"https://{_HOST}/api/gameinfo/players/{summary.get('Id')}")
            detail = d if isinstance(d, dict) else summary
            status = 'ok'
        elif ferr is None:
            status = 'notfound'

        embed = await self._build_embed(name, reg, member, guild_set, detail, status)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _build_embed(self, name, reg, member, guild_set, detail, status):
        gname = (detail.get('GuildName') or '').strip() if detail else ''
        if detail:
            atag = (detail.get('AllianceTag') or detail.get('AllianceName') or '').strip()
            guild_disp = f"[{atag}] {gname}" if (atag and gname) else (gname or '*sem guilda*')
        else:
            guild_disp = ""

        embed = make_embed('info', desc=f"\n{guild_disp}")
        if member:
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        else:
            embed.set_author(name=name)

        # ---- Dados globais (API) ----
        if detail:
            kf  = detail.get('KillFame') or 0
            df  = detail.get('DeathFame') or 0
            kda = (kf / df) if df else float(kf or 0)
            pve = ((detail.get('LifetimeStatistics') or {}).get('PvE') or {}).get('Total') or 0
            gval = (f"⚔️ Fama PVP: {_num(kf)}\n"
                    f"🌿 Fama PVE: {_num(pve)}\n"
                    f"📊 Ratio: __{kda:.2f}__")
        elif status == 'notfound':
            gval = "❔ jogador não encontrado"
        else:
            gval = "⚠️ erro ao buscar dados"
        embed.add_field(name="", value=gval, inline=False)

        # ---- Dados da guild (nosso banco) ----
        in_guild = bool(reg) or (gname.lower() in guild_set if gname else False)
        if in_guild:
            if reg:
                uid = reg['user_id']
                bal, _ = await get_user_balance(uid)
                line = f"💰 Saldo de Prata: {format_silver(bal)}"
                energy = await get_user_energy(uid)
                if energy != 0 or await has_energy_history(uid):
                    line += f"\n⚡ Saldo de Energia: {format_silver(energy)}"
                lines = [line]
                att = await get_user_attendance_count(uid)
                lines.append(f"🥷🏿 Attendance: {att} eventos")
            else:
                lines = ["*(sem cadastro no bot — saldo/attendance indisponíveis; use `/register`)*"]
            embed.add_field(name="", value="\n".join(lines), inline=False)

        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(PerfilCog(bot))
