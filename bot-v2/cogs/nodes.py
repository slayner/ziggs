"""Calendário de nodes (Black Zone) — substitui `bot/cogs/nodes.py` do bot-v1.

Fonte da verdade no backend (`/bot/guilds/{g}/nodes/*`); o bot só renderiza o
calendário embed (3 colunas UTC/node/mapa, gap `…` entre nodes >2h) e faz proxy
das adições/remoções. Wizard de adição por buckets de letra (a BZ tem 268
mapas — não cabe num Select de 25; v1 fazia assim também).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands, tasks

from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# Gap entre nodes vizinhos que vira "…" no calendário (2h, igual v1).
TIME_GAP_BREAK_S = 2 * 3600
# Lupa de horário: faixas de 25min paginadas até 36h à frente, depois minuto exato.
TIME_BUCKET_MIN       = 25                              # minutos por faixa
TIME_HORIZON_H        = 36                              # 36h de cobertura
TIME_TOTAL_FAIXAS     = TIME_HORIZON_H * 60 // TIME_BUCKET_MIN + 1   # 87
TIME_FAIXAS_PER_PAGE  = 23                              # 25 - 2 slots de navegação ◀️/▶️
TIME_PAGES            = -(-TIME_TOTAL_FAIXAS // TIME_FAIXAS_PER_PAGE)  # ceil → 4


def _fmt_eta(minutes: int) -> str:
    """Quanto falta, legível: '2 horas' · '2h25min' · '25 minutos' · 'agora'."""
    if minutes <= 0:
        return "agora"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h{m:02d}min"
    if h:
        return f"{h} hora{'s' if h > 1 else ''}"
    return f"{m} minuto{'s' if m > 1 else ''}"


async def _get(path: str) -> Optional[dict]:
    if not SITE_URL or not API_SECRET:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{SITE_URL}{path}", headers={"Authorization": f"Bearer {API_SECRET}"},
                             timeout=aiohttp.ClientTimeout(total=5))
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


async def _post(path: str, body: dict) -> Optional[dict]:
    if not SITE_URL or not API_SECRET:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post(f"{SITE_URL}{path}", json=body, headers={"Authorization": f"Bearer {API_SECRET}"},
                              timeout=aiohttp.ClientTimeout(total=5))
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


async def _delete(path: str) -> Optional[dict]:
    if not SITE_URL or not API_SECRET:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.delete(f"{SITE_URL}{path}", headers={"Authorization": f"Bearer {API_SECRET}"},
                                timeout=aiohttp.ClientTimeout(total=5))
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


def _is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def _parse_iso(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%H:%M")


def _build_calendar_embed(lang: str, events: list[dict], emoji_map: dict[str, str]) -> discord.Embed:
    embed = discord.Embed(color=discord.Color.blurple(), title=t(lang, "nodes_title"))
    if not events:
        embed.description = t(lang, "nodes_empty")
        return embed

    def _ts(e):
        d = _parse_iso(e["spawn_at"])
        return d.timestamp() if d else float("inf")

    events = sorted(events, key=_ts)
    col_utc, col_node, col_map = [], [], []
    used, shown, prev_ts = 0, 0, None
    for e in events:
        dt = _parse_iso(e["spawn_at"])
        if dt is None:
            continue
        ts = int(dt.timestamp())
        line_map = f"🌎 __{e['map_name']}__ · <t:{ts}:R>"
        if used + len(line_map) + 1 > 1000:
            break
        if prev_ts is not None and ts - prev_ts >= TIME_GAP_BREAK_S:
            col_utc.append("**…**")
            col_node.append("ㅤ")
            col_map.append("ㅤ")
            used += 2
        prev_ts = ts
        emoji = emoji_map.get(e["node_type"]) or "🌿"
        col_utc.append(f"**{_fmt_utc(dt)}**")
        col_node.append(f"**{emoji}  {e['node_type']}**")
        col_map.append(line_map)
        used += len(line_map) + 1
        shown += 1

    embed.add_field(name="", value="\n".join(col_utc) or "ㅤ", inline=True)
    embed.add_field(name="", value="\n".join(col_node) or "ㅤ", inline=True)
    embed.add_field(name="", value="\n".join(col_map) or "ㅤ", inline=True)
    remaining = len(events) - shown
    if remaining > 0:
        embed.description = f"*… +{remaining} node(s)."
    return embed


# ── Wizard de adição ─────────────────────────────────────────────────────────

class AddNodeView(discord.ui.View):
    """Wizard: tipo → letra (bucket de mapa) → mapa → horário → confirmar.
    Re-renderiza a si mesmo a cada passo (clear_items + monta de novo)."""

    def __init__(self, guild_id: int, lang: str, defs: list[dict], maps: list[str]):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.lang = lang
        self.defs = defs
        self.maps = maps
        self.node_type: str = ""
        self.map_name: str = ""
        self.spawn_at: datetime | None = None
        self._letter: str = ""
        self._time_page: int = 0
        self._time_base: datetime | None = None
        self._time_block_start: datetime | None = None
        self._show_type_select()

    def _add_cancel(self) -> None:
        cancel = discord.ui.Button(label=t(self.lang, "nodes_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _add_back(self, show_fn, label: str) -> None:
        async def _back(interaction: Interaction) -> None:
            show_fn()
            await interaction.response.edit_message(content=label, view=self)
        back = discord.ui.Button(label=t(self.lang, "nodes_back"), style=discord.ButtonStyle.secondary)
        back.callback = _back
        self.add_item(back)

    # --- etapa 1: tipo ---
    def _show_type_select(self) -> None:
        self.clear_items()
        if not self.defs:
            self._add_cancel()
            return
        select = discord.ui.Select(
            placeholder=t(self.lang, "nodes_pick_type"),
            options=[discord.SelectOption(label=f"{d.get('emoji') or '🌿'} {d['name']}", value=d["name"])
                     for d in self.defs[:25]],
        )
        select.callback = self._on_type_chosen
        self.add_item(select)

    async def _on_type_chosen(self, interaction: Interaction) -> None:
        self.node_type = self.children[0].values[0]
        self._show_letter_select()
        await interaction.response.edit_message(content=t(self.lang, "nodes_pick_map"), view=self)

    # --- etapa 2: letra (bucket) ---
    def _buckets(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {}
        for m in self.maps:
            key = (m[0].upper() if m else "?")
            buckets.setdefault(key, []).append(m)
        return buckets

    def _show_letter_select(self) -> None:
        self.clear_items()
        buckets = self._buckets()
        if not buckets:
            self._add_cancel()
            return
        if len(buckets) == 1:
            self._letter = next(iter(buckets))
            self._show_map_select()
            return
        select = discord.ui.Select(
            placeholder=t(self.lang, "nodes_pick_map"),
            options=[discord.SelectOption(label=letter, value=letter) for letter in sorted(buckets)[:25]],
        )
        select.callback = self._on_letter_chosen
        self.add_item(select)
        self._add_back(self._show_type_select, t(self.lang, "nodes_pick_type"))

    async def _on_letter_chosen(self, interaction: Interaction) -> None:
        self._letter = self.children[0].values[0]
        self._show_map_select()
        await interaction.response.edit_message(content=t(self.lang, "nodes_pick_map"), view=self)

    # --- etapa 3: mapa ---
    def _show_map_select(self) -> None:
        self.clear_items()
        maps = self._buckets().get(self._letter, [])[:25]
        opts = [discord.SelectOption(label=m, value=m) for m in maps] or [discord.SelectOption(label="—", value="")]
        select = discord.ui.Select(placeholder=t(self.lang, "nodes_pick_map"), options=opts)
        select.callback = self._on_map_chosen
        self.add_item(select)
        self._add_back(self._show_letter_select, t(self.lang, "nodes_pick_map"))

    async def _on_map_chosen(self, interaction: Interaction) -> None:
        self.map_name = self.children[0].values[0]
        if not self.map_name:
            await interaction.response.defer()
            return
        self._show_time_select()
        await interaction.response.edit_message(content=t(self.lang, "nodes_pick_time"), view=self)

    # --- etapa 4: horário (lupa de 25 em 25min → minuto exato, até 36h) ---
    def _show_time_select(self) -> None:
        """Entry point da lupa: base = agora (sem segundos), página 0, desenha a faixa."""
        self._time_base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self._time_page = 0
        self._render_time_range()

    def _render_time_range(self) -> None:
        """(Re)desenha a faixa de horário da página atual (com ◀️/▶️ embutidos)."""
        self.clear_items()
        now = datetime.now(timezone.utc)
        page = self._time_page
        start_idx = page * TIME_FAIXAS_PER_PAGE
        end_idx = min(start_idx + TIME_FAIXAS_PER_PAGE, TIME_TOTAL_FAIXAS)
        options: list[discord.SelectOption] = []
        if page > 0:                                     # voltar (não na 1ª pág)
            options.append(discord.SelectOption(label=t(self.lang, "nodes_time_prev"), value="__prev__"))
        for idx in range(start_idx, end_idx):
            s = self._time_base + timedelta(minutes=idx * TIME_BUCKET_MIN)
            e = s + timedelta(minutes=TIME_BUCKET_MIN)
            eta_s = _fmt_eta(-(-int((s - now).total_seconds()) // 60))   # ceil
            eta_e = _fmt_eta(-(-int((e - now).total_seconds()) // 60))
            pref = "" if eta_s == "agora" else "em "
            options.append(discord.SelectOption(
                label=f"{s:%H:%M}–{e:%H:%M} UTC  ({pref}{eta_s} - {eta_e})",
                value=str(int(s.timestamp())),
            ))
        if page < TIME_PAGES - 1:                        # avançar (não na última)
            options.append(discord.SelectOption(label=t(self.lang, "nodes_time_next"), value="__next__"))
        select = discord.ui.Select(
            placeholder=t(self.lang, "nodes_pick_time_range", page=page + 1, pages=TIME_PAGES),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_range_chosen
        self.add_item(select)
        self._add_back(self._show_map_select, t(self.lang, "nodes_pick_map"))

    async def _on_range_chosen(self, interaction: Interaction) -> None:
        val = self.children[0].values[0]
        if val == "__next__":
            self._time_page = min(self._time_page + 1, TIME_PAGES - 1)
            self._render_time_range()
            await interaction.response.edit_message(content=t(self.lang, "nodes_pick_time"), view=self)
            return
        if val == "__prev__":
            self._time_page = max(self._time_page - 1, 0)
            self._render_time_range()
            await interaction.response.edit_message(content=t(self.lang, "nodes_pick_time"), view=self)
            return
        block_start = datetime.fromtimestamp(int(val), tz=timezone.utc)
        self._show_time_minute_select(block_start)
        await interaction.response.edit_message(content=t(self.lang, "nodes_pick_time"), view=self)

    def _show_time_minute_select(self, block_start: datetime) -> None:
        """Minuto exato dentro da faixa de 25min escolhida."""
        self.clear_items()
        self._time_block_start = block_start
        now = datetime.now(timezone.utc)
        options: list[discord.SelectOption] = []
        for k in range(TIME_BUCKET_MIN):                  # os 25 minutos da faixa
            s = block_start + timedelta(minutes=k)
            eta = _fmt_eta(-(-int((s - now).total_seconds()) // 60))
            pref = "" if eta == "agora" else "em "
            options.append(discord.SelectOption(
                label=f"{s:%H:%M} UTC  ({pref}{eta})",
                value=str(int(s.timestamp())),
            ))
        select = discord.ui.Select(
            placeholder=t(self.lang, "nodes_pick_time_minute"),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_minute_chosen
        self.add_item(select)
        # Voltar → volta à faixa (página preservada).
        self._add_back(self._render_time_range, t(self.lang, "nodes_pick_time"))

    async def _on_minute_chosen(self, interaction: Interaction) -> None:
        self.spawn_at = datetime.fromtimestamp(int(self.children[0].values[0]), tz=timezone.utc)
        self.clear_items()
        confirm = discord.ui.Button(label=t(self.lang, "nodes_confirm"), style=discord.ButtonStyle.success)
        confirm.callback = self._on_confirm
        back = discord.ui.Button(label=t(self.lang, "nodes_back"), style=discord.ButtonStyle.secondary)
        back.callback = self._on_back_to_time
        cancel = discord.ui.Button(label=t(self.lang, "nodes_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(confirm)
        self.add_item(back)
        self.add_item(cancel)
        await interaction.response.edit_message(
            content=f"{self.node_type} · {self.map_name} · {_fmt_utc(self.spawn_at)}", view=self,
        )

    async def _on_back_to_time(self, interaction: Interaction) -> None:
        self._render_time_range()
        await interaction.response.edit_message(content=t(self.lang, "nodes_pick_time"), view=self)

    async def _on_confirm(self, interaction: Interaction) -> None:
        if not self.node_type or not self.map_name or self.spawn_at is None:
            await interaction.response.edit_message(content=t(self.lang, "nodes_add_fail", reason="incompleto"), view=None)
            return
        res = await _post(f"/bot/guilds/{self.guild_id}/nodes", {
            "node_type": self.node_type,
            "map_name": self.map_name,
            "spawn_at": self.spawn_at.isoformat(),
            "added_by_id": interaction.user.id,
            "added_by_name": interaction.user.display_name,
        })
        if res is None or not res.get("ok"):
            reason = (res or {}).get("detail") if isinstance(res, dict) else None
            await interaction.response.edit_message(
                content=t(self.lang, "nodes_add_fail", reason=reason or "erro"), view=None,
            )
            return
        await interaction.response.edit_message(
            content=t(self.lang, "nodes_added", node=self.node_type, mapa=self.map_name,
                      hora=_fmt_utc(self.spawn_at)),
            view=None,
        )
        asyncio.create_task(_trigger_calendar_refresh(interaction.client, interaction.guild))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "nodes_cancel"), view=None)


# ── Remoção ──────────────────────────────────────────────────────────────────

class NodeRemoveView(discord.ui.View):
    def __init__(self, guild_id: int, lang: str, events: list[dict]):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.lang = lang
        if not events:
            return
        select = discord.ui.Select(
            placeholder=t(lang, "nodes_pick_remove"),
            options=[discord.SelectOption(
                label=f"{e['node_type']} · {e['map_name']} · {_fmt_utc(_parse_iso(e['spawn_at']))}",
                value=str(e["id"]),
            ) for e in events[:25]],
        )
        select.callback = self._on_pick
        self.add_item(select)

    async def _on_pick(self, interaction: Interaction) -> None:
        node_id = self.children[0].values[0]
        res = await _delete(f"/bot/guilds/{self.guild_id}/nodes/{node_id}")
        if res is None or not res.get("ok"):
            await interaction.response.edit_message(content=t(self.lang, "nodes_remove_fail"), view=None)
            return
        await interaction.response.edit_message(content=t(self.lang, "nodes_removed"), view=None)
        asyncio.create_task(_trigger_calendar_refresh(interaction.client, interaction.guild))


# ── View persistente do calendário ───────────────────────────────────────────

class CalendarView(discord.ui.View):
    def __init__(self, lang: str):
        super().__init__(timeout=None)
        self.lang = lang
        add_btn = discord.ui.Button(label=t(lang, "nodes_add_btn"), style=discord.ButtonStyle.primary, emoji="⚔️")
        add_btn.callback = self._on_add
        rm_btn = discord.ui.Button(label=t(lang, "nodes_remove_btn"), style=discord.ButtonStyle.danger, emoji="🗑️")
        rm_btn.callback = self._on_remove
        self.add_item(add_btn)
        self.add_item(rm_btn)

    async def _on_add(self, interaction: Interaction) -> None:
        lang = await guild_lang_for(interaction.guild_id)
        cfg = await _guild_command_config(interaction.guild_id)
        if not cfg.get("nodes_calendar_channel_id"):
            await interaction.response.send_message(t(lang, "nodes_no_calendar"), ephemeral=True)
            return
        defs = (await _get(f"/bot/guilds/{interaction.guild_id}/nodes/defs") or {}).get("defs", [])
        if not defs:
            await interaction.response.send_message(t(lang, "nodes_no_defs"), ephemeral=True)
            return
        maps = (await _get(f"/bot/guilds/{interaction.guild_id}/nodes/maps") or {}).get("maps", [])
        view = AddNodeView(interaction.guild_id, lang, defs, maps)
        await interaction.response.send_message(content=t(lang, "nodes_pick_type"), view=view, ephemeral=True)

    async def _on_remove(self, interaction: Interaction) -> None:
        lang = await guild_lang_for(interaction.guild_id)
        staff = _is_staff(interaction.user) if isinstance(interaction.user, discord.Member) else False
        res = await _get(
            f"/bot/guilds/{interaction.guild_id}/nodes/removable"
            f"?user_id={interaction.user.id}&staff={'true' if staff else 'false'}"
        )
        events = (res or {}).get("events", [])
        if not events:
            await interaction.response.send_message(t(lang, "nodes_remove_empty"), ephemeral=True)
            return
        await interaction.response.send_message(view=NodeRemoveView(interaction.guild_id, lang, events), ephemeral=True)


async def _trigger_calendar_refresh(bot: commands.Bot, guild: discord.Guild | None) -> None:
    cog = bot.get_cog("Nodes")
    if cog is None or guild is None:
        return
    await cog.refresh_calendar(guild)


# ── Cog ──────────────────────────────────────────────────────────────────────

class Nodes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._calendar_msg: dict[int, int] = {}  # {guild_id: message_id} fast-path

    async def cog_load(self) -> None:
        # Sem bot.add_view: a view é reanexada a cada ciclo do update_calendar
        # (message.edit(view=...)) — padrão MassinfoView. add_view exigiria
        # custom_id em todo item; não vale a pena aqui.
        if not self.update_calendar.is_running():
            self.update_calendar.start()

    async def cog_unload(self) -> None:
        self.update_calendar.cancel()

    async def refresh_calendar(self, guild: discord.Guild) -> None:
        await self._sync_calendar(guild)

    async def _sync_calendar(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        ch_id = cfg.get("nodes_calendar_channel_id")
        if not ch_id:
            return
        channel = guild.get_channel(int(ch_id))
        if channel is None:
            return
        lang = cfg["language"]
        events = (await _get(f"/bot/guilds/{guild.id}/nodes") or {}).get("events", [])
        defs = (await _get(f"/bot/guilds/{guild.id}/nodes/defs") or {}).get("defs", [])
        emoji_map = {d["name"]: d.get("emoji") for d in defs if d.get("emoji")}

        embed = _build_calendar_embed(lang, events, emoji_map)
        view = CalendarView(lang)

        cal = await _get(f"/bot/guilds/{guild.id}/nodes/calendar")
        message_id = self._calendar_msg.get(guild.id)
        if message_id is None and cal and cal.get("message_id"):
            message_id = int(cal["message_id"])

        message = None
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            try:
                message = await channel.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                return
        else:
            try:
                await message.edit(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                return

        self._calendar_msg[guild.id] = message.id
        await _post(f"/bot/guilds/{guild.id}/nodes/calendar",
                    {"channel_id": str(channel.id), "message_id": str(message.id)})

    @tasks.loop(minutes=5)
    async def update_calendar(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._sync_calendar(guild)
            except Exception:
                pass

    @update_calendar.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Nodes(bot))