"""/profile /attendance /lowattendance — porta dos comandos do bot antigo.

/profile busca direto na API pública do Albion (como o bot antigo) e esquenta
o perfil no backend (warm_by_name). /attendance e /lowattendance falam com o
backend (EventParticipant é a "attendance" do bot antigo, agora no Postgres).
"""
from __future__ import annotations

import asyncio
import io
import os
import time
import urllib.parse
from typing import Optional

import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands

import http_client
from cogs.general import check_command_access, guild_lang
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

# Retry da API do Albion no /profile: mesma filosofia do /register — em vez
# de devolver erro e obrigar o usuário a rodar de novo, re-tenta com feedback
# ao vivo. Teto de 5 min como pedido pelo dono.
_PROFILE_RETRY_INTERVAL = 15
_PROFILE_RETRY_CAP = 5 * 60
_PROFILE_READY_POLL_INTERVAL = 2
_PROFILE_READY_TIMEOUT = 12 * 60


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


async def _warm_profile(guild_id: int, name: str, region: str) -> dict | None:
    """Esquenta o perfil no backend e devolve o estado do warm."""
    return await http_client.post_json(
        f"/bot/guilds/{guild_id}/warm",
        {"name": name, "region": region},
        timeout=10, tag="warm", queue_on_failure=False,
    )


async def _resolve_name_and_region(interaction: Interaction, raw: str, guild_id: int) -> tuple[str | None, str, str | None]:
    """Devolve (name, region, error_key). error_key não-None = o que mostrar
    pro usuário; name None significa que não dá pra buscar."""
    lang = await guild_lang(interaction)
    if not raw:
        # Sem nick: busca o nick registrado + região no backend
        await interaction.response.defer()
        data = await http_client.get_json(
            f"/bot/guilds/{guild_id}/attendance/{interaction.user.id}",
            timeout=10, tag="profile",
        )
        if data is None:
            return None, "americas", "retry_later"
        name = (data.get("albion_player_name") or "").strip()
        region = data.get("region") or "americas"
        if not name:
            return None, region, "profile_usage"
        return name, region, None
    elif raw.startswith("<@") or raw.isdigit():
        # Menção/ID: busca o nick registrado dessa pessoa
        await interaction.response.defer()
        target_id = int(raw.strip("<@!>")) if raw.startswith("<@") else int(raw)
        data = await http_client.get_json(
            f"/bot/guilds/{guild_id}/attendance/{target_id}",
            timeout=10, tag="profile",
        )
        if data is None:
            return None, "americas", "retry_later"
        name = (data.get("albion_player_name") or "").strip()
        region = data.get("region") or "americas"
        if not name:
            return None, region, "profile_not_registered"
        return name, region, None
    else:
        name = raw
        if not interaction.response.is_done():
            await interaction.response.defer()
        region = await _get_guild_region(guild_id)
        return name, region, None


async def _fetch_profile(name: str, host: str) -> tuple[dict | None, bool]:
    """Busca o perfil completo na API do Albion. Devolve (detail, api_ok).
    api_ok=False = a API não respondeu/erro → merece retry. api_ok=True com
    detail=None = a API respondeu mas o jogador não existe → não re-tentar."""
    q = urllib.parse.quote(name)
    search_data = await _fetch_albion(f"https://{host}/api/gameinfo/search?q={q}")
    if not isinstance(search_data, dict):
        return None, False
    players = search_data.get("players") or []
    nl = name.lower()
    exact = [p for p in players if (p.get("Name") or "").lower() == nl]
    cand = exact or players
    if not cand:
        return None, True
    # Múltiplos com o mesmo nome exato: pega o de maior KillFame.
    if len(exact) > 1:
        cand = sorted(exact, key=lambda p: p.get("KillFame") or 0, reverse=True)
    summary = cand[0]
    player_id = summary.get("Id")
    detail = await _fetch_albion(f"https://{host}/api/gameinfo/players/{player_id}")
    if not isinstance(detail, dict):
        detail = summary
    return detail, True


async def _search_region(name: str, region: str) -> tuple[str, dict | None, bool]:
    """Busca o player numa região. Devolve (region, summary, api_ok).
    summary=None+api_ok=True = não existe nessa região."""
    host = _HOSTS[region]
    q = urllib.parse.quote(name)
    search_data = await _fetch_albion(f"https://{host}/api/gameinfo/search?q={q}")
    if not isinstance(search_data, dict):
        return region, None, False
    players = search_data.get("players") or []
    nl = name.lower()
    exact = [p for p in players if (p.get("Name") or "").lower() == nl]
    cand = exact or players
    if not cand:
        return region, None, True
    if len(exact) > 1:
        cand = sorted(exact, key=lambda p: p.get("KillFame") or 0, reverse=True)
    return region, cand[0], True


