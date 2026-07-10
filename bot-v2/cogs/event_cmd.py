"""Comando /event (grupo) — criar/deletar/editar/adiar eventos (CTAs) pelo Discord.

O bot é só cliente da API do site: toda mutação chama os endpoints
`/bot/events/*` (auth por BOT_API_SECRET). A máquina de estados vive no site
(`app/services/events.py`); o bot renderiza pickers e wizards.

Horário UTC via lupa paginada (faixas de 25min ◀️/▶️ até 36h → minuto exato),
mesma UX do /nodes. Gate via command_roles do site (check_command_access,
"event") — default admin, refinável por cargo no painel de permissões.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands, Interaction
from discord.ext import commands

import http_client
from cogs.general import check_command_access, guild_lang_for
from i18n import t
from localization import loc, name_locs

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# Lupa de horário — espelho de cogs/nodes.py.
TIME_BUCKET_MIN       = 25
TIME_HORIZON_H        = 36
TIME_TOTAL_FAIXAS     = TIME_HORIZON_H * 60 // TIME_BUCKET_MIN + 1   # 87
TIME_FAIXAS_PER_PAGE  = 23
TIME_PAGES            = -(-TIME_TOTAL_FAIXAS // TIME_FAIXAS_PER_PAGE)  # 4


# ── HTTP ──────────────────────────────────────────────────────────────────────

async def _get(path: str):
    return await http_client.get_json(path)


async def _post(path: str, body: dict):
    return await http_client.post_json(path, body)


async def _patch(path: str, body: dict):
    return await http_client.patch_json(path, body)


async def _delete(path: str):
    return await http_client.delete_json(path)


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _parse_utc_hhmm(s: str) -> datetime | None:
    """'HH:MM', 'HHhMM' ou só 'HH' -> próxima ocorrência em UTC (hoje se ainda
    não passou, senão amanhã). Também aceita data completa ISO
    ('YYYY-MM-DD HH:MM') pra agendar além das próximas 24h."""
    s = s.strip()
    dt = _parse_iso(s)
    if dt is not None:
        return dt
    parts = s.lower().replace("h", ":").split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    now = datetime.now(timezone.utc)
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt if dt > now else dt + timedelta(days=1)


def _fmt_eta(minutes: int) -> str:
    if minutes <= 0:
        return "agora"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h{m:02d}min"
    if h:
        return f"{h} hora{'s' if h > 1 else ''}"
    return f"{m} minuto{'s' if m > 1 else ''}"


async def _rerender(interaction: Interaction, content: str, view: discord.ui.View) -> None:
    """Atualiza a mensagem ephemeral do componente clicado pra próxima etapa."""
    try:
        await interaction.response.edit_message(content=content, view=view)
    except discord.InteractionResponded:
        try:
            await interaction.edit_original_response(content=content, view=view)
        except (discord.NotFound, discord.HTTPException):
            pass
    except (discord.NotFound, discord.HTTPException):
        pass


async def _defer(interaction: Interaction) -> None:
    try:
        await interaction.response.defer()
    except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
        pass


async def _refresh_massinfo(interaction: Interaction) -> None:
    cog = interaction.client.get_cog("Events")
    if cog is None or interaction.guild is None:
        return
    try:
        await cog.refresh_massinfo(interaction.guild)
    except Exception:
        pass  # best-effort — o polling de 5s cobre


def _event_label(ev: dict, lang: str) -> str:
    title = ev.get("title") or "—"
    dt = _parse_iso(ev.get("scheduled_at"))
    when = f" · {_fmt_utc(dt)} UTC" if dt else ""
    comp = f" · {ev.get('comp_name')}" if ev.get("comp_name") else ""
    return f"#{ev['id']} — {title}{when}{comp}"[:100]


# ── Lupa de horário ───────────────────────────────────────────────────────────

class TimeLupaView(discord.ui.View):
    """Lupa paginada (faixa 25min → minuto exato, até 36h à frente) que devolve
    um datetime UTC via `on_picked(interaction, dt)`. Reusada por criar/editar-
    horário/adiar. Renderização espelhada de cogs/nodes.py:AddNodeView."""

    def __init__(self, lang: str, on_picked: Callable[[Interaction, datetime], Awaitable[None]],
                 prompt_key: str = "ev_pick_time"):
        super().__init__(timeout=300)
        self.lang = lang
        self.on_picked = on_picked
        self.prompt_key = prompt_key
        self.spawn_at: datetime | None = None
        self._time_base: datetime | None = None
        self._time_page = 0
        self._time_block_start: datetime | None = None
        self._show_range()

    def _prompt(self) -> str:
        return t(self.lang, self.prompt_key)

    def _show_range(self) -> None:
        if self._time_base is None:
            self._time_base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.clear_items()
        now = datetime.now(timezone.utc)
        page = self._time_page
        start_idx = page * TIME_FAIXAS_PER_PAGE
        end_idx = min(start_idx + TIME_FAIXAS_PER_PAGE, TIME_TOTAL_FAIXAS)
        options: list[discord.SelectOption] = []
        if page > 0:
            options.append(discord.SelectOption(label=t(self.lang, "ev_time_prev"), value="__prev__"))
        for idx in range(start_idx, end_idx):
            s = self._time_base + timedelta(minutes=idx * TIME_BUCKET_MIN)
            e = s + timedelta(minutes=TIME_BUCKET_MIN)
            eta_s = _fmt_eta(-(-int((s - now).total_seconds()) // 60))
            eta_e = _fmt_eta(-(-int((e - now).total_seconds()) // 60))
            pref = "" if eta_s == "agora" else "em "
            options.append(discord.SelectOption(
                label=f"{s:%H:%M}–{e:%H:%M} UTC  ({pref}{eta_s} - {eta_e})",
                value=str(int(s.timestamp())),
            ))
        if page < TIME_PAGES - 1:
            options.append(discord.SelectOption(label=t(self.lang, "ev_time_next"), value="__next__"))
        select = discord.ui.Select(
            placeholder=t(self.lang, "ev_pick_time_range", page=page + 1, pages=TIME_PAGES),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_range
        self.add_item(select)
        cancel = discord.ui.Button(label=t(self.lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _show_minute(self, block_start: datetime) -> None:
        self._time_block_start = block_start
        self.clear_items()
        now = datetime.now(timezone.utc)
        options: list[discord.SelectOption] = []
        for k in range(TIME_BUCKET_MIN):
            s = block_start + timedelta(minutes=k)
            eta = _fmt_eta(-(-int((s - now).total_seconds()) // 60))
            pref = "" if eta == "agora" else "em "
            options.append(discord.SelectOption(
                label=f"{s:%H:%M} UTC  ({pref}{eta})", value=str(int(s.timestamp())),
            ))
        select = discord.ui.Select(
            placeholder=t(self.lang, "ev_pick_time_minute"),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_minute
        self.add_item(select)
        back = discord.ui.Button(label=t(self.lang, "ev_back"), style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)
        cancel = discord.ui.Button(label=t(self.lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _show_confirm(self) -> None:
        self.clear_items()
        confirm = discord.ui.Button(label=t(self.lang, "ev_confirm"), style=discord.ButtonStyle.success)
        confirm.callback = self._on_confirm
        back = discord.ui.Button(label=t(self.lang, "ev_back"), style=discord.ButtonStyle.secondary)
        back.callback = self._on_back_to_minute
        self.add_item(confirm)
        self.add_item(back)
        cancel = discord.ui.Button(label=t(self.lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_range(self, interaction: Interaction) -> None:
        val = self.children[0].values[0]
        if val == "__next__":
            self._time_page = min(self._time_page + 1, TIME_PAGES - 1)
            self._show_range()
            await interaction.response.edit_message(content=self._prompt(), view=self)
            return
        if val == "__prev__":
            self._time_page = max(self._time_page - 1, 0)
            self._show_range()
            await interaction.response.edit_message(content=self._prompt(), view=self)
            return
        block = datetime.fromtimestamp(int(val), tz=timezone.utc)
        self._show_minute(block)
        await interaction.response.edit_message(content=self._prompt(), view=self)

    async def _on_minute(self, interaction: Interaction) -> None:
        self.spawn_at = datetime.fromtimestamp(int(self.children[0].values[0]), tz=timezone.utc)
        self._show_confirm()
        await interaction.response.edit_message(
            content=f"{self._prompt()}\n**{_fmt_utc(self.spawn_at)} UTC**", view=self)

    async def _on_back(self, interaction: Interaction) -> None:
        self._show_range()
        await interaction.response.edit_message(content=self._prompt(), view=self)

    async def _on_back_to_minute(self, interaction: Interaction) -> None:
        if self._time_block_start is not None:
            self._show_minute(self._time_block_start)
        else:
            self._show_range()
        await interaction.response.edit_message(content=self._prompt(), view=self)

    async def _on_confirm(self, interaction: Interaction) -> None:
        if self.spawn_at is None:
            await interaction.response.defer()
            return
        await self.on_picked(interaction, self.spawn_at)

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Picker de comp ────────────────────────────────────────────────────────────

class CompSelectView(discord.ui.View):
    def __init__(self, lang: str, comps: list[dict],
                 on_picked: Callable[[Interaction, int | None], Awaitable[None]]):
        super().__init__(timeout=180)
        self.lang = lang
        self.on_picked = on_picked
        options = [discord.SelectOption(label=c["name"][:100], value=str(c["id"]))
                   for c in comps[:25]]
        options.append(discord.SelectOption(label=t(lang, "ev_no_comp"), value="none"))
        select = discord.ui.Select(
            placeholder=t(lang, "ev_pick_comp"), min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_pick
        self.add_item(select)
        cancel = discord.ui.Button(label=t(lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_pick(self, interaction: Interaction) -> None:
        val = self.children[0].values[0]
        comp_id = int(val) if val != "none" else None
        await self.on_picked(interaction, comp_id)

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Picker de evento (não-finalizados) ────────────────────────────────────────

class EventSelectView(discord.ui.View):
    def __init__(self, lang: str, events: list[dict],
                 on_picked: Callable[[Interaction, dict], Awaitable[None]]):
        super().__init__(timeout=180)
        self.lang = lang
        self.on_picked = on_picked
        self._events = events
        options = [discord.SelectOption(label=_event_label(e, lang), value=str(e["id"]))
                   for e in events[:25]]
        select = discord.ui.Select(
            placeholder=t(lang, "ev_pick_event"), min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_pick
        self.add_item(select)
        cancel = discord.ui.Button(label=t(lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_pick(self, interaction: Interaction) -> None:
        eid = int(self.children[0].values[0])
        ev = next((e for e in self._events if e["id"] == eid), None)
        if ev is None:
            await interaction.response.edit_message(content=t(self.lang, "ev_fetch_fail"), view=None)
            return
        await self.on_picked(interaction, ev)

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Modais (editar: objetivo + attendance) ────────────────────────────────────

class TitleModal(discord.ui.Modal):
    def __init__(self, lang: str, ev: dict):
        super().__init__(title=t(lang, "ev_field_objetivo"), timeout=180)
        self.lang = lang
        self.ev = ev
        self.title = discord.ui.TextInput(
            label=t(lang, "ev_field_objetivo"),
            default=ev.get("title") or None,
            max_length=255, required=True,
        )
        self.add_item(self.title)

    async def on_submit(self, interaction: Interaction) -> None:
        await _defer(interaction)
        res = await _patch(
            f"/bot/events/{interaction.guild_id}/{self.ev['id']}",
            {"set_title": True, "title": self.title.value, "actor_id": interaction.user.id},
        )
        if res is None:
            await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        await interaction.followup.send(
            t(self.lang, "ev_edit_done_field", field=t(self.lang, "ev_field_objetivo")), ephemeral=True)
        await _refresh_massinfo(interaction)


class AttendanceModal(discord.ui.Modal):
    def __init__(self, lang: str, ev: dict):
        super().__init__(title=t(lang, "ev_field_attendance"), timeout=180)
        self.lang = lang
        self.ev = ev
        self.val = discord.ui.TextInput(
            label=t(lang, "ev_field_attendance"),
            placeholder="1 · 0.5 · 1.5",
            default=str(ev.get("attendance") if "attendance" in ev else "1"),
            max_length=10, required=True,
        )
        self.add_item(self.val)

    async def on_submit(self, interaction: Interaction) -> None:
        try:
            value = float(str(self.val.value).strip().replace(",", "."))
            assert value >= 0
        except (ValueError, AssertionError):
            await interaction.response.send_message(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        await _defer(interaction)
        res = await _patch(
            f"/bot/events/{interaction.guild_id}/{self.ev['id']}",
            {"set_attendance": True, "attendance": value, "actor_id": interaction.user.id},
        )
        if res is None:
            await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        await interaction.followup.send(
            t(self.lang, "ev_edit_done_field", field=t(self.lang, "ev_field_attendance")), ephemeral=True)
        await _refresh_massinfo(interaction)


# ── Editar: picker de campo ───────────────────────────────────────────────────

class EditFieldView(discord.ui.View):
    def __init__(self, lang: str, ev: dict, comps: list[dict]):
        super().__init__(timeout=180)
        self.lang = lang
        self.ev = ev
        self.comps = comps
        options = [
            discord.SelectOption(label=t(lang, "ev_field_objetivo"), value="objetivo"),
            discord.SelectOption(label=t(lang, "ev_field_horario"), value="horario"),
            discord.SelectOption(label=t(lang, "ev_field_comp"), value="comp"),
            discord.SelectOption(label=t(lang, "ev_field_attendance"), value="attendance"),
        ]
        select = discord.ui.Select(
            placeholder=t(lang, "ev_pick_field"), min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_pick
        self.add_item(select)
        cancel = discord.ui.Button(label=t(lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_pick(self, interaction: Interaction) -> None:
        field = self.children[0].values[0]
        lang = self.lang
        ev = self.ev
        if field == "objetivo":
            await interaction.response.send_modal(TitleModal(lang, ev))
        elif field == "horario":
            async def on_time(inter, dt):
                await _do_patch_scheduled(inter, lang, ev, dt)
            await _rerender(interaction, t(lang, "ev_pick_time"), TimeLupaView(lang, on_time))
        elif field == "comp":
            async def on_comp(inter, comp_id):
                await _do_patch_comp(inter, lang, ev, comp_id, self.comps)
            await _rerender(interaction, t(lang, "ev_pick_comp"), CompSelectView(lang, self.comps, on_comp))
        elif field == "attendance":
            await interaction.response.send_modal(AttendanceModal(lang, ev))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Deletar: confirmação ──────────────────────────────────────────────────────

class DeleteConfirmView(discord.ui.View):
    def __init__(self, lang: str, ev: dict):
        super().__init__(timeout=120)
        self.lang = lang
        self.ev = ev
        confirm = discord.ui.Button(label=t(lang, "ev_delete_btn"), style=discord.ButtonStyle.danger)
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label=t(lang, "ev_cancel"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_confirm(self, interaction: Interaction) -> None:
        await _defer(interaction)
        res = await _delete(
            f"/bot/events/{interaction.guild_id}/{self.ev['id']}?actor_id={interaction.user.id}"
        )
        if res is None:
            await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        await interaction.followup.send(
            t(self.lang, "ev_delete_done", eid=self.ev["id"]), ephemeral=True)
        await _refresh_massinfo(interaction)

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Ações (POST/PATCH/DELETE + refresh) ───────────────────────────────────────

async def _do_create(interaction: Interaction, lang: str, objetivo: str,
                     comp_id: int | None, dt: datetime) -> None:
    await _defer(interaction)
    res = await _post(f"/bot/events/{interaction.guild_id}", {
        "title": objetivo,
        "scheduled_at": dt.isoformat(),
        "comp_id": comp_id,
        "actor_id": interaction.user.id,
        "actor_name": interaction.user.display_name,
    })
    if res is None or "id" not in (res or {}):
        await interaction.followup.send(t(lang, "ev_create_fail"), ephemeral=True)
        return
    await interaction.followup.send(
        t(lang, "ev_create_done", eid=res["id"], hora=_fmt_utc(dt)), ephemeral=True)
    await _refresh_massinfo(interaction)


async def _do_patch_scheduled(interaction: Interaction, lang: str, ev: dict, dt: datetime) -> None:
    await _defer(interaction)
    res = await _patch(
        f"/bot/events/{interaction.guild_id}/{ev['id']}",
        {"set_scheduled_at": True, "scheduled_at": dt.isoformat(), "actor_id": interaction.user.id},
    )
    if res is None:
        await interaction.followup.send(t(lang, "ev_update_fail"), ephemeral=True)
        return
    await interaction.followup.send(
        t(lang, "ev_reschedule_done", eid=ev["id"], hora=_fmt_utc(dt)), ephemeral=True)
    await _refresh_massinfo(interaction)


async def _do_patch_comp(interaction: Interaction, lang: str, ev: dict,
                         comp_id: int | None, comps: list[dict]) -> None:
    await _defer(interaction)
    res = await _patch(
        f"/bot/events/{interaction.guild_id}/{ev['id']}",
        {"set_comp": True, "comp_id": comp_id, "actor_id": interaction.user.id},
    )
    if res is None:
        await interaction.followup.send(t(lang, "ev_update_fail"), ephemeral=True)
        return
    notified = (res or {}).get("notified_signups") or []
    comp_name = next((c["name"] for c in comps if c["id"] == comp_id), None) if comp_id else None
    if notified:
        # DMa cada inscrito avisando que a comp mudou e ele precisa repingar.
        for su in notified:
            await _send_comp_changed_dm(interaction, lang, ev, su, comp_name)
        await interaction.followup.send(
            t(lang, "ev_comp_changed_summary", n=len(notified), comp=comp_name or "—"), ephemeral=True)
    else:
        await interaction.followup.send(
            t(lang, "ev_edit_done_field", field=t(lang, "ev_field_comp")), ephemeral=True)
    await _refresh_massinfo(interaction)


async def _send_comp_changed_dm(interaction: Interaction, lang: str, ev: dict,
                                su: dict, comp_name: str | None) -> None:
    user = None
    guild = interaction.guild
    if guild is not None:
        user = guild.get_member(su.get("user_id"))
    if user is None:
        try:
            user = await interaction.client.fetch_user(su.get("user_id"))
        except (discord.NotFound, discord.HTTPException):
            return
    try:
        await user.send(t(lang, "ev_comp_changed_dm", eid=ev["id"],
                          title=ev.get("title") or "—", comp=comp_name or "—"))
    except (discord.Forbidden, discord.HTTPException):
        pass  # DMs fechadas — nada a fazer


# ── Cog / grupo ───────────────────────────────────────────────────────────────

class EventCmd(commands.Cog):
    group = app_commands.Group(name="event",
                               description=loc("Manage events (CTAs): create, delete, edit and reschedule",
                                               "cmd_group_event"))
    group.name_localizations = name_locs("evento", "event", "evento")

    async def _manageable(self, interaction: Interaction) -> tuple[str, list[dict]]:
        lang = await guild_lang_for(interaction.guild_id)
        events = await _get(f"/bot/events/{interaction.guild_id}/manageable")
        return lang, events or []

    # ── criar ──────────────────────────────────────────────────────────────────

    async def _comp_autocomplete(self, interaction: Interaction, current: str
                                 ) -> list[app_commands.Choice[str]]:
        lang = await guild_lang_for(interaction.guild_id)
        comps = await _get(f"/bot/events/{interaction.guild_id}/comps") or []
        cur = current.lower()
        none_label = t(lang, "ev_no_comp")
        choices = [app_commands.Choice(name=none_label, value="none")] if cur in none_label.lower() or not cur else []
        choices += [
            app_commands.Choice(name=c["name"][:100], value=str(c["id"]))
            for c in comps if cur in c["name"].lower()
        ]
        return choices[:25]

    @group.command(name="create",
                   description=loc("Create a new event (CTA): objective, comp and UTC time",
                                                   "cmd_desc_event_criar"))
    @app_commands.describe(
        objetivo=loc("The event's objective (its name/title)", "opt_desc_event_objetivo"),
        comp=loc("The event's comp (start typing to search)", "opt_desc_event_comp"),
        utc=loc("UTC time (HH:MM, next occurrence)", "opt_desc_event_utc"),
    )
    @app_commands.rename(
        objetivo=loc("objective", "opt_name_event_objetivo"),
        comp=loc("comp", "opt_name_event_comp"),
        utc=loc("utc", "opt_name_event_utc"),
    )
    @app_commands.autocomplete(comp=_comp_autocomplete)
    async def criar(self, interaction: Interaction, objetivo: str, comp: str, utc: str) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang = await guild_lang_for(interaction.guild_id)

        dt = _parse_utc_hhmm(utc)
        if dt is None:
            await interaction.response.send_message(t(lang, "ev_bad_time"), ephemeral=True)
            return
        comp_id = int(comp) if comp != "none" else None
        await _do_create(interaction, lang, objetivo, comp_id, dt)

    # ── deletar ────────────────────────────────────────────────────────────────

    @group.command(name="delete",
                   description=loc("Delete a not-yet-finalized event", "cmd_desc_event_deletar"))
    async def deletar(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang, events = await self._manageable(interaction)
        if not events:
            await interaction.response.send_message(t(lang, "ev_no_events"), ephemeral=True)
            return

        async def on_event(inter, ev):
            await _rerender(inter, t(lang, "ev_delete_confirm", ev=_event_label(ev, lang)),
                            DeleteConfirmView(lang, ev))

        await interaction.response.send_message(
            t(lang, "ev_pick_event"), view=EventSelectView(lang, events, on_event), ephemeral=True)

    # ── editar ──────────────────────────────────────────────────────────────────

    @group.command(name="edit",
                   description=loc("Edit objective, time, comp or attendance points of an event",
                                                   "cmd_desc_event_editar"))
    async def editar(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang, events = await self._manageable(interaction)
        if not events:
            await interaction.response.send_message(t(lang, "ev_no_events"), ephemeral=True)
            return
        comps = (await _get(f"/bot/events/{interaction.guild_id}/comps")) or []

        async def on_event(inter, ev):
            await _rerender(inter, t(lang, "ev_pick_field"),
                            EditFieldView(lang, ev, comps))

        await interaction.response.send_message(
            t(lang, "ev_pick_event"), view=EventSelectView(lang, events, on_event), ephemeral=True)

    # ── adiar ───────────────────────────────────────────────────────────────────

    @group.command(name="reschedule",
                   description=loc("Reschedule an event (new UTC time)", "cmd_desc_event_adiar"))
    async def adiar(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang, events = await self._manageable(interaction)
        if not events:
            await interaction.response.send_message(t(lang, "ev_no_events"), ephemeral=True)
            return

        async def on_event(inter, ev):
            async def on_time(inter, dt):
                await _do_patch_scheduled(inter, lang, ev, dt)
            await _rerender(inter, t(lang, "ev_pick_time"),
                            TimeLupaView(lang, on_time, prompt_key="ev_pick_time"))

        await interaction.response.send_message(
            t(lang, "ev_pick_event"), view=EventSelectView(lang, events, on_event), ephemeral=True)

    # Nomes dos subcomandos localizados por locale do cliente Discord (o nome
    # canonico é inglês; pt/es via name_localizations — o Translator não cobre
    # nomes, só descrições/options). Setado no corpo da classe pq Group.command
    # não aceita name_localizations como kwarg.
    criar.name_localizations = name_locs("criar", "create", "crear")
    deletar.name_localizations = name_locs("deletar", "delete", "eliminar")
    editar.name_localizations = name_locs("editar", "edit", "editar")
    adiar.name_localizations = name_locs("adiar", "reschedule", "aplazar")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventCmd())