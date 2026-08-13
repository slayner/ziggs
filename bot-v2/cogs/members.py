"""/profile /attendance /lowattendance — porta dos comandos do bot antigo.

/profile busca direto na API pública do Albion (como o bot antigo) e esquenta
o perfil no backend (warm_by_name). /attendance e /lowattendance falam com o
backend (EventParticipant é a "attendance" do bot antigo, agora no Postgres).
"""
from __future__ import annotations

import asyncio
import os
import urllib.parse
from typing import Optional

import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands

import http_client
from cogs.general import check_command_access, guild_lang, resolve_user_or_guild
from i18n import t
from localization import loc

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")

_HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe": "gameinfo-ams.albiononline.com",
    "asia": "gameinfo-sgp.albiononline.com",
}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_API_TIMEOUT = 10
_API_RETRIES = 3


def _num(v) -> str:
    try:
        return f"{int(v or 0):,}"
    except (TypeError, ValueError):
        return "0"


async def _fetch_albion(url: str) -> dict | list | None:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_API_TIMEOUT),
            headers={"User-Agent": _UA},
        ) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
    except Exception:
        return None


async def _get_guild_region(guild_id: int) -> str:
    """Região configurada da guilda (fallback americas)."""
    data = await http_client.get_json(f"/bot/guilds/{guild_id}/bank", timeout=5)
    region = (data or {}).get("region") if data else None
    return region if region in _HOSTS else "americas"


async def _warm_profile(guild_id: int, name: str, region: str) -> None:
    """Best-effort: esquenta o perfil no backend pra não desperdiçar a busca."""
    await http_client.post_json(
        f"/bot/guilds/{guild_id}/warm",
        {"name": name, "region": region},
        timeout=10, tag="warm", queue_on_failure=False,
    )