async def _search_all_regions(name: str) -> list[tuple[str, dict, bool]]:
    """Busca o nick nas 3 regiões em paralelo. Devolve lista de
    (region, summary, api_ok) — só as regiões onde api_ok=True E summary
    não-None (player existe). Regiões com erro de API são excluídas."""
    regions = list(_HOSTS.keys())
    results = await asyncio.gather(*(_search_region(name, r) for r in regions))
    return [(r, s, ok) for r, s, ok in results if ok and s is not None]


_REGION_LABELS = {
    "americas": "Americas",
    "europe": "Europe",
    "asia": "Asia",
}


class _RegionSelectView(discord.ui.View):
    """Botões pra escolher a região quando o nick existe em mais de uma.
    Timeout de 10s → escolhe automaticamente o de maior KillFame."""

    def __init__(self, candidates: list[tuple[str, dict]], lang: str, on_select):
        super().__init__(timeout=10)
        self._candidates = candidates
        self._lang = lang
        self._on_select = on_select
        self._resolved = False
        for region, summary in candidates:
            kf = summary.get("KillFame") or 0
            label = f"{_REGION_LABELS.get(region, region)} · {kf:,} fame"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            btn.callback = self._make_callback(region)
            self.add_item(btn)

    def _make_callback(self, region: str):
        async def callback(interaction: Interaction) -> None:
            if self._resolved:
                return
            self._resolved = True
            self.stop()
            await self._on_select(interaction, region)
        return callback

    async def on_timeout(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        # Pick most recent events = highest KillFame (proxy for activity).
        best = max(self._candidates, key=lambda c: c[1].get("KillFame") or 0)
        await self._on_select(None, best[0])


class Members(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /profile — busca Albion API + warm, com retry
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
        name, region, err_key = await _resolve_name_and_region(interaction, raw, guild_id)
        if err_key:
            await interaction.followup.send(t(lang, err_key))
            return

        # Nick digitado livremente (não de registro): pode existir em várias
        # regiões. Busca nas 3 em paralelo e oferece botões se achar em >1.
        region_known = bool(raw and not raw.startswith("<@") and not raw.isdigit())
        if region_known:
            candidates = await _search_all_regions(name)
            if not candidates:
                await interaction.edit_original_response(
                    content=t(lang, "profile_not_found", name=name)
                )
                return
            region = max(candidates, key=lambda c: c[1].get("KillFame") or 0)[0]

        await self._wait_and_show_preview(interaction, name, region, lang, guild_id)

    async def _wait_and_show_preview(
        self, interaction: Interaction, name: str, region: str, lang: str, guild_id: int,
    ) -> None:
        """Espera silenciosamente o cold-load e responde somente com o preview pronto."""
        warm = await _warm_profile(guild_id, name, region)
        if warm and warm.get("status") in {"not_found", "search_failed"}:
            await interaction.edit_original_response(
                content=t(lang, "profile_not_found", name=name), embed=None)
            return
        encoded_name = urllib.parse.quote(name, safe="")
        profile_path = f"/players/by-name/{region}/{encoded_name}"
        preview_path = f"/players/embed/{region}/{encoded_name}.png"
        deadline = time.monotonic() + _PROFILE_READY_TIMEOUT
        while time.monotonic() < deadline:
            profile = await http_client.get_json(profile_path, timeout=15, tag="profile")
            if profile is None:
                await asyncio.sleep(_PROFILE_READY_POLL_INTERVAL)
                continue
            if profile.get("_cold_load"):
                await asyncio.sleep(_PROFILE_READY_POLL_INTERVAL)
                continue
            png = await http_client.get_bytes(preview_path, timeout=30, tag="profile")
            if png:
                image = discord.File(io.BytesIO(png), filename="profile.png")
                embed = discord.Embed(color=discord.Color.blurple())
                embed.set_image(url="attachment://profile.png")
                await interaction.edit_original_response(content=None, embed=embed, attachments=[image])
                return
            await asyncio.sleep(_PROFILE_READY_POLL_INTERVAL)
        await interaction.edit_original_response(content=t(lang, "profile_api_error"), embed=None)

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

        embed = discord.Embed(
            color=discord.Color.blurple(),
            description=f"## {member.mention}",
        )
        embed.set_thumbnail(url=member.display_avatar.url)

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