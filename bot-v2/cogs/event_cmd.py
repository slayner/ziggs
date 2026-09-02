"""Comando /event (grupo) — criar/deletar/editar/adiar eventos (CTAs) pelo Discord.

O bot é só cliente da API do site: toda mutação chama os endpoints
`/bot/events/*` (auth por BOT_API_SECRET). A máquina de estados vive no site
(`app/services/events.py`); o bot renderiza pickers e wizards.

Horário digitado em formato humano (21h, 21:30 BRT, data completa). Gate via
command_roles do site (check_command_access, "event") — default admin,
refinável por cargo no painel de permissões.
"""
from __future__ import annotations

import asyncio
import os
import re
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

# ── HTTP ──────────────────────────────────────────────────────────────────────

async def _get(path: str):
    return await http_client.get_json(
        path, tag="event_cmd", raise_on_unavailable=True,
    )


async def _post(path: str, body: dict):
    return await http_client.post_json(
        path, body, tag="event_cmd", attempts=2, queue_on_failure=False,
        raise_on_unavailable=True,
    )


async def _patch(path: str, body: dict):
    return await http_client.patch_json(
        path, body, tag="event_cmd", attempts=2, queue_on_failure=False,
        raise_on_unavailable=True,
    )


async def _delete(path: str):
    return await http_client.delete_json(
        path, tag="event_cmd", attempts=2, queue_on_failure=False,
        raise_on_unavailable=True,
    )


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


def _parse_event_time(s: str, now: datetime | None = None) -> datetime | None:
    """Interpreta horário humano e devolve a próxima ocorrência em UTC.

    Aceita 21h, 21h30, 21:30, fusos conhecidos e datas DD/MM/YYYY ou
    YYYY-MM-DD. Sem sufixo, mantém UTC como padrão histórico do comando.
    """
    value = s.strip()
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    # Também preserva ISO completo com offset, usado por integrações antigas.
    try:
        iso = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        iso = None
    if iso is not None:
        iso = iso if iso.tzinfo else iso.replace(tzinfo=timezone.utc)
        iso = iso.astimezone(timezone.utc)
        return iso if iso > now_utc else None

    match = re.fullmatch(
        r"(?:(?:(\d{4})-(\d{1,2})-(\d{1,2})|"
        r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?)\s+)?"
        r"(\d{1,2})(?:h(\d{1,2})?|:(\d{1,2}))?\s*"
        r"(BRT|BRST|UTC|GMT|CET|CEST)?",
        value,
        re.IGNORECASE,
    )
    if match is None:
        return None

    hour = int(match.group(7))
    minute = int(match.group(8) or match.group(9) or 0)
    if hour > 23 or minute > 59:
        return None

    offsets = {"UTC": 0, "GMT": 0, "BRT": -3, "BRST": -2, "CET": 1, "CEST": 2}
    suffix = (match.group(10) or "UTC").upper()
    source_tz = timezone(timedelta(hours=offsets[suffix]), name=suffix)
    source_now = now_utc.astimezone(source_tz)
    year = int(match.group(1) or match.group(6) or source_now.year)
    month = int(match.group(2) or match.group(5) or source_now.month)
    day = int(match.group(3) or match.group(4) or source_now.day)
    has_date = bool(match.group(1) or match.group(4))
    has_year = bool(match.group(1) or match.group(6))

    try:
        parsed = datetime(year, month, day, hour, minute, tzinfo=source_tz)
    except ValueError:
        return None
    if parsed <= source_now:
        if has_year:
            return None
        try:
            parsed = (
                parsed.replace(year=year + 1)
                if has_date
                else parsed + timedelta(days=1)
            )
        except ValueError:
            return None
    return parsed.astimezone(timezone.utc)


async def _rerender(
    interaction: Interaction, content: str, view: discord.ui.View | None,
) -> None:
    """Atualiza a mensagem ephemeral do componente clicado pra próxima etapa."""
    if interaction.response.is_done():
        try:
            await interaction.edit_original_response(content=content, view=view)
        except (discord.NotFound, discord.HTTPException):
            pass
        return
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


_EDIT_FIELD_ORDER = ("objetivo", "horario", "comp", "attendance")