class Members(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /profile — busca Albion API + warm
    # ------------------------------------------------------------------
    @app_commands.command(
        name="profile",
        description=loc("Mostra o perfil de um jogador do Albion (fama, guilda, saldo e attendance)", "cmd_desc_profile"),
    )
    @app_commands.describe(
        jogador=loc("Nick do jogador (em branco = o seu nick cadastrado)", "opt_desc_profile_jogador"),
    )
    @app_commands.guild_only()
    async def profile(self, interaction: Interaction, jogador: Optional[str] = None) -> None:
        if not await check_command_access(interaction, "profile"):
            return
        lang = await guild_lang(interaction)
        guild_id = interaction.guild_id

        raw = (jogador or "").strip()
        if not raw:
            # Sem nick: busca o nick registrado + região no backend
            await interaction.response.defer()
            data = await http_client.get_json(
                f"/bot/guilds/{guild_id}/attendance/{interaction.user.id}",
                timeout=10, tag="profile",
            )
            if data is None:
                await interaction.followup.send(t(lang, "retry_later"))
                return
            name = (data.get("albion_player_name") or "").strip()
            region = data.get("region") or "americas"
            if not name:
                await interaction.followup.send(t(lang, "profile_usage"))
                return
        elif raw.startswith("<@") or raw.isdigit():
            # Menção/ID: busca o nick registrado dessa pessoa
            await interaction.response.defer()
            target_id = int(raw.strip("<@!>")) if raw.startswith("<@") else int(raw)
            data = await http_client.get_json(
                f"/bot/guilds/{guild_id}/attendance/{target_id}",
                timeout=10, tag="profile",
            )
            if data is None:
                await interaction.followup.send(t(lang, "retry_later"))
                return
            name = (data.get("albion_player_name") or "").strip()
            region = data.get("region") or "americas"
            if not name:
                await interaction.followup.send(t(lang, "profile_not_registered"))
                return
        else:
            name = raw
            if not interaction.response.is_done():
                await interaction.response.defer()
            region = await _get_guild_region(guild_id)

        host = _HOSTS[region]

        # Busca na API do Albion
        q = urllib.parse.quote(name)
        search_data = await _fetch_albion(f"https://{host}/api/gameinfo/search?q={q}")
        if not isinstance(search_data, dict):
            await interaction.followup.send(t(lang, "profile_api_error"))
            return

        players = search_data.get("players") or []
        nl = name.lower()
        exact = [p for p in players if (p.get("Name") or "").lower() == nl]
        cand = exact or players
        if not cand:
            await interaction.followup.send(t(lang, "profile_not_found", name=name))
            return

        # Se tem múltiplos com o mesmo nome exato, pega o primeiro (ordena por
        # KillFame desc). Ponytail: sem dropdown como o bot antigo.
        if len(exact) > 1:
            cand = sorted(exact, key=lambda p: p.get("KillFame") or 0, reverse=True)

        summary = cand[0]
        player_id = summary.get("Id")
        detail = await _fetch_albion(f"https://{host}/api/gameinfo/players/{player_id}")
        if not isinstance(detail, dict):
            detail = summary

        # Warm do perfil no backend (best-effort, não bloqueia a resposta)
        asyncio.create_task(_warm_profile(guild_id, name, region))

        # Busca dados extras do backend (saldo + attendance) se o jogador for
        # registrado na guilda
        # Ponytail: buscar pelo nick é indireto — o backend tem attendance por
        # discord_user_id, não por nick. Vamos pular os dados extras por agora;
        # o /attendance cobre isso separado.
        embed = self._build_profile_embed(detail, name)
        await interaction.followup.send(embed=embed)

    def _build_profile_embed(self, detail: dict, name: str) -> discord.Embed:
        gname = (detail.get("GuildName") or "").strip()
        atag = (detail.get("AllianceTag") or detail.get("AllianceName") or "").strip()
        guild_disp = f"[{atag}] {gname}" if (atag and gname) else (gname or "*sem guilda*")

        embed = discord.Embed(color=discord.Color.blurple(), description=f"\n{guild_disp}")
        embed.set_author(name=name)

        kf = detail.get("KillFame") or 0
        df = detail.get("DeathFame") or 0
        kda = (kf / df) if df else float(kf or 0)
        pve = ((detail.get("LifetimeStatistics") or {}).get("PvE") or {}).get("Total") or 0

        embed.add_field(
            name="",
            value=(
                f"⚔️ Fama PvP: {_num(kf)}\n"
                f"🌿 Fama PvE: {_num(pve)}\n"
                f"📊 Ratio: __{kda:.2f}__"
            ),
            inline=False,
        )
        return embed

    # ------------------------------------------------------------------
    # /attendance — stats do backend
    # ------------------------------------------------------------------
    @app_commands.command(
        name="attendance",
        description=loc("Mostra estatísticas de participação em eventos CTA", "cmd_desc_attendance"),
    )
    @app_commands.describe(
        target=loc("Usuário (em branco = você)", "opt_desc_attendance_target"),
    )
    @app_commands.rename(target=loc("user", "opt_name_alvo"))
    @app_commands.guild_only()
    async def attendance(self, interaction: Interaction, target: Optional[discord.Member] = None) -> None:
        if not await check_command_access(interaction, "attendance"):
            return
        lang = await guild_lang(interaction)
        guild_id = interaction.guild_id

        member = target or interaction.user
        await interaction.response.defer(ephemeral=True)

        data = await http_client.get_json(
            f"/bot/guilds/{guild_id}/attendance/{member.id}", timeout=10, tag="attendance",
        )
        if data is None:
            await interaction.followup.send(t(lang, "retry_later"), ephemeral=True)
            return

        total_all = data.get("total_events", 0)
        user_all = data.get("user_events", 0)
        total_7d = data.get("total_events_7d", 0)
        user_7d = data.get("user_events_7d", 0)
        rank = data.get("rank")
        last_event = data.get("last_event")

        pct_all = (user_all / total_all * 100) if total_all > 0 else 0
        pct_7d = (user_7d / total_7d * 100) if total_7d > 0 else 0

        embed = discord.Embed(color=discord.Color.blurple(), title=f"Attendance — {member.display_name}")

        if total_all == 0:
            embed.add_field(name=t(lang, "att_lifetime"), value=t(lang, "att_no_events"), inline=False)
        else:
            embed.add_field(
                name=t(lang, "att_lifetime"),
                value=t(lang, "att_lifetime_val", total=total_all, user=user_all, pct=pct_all),
                inline=False,
            )

        if total_7d == 0:
            embed.add_field(name=t(lang, "att_7d"), value=t(lang, "att_no_events_7d"), inline=False)
        else:
            embed.add_field(
                name=t(lang, "att_7d"),
                value=t(lang, "att_7d_val", total=total_7d, user=user_7d, pct=pct_7d),
                inline=False,
            )

        rank_str = f"`#{rank}`" if rank is not None else t(lang, "att_no_data")
        embed.add_field(name=t(lang, "att_rank"), value=rank_str, inline=True)

        if last_event:
            embed.add_field(name=t(lang, "att_last"), value=f"<t:{_iso_to_ts(last_event)}:R>", inline=True)
        else:
            embed.add_field(name=t(lang, "att_last"), value=t(lang, "att_never"), inline=True)

        embed.set_footer(text=t(lang, "att_footer"))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /lowattendance — admin only
    # ------------------------------------------------------------------
    @app_commands.command(
        name="lowattendance",
        description=loc("Lista membros com menor participação nos últimos 7 dias", "cmd_desc_lowattendance"),
    )
    @app_commands.guild_only()
    async def lowattendance(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "lowattendance"):
            return
        lang = await guild_lang(interaction)
        guild_id = interaction.guild_id

        await interaction.response.defer(ephemeral=True)

        data = await http_client.get_json(
            f"/bot/guilds/{guild_id}/lowattendance", timeout=15, tag="lowattendance",
        )
        if data is None:
            await interaction.followup.send(t(lang, "retry_later"), ephemeral=True)
            return

        members = data.get("members", [])
        total_7d = data.get("total_7d", 0)
        filtered = data.get("filtered_recent", 0)

        embed = discord.Embed(color=discord.Color.gold(), title=t(lang, "lowatt_title"))
        embed.description = t(lang, "lowatt_desc", total=total_7d, analyzed=len(members), filtered=filtered)

        if not members:
            embed.add_field(name="", value=t(lang, "lowatt_empty"), inline=False)
        else:
            lines = []
            for i, m in enumerate(members, start=1):
                count = m.get("count_7d", 0)
                last = m.get("last_event")
                last_str = f"<t:{_iso_to_ts(last)}:R>" if last else t(lang, "att_never")
                word = t(lang, "event_word") if count == 1 else t(lang, "events_word")
                lines.append(f"`#{i:>2}`  {m.get('albion_player_name', '?')}  ·  **{count}** {word}  ·  {last_str}")

            # Quebra em fields se passar de 1024 chars
            chunk, chunk_len, first = [], 0, True
            for line in lines:
                needed = len(line) + (1 if chunk else 0)
                if chunk_len + needed > 1024:
                    embed.add_field(name=t(lang, "lowatt_ranking") if first else "​", value="\n".join(chunk), inline=False)
                    first = False
                    chunk, chunk_len = [], 0
                chunk.append(line)
                chunk_len += needed
            if chunk:
                embed.add_field(name=t(lang, "lowatt_ranking") if first else "​", value="\n".join(chunk), inline=False)

        embed.set_footer(text=t(lang, "att_footer"))
        await interaction.followup.send(embed=embed, ephemeral=True)


def _iso_to_ts(iso: str) -> int:
    """ISO string → Unix timestamp (int)."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso.replace(" ", "T"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Members(bot))