def _edit_panel_content(
    lang: str, changes: dict[str, tuple[str, str]], error: str | None = None,
) -> str:
    lines = [error] if error else []
    if changes:
        if lines:
            lines.append("")
        lines.append(t(lang, "ev_edit_history"))
        for field in _EDIT_FIELD_ORDER:
            change = changes.get(field)
            if change is None:
                continue
            before, after = change
            label = t(lang, f"ev_field_{field}")
            lines.append(t(lang, "ev_edit_history_line", field=label, before=before, after=after))
    if lines:
        lines.append("")
    lines.append(t(lang, "ev_pick_field"))
    return "\n".join(lines)


async def _show_edit_result(
    interaction: Interaction, lang: str, ev: dict, comps: list[dict],
    changes: dict[str, tuple[str, str]], error: str | None = None,
) -> None:
    await _rerender(
        interaction,
        _edit_panel_content(lang, changes, error),
        EditFieldView(lang, ev, comps, changes),
    )


def _event_label(ev: dict, lang: str) -> str:
    dt = _parse_iso(ev.get("scheduled_at"))
    when = f" · {_fmt_utc(dt)} UTC" if dt else ""
    title = ev.get("title") or f"Evento #{ev['id']}"
    comp = f" · {ev.get('comp_name')}" if ev.get("comp_name") else ""
    return f"#{ev['id']} — {title}{when}{comp}"[:100]


# ── Picker de comp ────────────────────────────────────────────────────────────

class CompSelectView(discord.ui.View):
    def __init__(self, lang: str, comps: list[dict],
                 on_picked: Callable[[Interaction, int | None], Awaitable[None]],
                 *, show_cancel: bool = True):
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
        if show_cancel:
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
                 on_picked: Callable[[Interaction, dict], Awaitable[None]],
                 *, show_cancel: bool = True):
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
        if show_cancel:
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


# ── Modais ────────────────────────────────────────────────────────────────────

class TimeModal(discord.ui.Modal):
    def __init__(
        self, lang: str, ev: dict, comps: list[dict] | None = None,
        changes: dict[str, tuple[str, str]] | None = None,
    ):
        super().__init__(title=t(lang, "ev_field_horario"), timeout=180)
        self.lang = lang
        self.ev = ev
        self.comps = comps
        self.changes = changes
        current = _parse_iso(ev.get("scheduled_at"))
        self.value = discord.ui.TextInput(
            label=t(lang, "ev_time_input_label"),
            placeholder=t(lang, "ev_time_input_placeholder"),
            default=current.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC") if current else None,
            max_length=40,
            required=True,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: Interaction) -> None:
        parsed = _parse_event_time(str(self.value.value))
        if parsed is None:
            await interaction.response.send_message(t(self.lang, "ev_bad_time"), ephemeral=True)
            return
        await _do_patch_scheduled(
            interaction, self.lang, self.ev, parsed, self.comps, self.changes,
        )

class TitleModal(discord.ui.Modal):
    def __init__(
        self, lang: str, ev: dict, comps: list[dict],
        changes: dict[str, tuple[str, str]],
    ):
        super().__init__(title=t(lang, "ev_field_objetivo"), timeout=180)
        self.lang = lang
        self.ev = ev
        self.comps = comps
        self.changes = changes
        self.title_input = discord.ui.TextInput(
            label=t(lang, "ev_field_objetivo"),
            default=ev.get("title") or None,
            max_length=255, required=True,
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: Interaction) -> None:
        await _defer(interaction)
        res = await _patch(
            f"/bot/events/{interaction.guild_id}/{self.ev['id']}",
            {"set_title": True, "title": self.title_input.value, "actor_id": interaction.user.id},
        )
        if res is None:
            await _show_edit_result(
                interaction, self.lang, self.ev, self.comps, self.changes,
                t(self.lang, "ev_update_fail"),
            )
            return
        old_value = str(self.ev.get("title") or "—")
        new_value = str(self.title_input.value)
        self.changes["objetivo"] = (old_value, new_value)
        self.ev["title"] = new_value
        await _show_edit_result(
            interaction, self.lang, self.ev, self.comps, self.changes,
        )


class AttendanceModal(discord.ui.Modal):
    def __init__(
        self, lang: str, ev: dict, comps: list[dict],
        changes: dict[str, tuple[str, str]],
    ):
        super().__init__(title=t(lang, "ev_field_attendance"), timeout=180)
        self.lang = lang
        self.ev = ev
        self.comps = comps
        self.changes = changes
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
            await _show_edit_result(
                interaction, self.lang, self.ev, self.comps, self.changes,
                t(self.lang, "ev_update_fail"),
            )
            return
        old_value = f"{float(self.ev.get('attendance', 1)):g}"
        new_value = f"{value:g}"
        self.changes["attendance"] = (old_value, new_value)
        self.ev["attendance"] = value
        await _show_edit_result(
            interaction, self.lang, self.ev, self.comps, self.changes,
        )


# ── Editar: picker de campo ───────────────────────────────────────────────────

class EditFieldView(discord.ui.View):
    def __init__(
        self, lang: str, ev: dict, comps: list[dict],
        changes: dict[str, tuple[str, str]] | None = None,
    ):
        super().__init__(timeout=180)
        self.lang = lang
        self.ev = ev
        self.comps = comps
        self.changes = changes if changes is not None else {}
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

    async def _on_pick(self, interaction: Interaction) -> None:
        field = self.children[0].values[0]
        lang = self.lang
        ev = self.ev
        if field == "objetivo":
            await interaction.response.send_modal(
                TitleModal(lang, ev, self.comps, self.changes),
            )
        elif field == "horario":
            await interaction.response.send_modal(
                TimeModal(lang, ev, self.comps, self.changes),
            )
        elif field == "comp":
            async def on_comp(inter, comp_id):
                await _do_patch_comp(
                    inter, lang, ev, comp_id, self.comps, self.changes,
                )
            await _rerender(
                interaction, t(lang, "ev_pick_comp"),
                CompSelectView(lang, self.comps, on_comp, show_cancel=False),
            )
        elif field == "attendance":
            await interaction.response.send_modal(
                AttendanceModal(lang, ev, self.comps, self.changes),
            )


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

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "ev_cancelled"), view=None)


# ── Ações (POST/PATCH/DELETE + refresh) ───────────────────────────────────────

async def _do_create(interaction: Interaction, lang: str, objetivo: str | None,
                     comp_id: int | None, dt: datetime) -> None:
    await _defer(interaction)
    res = await _post(f"/bot/events/{interaction.guild_id}", {
        "title": objetivo or None,
        "scheduled_at": dt.isoformat(),
        "comp_id": comp_id,
        "actor_id": interaction.user.id,
        "actor_name": interaction.user.display_name,
        "request_id": str(interaction.id),
    })
    if res is None or "id" not in (res or {}):
        await interaction.followup.send(t(lang, "ev_create_fail"), ephemeral=True)
        return
    await interaction.followup.send(
        t(lang, "ev_create_done", eid=res["id"], hora=_fmt_utc(dt)), ephemeral=True)


async def _do_patch_scheduled(
    interaction: Interaction, lang: str, ev: dict, dt: datetime,
    comps: list[dict] | None = None,
    changes: dict[str, tuple[str, str]] | None = None,
) -> None:
    await _defer(interaction)
    res = await _patch(
        f"/bot/events/{interaction.guild_id}/{ev['id']}",
        {"set_scheduled_at": True, "scheduled_at": dt.isoformat(), "actor_id": interaction.user.id},
    )
    if res is None:
        if comps is not None and changes is not None:
            await _show_edit_result(
                interaction, lang, ev, comps, changes, t(lang, "ev_update_fail"),
            )
        else:
            await _rerender(interaction, t(lang, "ev_update_fail"), None)
        return
    old_dt = _parse_iso(ev.get("scheduled_at"))
    old_value = old_dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC") if old_dt else "—"
    new_value = dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    if changes is not None:
        changes["horario"] = (old_value, new_value)
    ev["scheduled_at"] = dt.isoformat()
    if comps is not None and changes is not None:
        await _show_edit_result(interaction, lang, ev, comps, changes)
    else:
        await _rerender(
            interaction,
            t(
                lang, "ev_edit_changed", field=t(lang, "ev_field_horario"),
                value=new_value,
            ),
            None,
        )


async def _do_patch_comp(interaction: Interaction, lang: str, ev: dict,
                         comp_id: int | None, comps: list[dict],
                         changes: dict[str, tuple[str, str]]) -> None:
    await _defer(interaction)
    res = await _patch(
        f"/bot/events/{interaction.guild_id}/{ev['id']}",
        {"set_comp": True, "comp_id": comp_id, "actor_id": interaction.user.id},
    )
    if res is None:
        await _show_edit_result(
            interaction, lang, ev, comps, changes, t(lang, "ev_update_fail"),
        )
        return
    old_value = ev.get("comp_name") or t(lang, "ev_no_comp")
    comp_name = next((c["name"] for c in comps if c["id"] == comp_id), None) if comp_id else None
    new_value = comp_name or t(lang, "ev_no_comp")
    changes["comp"] = (old_value, new_value)
    ev["comp_id"] = comp_id
    ev["comp_name"] = comp_name
    await _show_edit_result(interaction, lang, ev, comps, changes)


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
        try:
            lang = await guild_lang_for(interaction.guild_id)
            comps = await _get(f"/bot/events/{interaction.guild_id}/comps") or []
        except http_client.BackendUnavailable:
            return []
        cur = current.lower()
        none_label = t(lang, "ev_no_comp")
        choices = [app_commands.Choice(name=none_label, value="none")]
        choices += [
            app_commands.Choice(name=c["name"][:100], value=str(c["id"]))
            for c in comps if cur in c["name"].lower()
        ]
        return choices[:25]

    @group.command(name="create",
                   description=loc("Create a new event (CTA): objective, optional comp and time",
                                                   "cmd_desc_event_criar"))
    @app_commands.describe(
        objetivo=loc("The event's objective (optional)", "opt_desc_event_objetivo"),
        comp=loc("The event's comp (start typing to search)", "opt_desc_event_comp"),
        horario=loc("Time: 21h, 21:30 BRT or a full date", "opt_desc_event_time"),
    )
    @app_commands.rename(
        objetivo=loc("objective", "opt_name_event_objetivo"),
        comp=loc("comp", "opt_name_event_comp"),
        horario=loc("time", "opt_name_event_time"),
    )
    @app_commands.autocomplete(comp=_comp_autocomplete)
    async def criar(
        self, interaction: Interaction, horario: str, comp: str, objetivo: str | None = None,
    ) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang = await guild_lang_for(interaction.guild_id)

        dt = _parse_event_time(horario)
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
            t(lang, "ev_pick_event"),
            view=EventSelectView(lang, events, on_event, show_cancel=False),
            ephemeral=True,
        )

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
            await inter.response.send_modal(TimeModal(lang, ev))

        await interaction.response.send_message(
            t(lang, "ev_pick_event"), view=EventSelectView(lang, events, on_event), ephemeral=True)

    # ── finalizar ───────────────────────────────────────────────────────────────

    @group.command(name="finalize",
                   description=loc("Move an in-progress event to review", "cmd_desc_event_finalizar"))
    async def finalizar(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "event"):
            return
        lang, events = await self._manageable(interaction)
        in_progress = [e for e in events if e.get("state") == "in_progress"]
        if not in_progress:
            await interaction.response.send_message(t(lang, "ev_no_in_progress"), ephemeral=True)
            return

        async def on_event(inter, ev):
            await _defer(inter)
            res = await _post(f"/bot/events/{inter.guild_id}/{ev['id']}/transition", {
                "to": "review", "actor_id": inter.user.id, "actor_name": inter.user.display_name,
            })
            if res is None:
                await inter.followup.send(t(lang, "ev_update_fail"), ephemeral=True)
                return
            await inter.followup.send(
                t(lang, "ev_finalize_done", ev=_event_label(ev, lang)), ephemeral=True)
            # Dispara a criação do embed/thread de revisão imediatamente — sem
            # depender do embed_work_loop (que pode estar wedge processando
            # dezenas de embeds em série). O loop cobre no próximo tick se isto
            # falhar; isto cobre o caso comum (evento novo, loop ocupado).
            from cogs.event_embeds import _trigger_embed_refresh
            asyncio.create_task(_trigger_embed_refresh(inter.client, inter.guild, ev["id"]))

        await interaction.response.send_message(
            t(lang, "ev_pick_event"),
            view=EventSelectView(lang, in_progress, on_event, show_cancel=False),
            ephemeral=True,
        )

    # Nomes dos subcomandos localizados por locale do cliente Discord (o nome
    # canonico é inglês; pt/es via name_localizations — o Translator não cobre
    # nomes, só descrições/options). Setado no corpo da classe pq Group.command
    # não aceita name_localizations como kwarg.
    criar.name_localizations = name_locs("criar", "create", "crear")
    deletar.name_localizations = name_locs("deletar", "delete", "eliminar")
    editar.name_localizations = name_locs("editar", "edit", "editar")
    adiar.name_localizations = name_locs("adiar", "reschedule", "aplazar")
    finalizar.name_localizations = name_locs("finalizar", "finalize", "finalizar")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventCmd())
