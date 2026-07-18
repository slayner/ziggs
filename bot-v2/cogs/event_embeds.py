"""Embed por evento (thread 📑 EVENTO #N) — fluxo de 4 estados
(scheduled → in_progress → review → finalized).

Fonte da verdade no site (`/bot/events/{g}/{eid}/embed`). O bot:
  1. No callout (IN_PROGRESS→REVIEW) cria a mensagem de embed no canal de
     eventos e grava os ids via /embed-synced.
  2. A cada mutação o site marca `event_embed_dirty`; o loop `embed_work_loop`
     (10s) puxa /embed-work, rebusca o DTO, reedita o embed e limpa o flag.
  3. Botões gerenciadores (➕ add · ✏️ % · 🫷 remover · 💰 split · ✅ finalizar)
     fazem gate de cargo local e chamam os endpoints /bot/events/* de mutação.
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import Interaction
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path, tag="event_embeds")


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body, tag="event_embeds")


async def _patch(path: str, body: dict) -> Optional[dict]:
    return await http_client.patch_json(path, body, tag="event_embeds")


async def _delete(path: str) -> Optional[dict]:
    return await http_client.delete_json(path)


async def _dismiss_ephemeral(interaction: Interaction) -> None:
    """Apaga o ephemeral do componente clicado, sem reenviar nada. Mesmo truque
    de _replace_ephemeral em cogs/events.py: defer() sem thinking=True numa
    interação de componente manda DEFERRED_UPDATE_MESSAGE em vez de mensagem
    nova, deixando a mensagem antiga endereçável como "@original" pra
    delete_original_response() apagar (edit_message com zero-width space
    deixava um balão vazio pra sempre no canal)."""
    try:
        await interaction.response.defer()
    except (discord.InteractionResponded, discord.HTTPException):
        pass
    try:
        await interaction.delete_original_response()
    except (discord.NotFound, discord.HTTPException):
        pass


def _is_manager(member: discord.Member) -> bool:
    # ponytail: gate local espelhando events.manage — administrador do server
    # sempre passa; gate fino por cargo fica no site (require_permission).
    return member.guild_permissions.administrator or member.guild_permissions.manage_events


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt_silver(v: int) -> str:
    v = int(v or 0)
    return f"{v:,}".replace(",", ".")


def _fit_field(lines: list[str], cap: int = 1024) -> str:
    out: list[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > cap:
            out.append("…")
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def _build_event_embed(lang: str, guild_id: int, eid: int, dto: dict) -> discord.Embed:
    ev = dto["event"]
    embed = discord.Embed(color=discord.Color.blurple())
    bb = ev.get("battleboard_url")
    if bb:
        embed.url = bb

    # "Título" vira um field em negrito (não embed.title) — pedido explícito:
    # a data do evento entra aqui, não o title nativo do embed.
    started = _parse_iso(ev.get("started_at") or ev.get("scheduled_at"))
    date_str = started.strftime("%d/%m/%y %H:%M") if started else "—"
    embed.add_field(
        name=f"**📑 {t(lang, 'ev_title')} #{eid} — {date_str} UTC**",
        value="​", inline=False,
    )

    # Cabeçalho: 💀 regear descontado do split · 👥 pessoas · 💰 tab.
    n_people = len(ev.get("participants") or [])
    tab = ev.get("tab_value") or 0
    payout = ev.get("payout")
    regear_deducted = int((payout or {}).get("total_regear") or 0)
    embed.add_field(name=f"💀 {_fmt_silver(regear_deducted)}", value="​", inline=True)
    embed.add_field(name=f"👥 {n_people}", value="​", inline=True)
    embed.add_field(name=f"💰 {_fmt_silver(tab)}", value="​", inline=True)

    # Participantes em 3 colunas com badge ⚠️<50%.
    parts = ev.get("participants") or []
    if parts:
        n = len(parts)
        per_col = (n + 2) // 3
        cols = [parts[i * per_col:(i + 1) * per_col] for i in range(3)]
        for i, col in enumerate(cols):
            if not col:
                embed.add_field(name="​", value="​", inline=True)
                continue
            lines = []
            for p in col:
                pct = p.get("percent") or 0
                badge = f"`⚠️{pct}%`" if pct < 50 else f"`{pct}%`"
                trial = " 🐥" if p.get("is_trial") else ""
                lines.append(f"{badge} <@{p['user_id']}>{trial}")
            embed.add_field(
                name=f"#{i * per_col + 1}–{i * per_col + len(col)}",
                value=_fit_field(lines), inline=True,
            )
    else:
        embed.add_field(name="", value=t(lang, "ev_no_participants"), inline=False)

    # Split definido? (step tab_value completo) — decide o mark dos nodes não
    # capturados: ❌ depois do split, ▫️ antes (ainda dá pra capturar).
    split_defined = any(s.get("step") == "tab_value" and s.get("completed")
                        for s in (ev.get("verification") or []))

    # Nodes próximos do callout. ✅ capturado (scout+ganho numa 2ª linha), ❌
    # perdido (só depois do split), ▫️ pendente (antes do split).
    nodes = dto.get("nodes") or []
    if nodes:
        lines = []
        for nd in nodes[:20]:
            spawn = _parse_iso(nd.get("spawn_at"))
            ts = f"<t:{int(spawn.timestamp())}:t>" if spawn else "?"
            base = f"· **{nd['node_type']}** · 🗺️ {nd['map_name']}"
            if nd.get("captured"):
                who = f"<@{nd['scout_id']}>" if nd.get("scout_id") else (nd.get("scout_name") or "—")
                lines.append(f"​ ✅ {ts} {base}")
                lines.append(f"​  ⤷ 🔎 {who} +{_fmt_silver(nd.get('scout_amount') or 0)}")
            elif split_defined:
                lines.append(f"​ ❌ {ts} {base}")
            else:
                lines.append(f"​ ▫️ {ts} {base}")
        embed.add_field(name=t(lang, "ev_nodes_title", n=len(nodes)),
                        value=_fit_field(lines), inline=False)

    # Loggers (lootlog anônimo) — quem mandou .csv/log e a fatia que coube a
    # cada um (logger_pool dividido por peso). Espelho do "🪵 Loggers" do bot-v1.
    loggers = (payout or {}).get("logger_payouts") or []
    if loggers:
        # Mostra só a % do logger_pool de cada logger (sem silver, sem qtd de
        # logs) — espelho do que o user pediu: "porcentagem da porcentagem".
        lines = [f"<@{lp.get('user_id')}> {lp.get('percent') or 0}%"
                 for lp in loggers if lp.get("user_id")]
        if lines:
            embed.add_field(name=t(lang, "ev_loggers", n=len(lines)),
                            value=_fit_field(lines), inline=False)

    return embed


# ── Modais ───────────────────────────────────────────────────────────────────

class PercentModal(discord.ui.Modal):
    def __init__(self, lang: str, event_id: int, participant_id: int):
        super().__init__(title=t(lang, "ev_enter_percent"), timeout=120)
        self.lang = lang
        self.event_id = event_id
        self.participant_id = participant_id
        self.pct = discord.ui.TextInput(
            label=t(lang, "ev_enter_percent"), placeholder="50",
            min_length=1, max_length=4, required=True,
        )
        self.add_item(self.pct)

    async def on_submit(self, interaction: Interaction) -> None:
        try:
            value = int(str(self.pct.value).strip())
            assert 0 <= value <= 100
        except (ValueError, AssertionError):
            await interaction.response.send_message(
                t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        # acka antes do PATCH pra não estourar o timeout de 3s do Discord
        # (visto em produção: PATCH lento → "Unknown interaction" 10062 em
        # qualquer resposta tentada depois).
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        res = await _patch(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/participants/{self.participant_id}",
            {"percent": value, "actor_id": interaction.user.id},
        )
        if res is None:
            await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        # Sem mensagem de confirmação — já deferiu acima, modal só fecha.
        asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


# ── Captura de nodes dentro do fluxo de split ─────────────────────────────────
# Junto com o 💰 Definir split: depois da tab, se há nodes próximos, pergunta
# quais foram capturados (multi-select) e o valor vendido de cada um (modal com
# 1 campo por node — limite 5 imposto pelo Discord).

class SplitNodesValueModal(discord.ui.Modal):
    """Um TextInput por node capturado (até 5) — valor vendido. Em branco NÃO
    conta como definido: se sobrar algum, volta à etapa de marcar os nodes
    (re-envia o dropdown) em vez de gravar 0."""

    def __init__(self, lang: str, event_id: int, selected: list[tuple[int, str]],
                 dropdown_msg: Optional[discord.Message] = None,
                 nodes: list[dict] | None = None,
                 preselected_ids: list[int] | None = None):
        super().__init__(title=t(lang, "ev_split_nodes_values"), timeout=180)
        self.lang = lang
        self.event_id = event_id
        self.dropdown_msg = dropdown_msg
        self.nodes = nodes or []
        self.preselected_ids = preselected_ids or []
        self.items: list[tuple[discord.ui.TextInput, int]] = []
        for nid, label in selected[:5]:
            ti = discord.ui.TextInput(label=label[:45], placeholder="0",
                                       required=False, max_length=12)
            self.add_item(ti)
            self.items.append((ti, nid))

    async def _close_question(self) -> None:
        # Apaga o ephemeral do dropdown (a "pergunta") — chamado tanto no sucesso
        # quanto no go-back (re-envia outro dropdown no lugar).
        if self.dropdown_msg is None:
            return
        try:
            await self.dropdown_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            pass

    async def on_submit(self, interaction: Interaction) -> None:
        # acka antes de qualquer coisa pra não estourar o timeout de 3s.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        # Node sem preço (campo em branco) → volta à etapa anterior (dropdown),
        # preservando a seleção feita. Não grava nada.
        if any(not str(ti.value).strip() for ti, _ in self.items):
            await interaction.followup.send(
                t(self.lang, "ev_split_nodes_intro"),
                view=SplitNodesView(self.lang, self.event_id, self.nodes,
                                    preselected_ids=self.preselected_ids),
                ephemeral=True)
            await self._close_question()
            return
        # Todos com preço → captura cada um, apaga a pergunta, refresh.
        for ti, nid in self.items:
            raw = str(ti.value).strip().replace(".", "").replace(",", "")
            sold = int(raw) if raw.isdigit() else 0
            await _post(
                f"/bot/events/{interaction.guild_id}/{self.event_id}/nodes/{nid}/claim",
                {"captured": True, "sold_value": sold, "actor_id": interaction.user.id},
            )
        await self._close_question()
        asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


class SplitNodesSelect(discord.ui.Select):
    def __init__(self, lang: str, nodes: list[dict],
                 preselected_ids: list[int] | None = None):
        opts = []
        # pre = seleção herdada (go-back do modal) ∪ nodes já capturados no site
        # (sync: o dropdown abre refletindo o estado do site, não em branco).
        pre = set(preselected_ids or [])
        for nd in nodes[:25]:
            if nd.get("captured"):
                pre.add(nd["node_log_id"])
            spawn = _parse_iso(nd.get("spawn_at"))
            ts = f"<t:{int(spawn.timestamp())}:t>" if spawn else "?"
            label = f"{ts} {nd['node_type']} · {nd['map_name']}"
            opts.append(discord.SelectOption(
                label=label[:100], value=str(nd["node_log_id"]),
                description=(nd.get("scout_name") or "—")[:100],
                default=nd["node_log_id"] in pre))
        # max 5: o modal de valor só cabe 5 inputs (limite do Discord).
        maxv = min(len(opts), 5) if opts else 1
        super().__init__(placeholder=t(lang, "ev_split_nodes_pick"), min_values=0,
                         max_values=maxv,
                         options=opts or [discord.SelectOption(label="(sem nodes)", value="-1")])
        self.lang = lang
        self.selected_ids: list[int] = list(pre)

    async def callback(self, interaction: Interaction) -> None:
        self.selected_ids = [int(v) for v in self.values if v != "-1"]
        for opt in self.options:
            opt.default = opt.value in {str(i) for i in self.selected_ids}
        await interaction.response.edit_message(view=self.view)


class SplitNodesView(discord.ui.View):
    def __init__(self, lang: str, event_id: int, nodes: list[dict],
                 preselected_ids: list[int] | None = None):
        super().__init__(timeout=300)
        self.lang = lang
        self.event_id = event_id
        self.nodes = nodes
        self.nodes_by_id = {nd["node_log_id"]: nd for nd in nodes}
        self.select = SplitNodesSelect(lang, nodes, preselected_ids)
        self.add_item(self.select)
        confirm = discord.ui.Button(label=t(lang, "ev_split_nodes_confirm"),
                                    style=discord.ButtonStyle.success)
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        skip = discord.ui.Button(label=t(lang, "ev_split_nodes_skip"),
                                 style=discord.ButtonStyle.secondary)
        skip.callback = self._on_skip
        self.add_item(skip)

    def _selected_label(self, nd: dict) -> tuple[int, str]:
        spawn = _parse_iso(nd.get("spawn_at"))
        ts = f"<t:{int(spawn.timestamp())}:t>" if spawn else "?"
        return nd["node_log_id"], f"💵 {nd['node_type']} · {nd['map_name']} ({ts})"

    async def _on_confirm(self, interaction: Interaction) -> None:
        sel = self.select.selected_ids
        if not sel:
            await self._finish(interaction)
            return
        batch = [self._selected_label(self.nodes_by_id[n])
                 for n in sel if n in self.nodes_by_id]
        if not batch:
            await self._finish(interaction)
            return
        # interaction.message = o ephemeral do dropdown; o modal usa pra se
        # apagar sozinho no sucesso ou no go-back.
        await interaction.response.send_modal(
            SplitNodesValueModal(self.lang, self.event_id, batch,
                                 dropdown_msg=interaction.message,
                                 nodes=self.nodes, preselected_ids=sel))

    async def _on_skip(self, interaction: Interaction) -> None:
        # Nenhum marcado = todos ficam ❌ no embed (split já definido).
        await self._finish(interaction)

    async def _finish(self, interaction: Interaction) -> None:
        await _dismiss_ephemeral(interaction)
        asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


class TabValueModal(discord.ui.Modal):
    def __init__(self, lang: str, event_id: int, nodes: list[dict] | None = None):
        super().__init__(title=t(lang, "ev_enter_tab_value"), timeout=120)
        self.lang = lang
        self.event_id = event_id
        self.nodes = nodes or []
        self.val = discord.ui.TextInput(
            label=t(lang, "ev_enter_tab_value"), placeholder="500000000",
            min_length=1, max_length=12, required=True,
        )
        self.add_item(self.val)

    async def on_submit(self, interaction: Interaction) -> None:
        try:
            value = int(str(self.val.value).strip().replace(".", "").replace(",", ""))
        except ValueError:
            await interaction.response.send_message(
                t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        # acka antes do POST pra não estourar o timeout de 3s do Discord.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        res = await _post(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/verification/tab_value",
            {"completed": True, "data": {"value": value}, "actor_id": interaction.user.id},
        )
        if res is None:
            await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
            return
        # Tab definida. Se há nodes próximos do callout AINDA sem captura
        # decidida (site pode já ter resolvido alguns/todos via NodeClaimSection),
        # pergunta quais foram capturados + o valor vendido de cada um (junto
        # com o split, como no bot-v1) — senão os não-capturados viriam ❌ sem
        # o gestor marcar nada. Se o site já capturou TODOS os nodes próximos,
        # não há nada a perguntar — pula a pergunta em vez de reabri-la à toa.
        if any(not nd.get("captured") for nd in self.nodes):
            await interaction.followup.send(
                t(self.lang, "ev_split_nodes_intro"),
                view=SplitNodesView(self.lang, self.event_id, self.nodes), ephemeral=True)
        else:
            asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


# ── Selects de participante ───────────────────────────────────────────────────

# ponytail: teto de 5 selects (125 itens) por View — Discord não permite mais
# que 5 linhas de componente numa View. Acima disso, paginação (Prev/Next)
# seria o próximo passo se algum evento/guilda realmente precisar.
_MAX_SELECTS = 5


def _build_selects(items: list, factory) -> list[discord.ui.Select]:
    """Quebra items em grupos de até 25 (limite de opções do Discord por
    select) e cria 1 select por grupo via factory(chunk, page, total_pages),
    até _MAX_SELECTS grupos."""
    chunks = [items[i:i + 25] for i in range(0, len(items), 25)][:_MAX_SELECTS] or [[]]
    total = len(chunks)
    return [factory(c, i, total) for i, c in enumerate(chunks)]


def _participant_options(participants: list[dict]) -> list[discord.SelectOption]:
    """UserSelect deixa escolher QUALQUER membro do server — errado aqui, já
    que só participantes já escalados fazem sentido pra editar %/remover.
    Select comum com opções explícitas restringe a lista de verdade."""
    return [
        discord.SelectOption(
            label=(p.get("user_name") or f"#{p['user_id']}")[:100],
            value=str(p["user_id"]),
            description=f"{p.get('percent', 0)}%",
        )
        for p in participants
    ]


def _placeholder_paged(base: str, page: int, total_pages: int) -> str:
    return base if total_pages <= 1 else f"{base} ({page + 1}/{total_pages})"


class PercentPickSelect(discord.ui.Select):
    def __init__(self, lang: str, event_id: int, participants: list[dict], *,
                page: int = 0, total_pages: int = 1):
        opts = _participant_options(participants)
        super().__init__(
            placeholder=_placeholder_paged(t(lang, "ev_pick_participant"), page, total_pages),
            min_values=1, max_values=1,
            options=opts or [discord.SelectOption(label="—", value="-1")],
        )
        self.lang = lang
        self.event_id = event_id
        self.by_user = {str(p["user_id"]): p for p in participants}

    async def callback(self, interaction: Interaction) -> None:
        p = self.by_user.get(self.values[0])
        if p is None:
            await interaction.response.edit_message(content=t(self.lang, "ev_update_fail"), view=None)
            return
        await interaction.response.send_modal(PercentModal(self.lang, self.event_id, p["id"]))


def _build_percent_selects(lang: str, event_id: int, participants: list[dict]) -> list[discord.ui.Select]:
    return _build_selects(participants, lambda c, i, n: PercentPickSelect(lang, event_id, c, page=i, total_pages=n))


class RemovePickSelect(discord.ui.Select):
    def __init__(self, lang: str, event_id: int, participants: list[dict], *,
                page: int = 0, total_pages: int = 1):
        opts = _participant_options(participants)
        super().__init__(
            placeholder=_placeholder_paged(t(lang, "ev_pick_participant_remove"), page, total_pages),
            min_values=1, max_values=1,
            options=opts or [discord.SelectOption(label="—", value="-1")],
        )
        self.lang = lang
        self.event_id = event_id
        self.by_user = {str(p["user_id"]): p for p in participants}

    async def callback(self, interaction: Interaction) -> None:
        p = self.by_user.get(self.values[0])
        if p is None:
            await interaction.response.edit_message(content=t(self.lang, "ev_update_fail"), view=None)
            return
        # acka antes do DELETE pra não estourar o timeout de 3s do Discord.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        res = await _delete(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/participants/{p['id']}"
            f"?actor_id={interaction.user.id}"
        )
        if res is None:
            await interaction.edit_original_response(content=t(self.lang, "ev_update_fail"), view=None)
            return
        await _dismiss_ephemeral(interaction)
        asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


def _build_remove_selects(lang: str, event_id: int, participants: list[dict]) -> list[discord.ui.Select]:
    return _build_selects(participants, lambda c, i, n: RemovePickSelect(lang, event_id, c, page=i, total_pages=n))


# ── Adicionar participante (substitui o late-attend do bot-v1) ─────────────────
# Sem late-attend nesta versão: o gestor adiciona manualmente pelo embed.
# Select comum (não UserSelect: precisamos EXCLUIR quem já é participante,
# e UserSelect sempre mostra o server inteiro sem filtro) → POST /participants
# direto, sempre a 100% (sem modal perguntando % — quem quiser outro valor
# usa "✏️ Change %" logo em seguida).

class AddParticipantUserSelect(discord.ui.Select):
    def __init__(self, lang: str, event_id: int, members: list[discord.Member], *,
                page: int = 0, total_pages: int = 1):
        self.by_id = {str(m.id): m for m in members}
        opts = [
            discord.SelectOption(label=(m.display_name or m.name)[:100], value=str(m.id))
            for m in members
        ]
        super().__init__(
            placeholder=_placeholder_paged(t(lang, "ev_pick_member"), page, total_pages),
            min_values=1, max_values=1,
            options=opts or [discord.SelectOption(label="—", value="-1")],
        )
        self.lang = lang
        self.event_id = event_id

    async def callback(self, interaction: Interaction) -> None:
        member = self.by_id.get(self.values[0])
        if member is None:
            await interaction.response.edit_message(content=t(self.lang, "ev_update_fail"), view=None)
            return
        # acka antes do POST pra não estourar o timeout de 3s do Discord.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        res = await _post(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/participants",
            {"user_id": member.id, "user_name": member.display_name,
             "percent": 100, "base_percent": 100,
             "is_trial": False, "actor_id": interaction.user.id},
        )
        if res is None:
            # 400 = já existe; 404 = evento. _post devolve None p/ qualquer não-200.
            await interaction.edit_original_response(content=t(self.lang, "ev_update_fail"), view=None)
            return
        await _dismiss_ephemeral(interaction)
        asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))


def _build_add_selects(lang: str, event_id: int, members: list[discord.Member]) -> list[discord.ui.Select]:
    return _build_selects(members, lambda c, i, n: AddParticipantUserSelect(lang, event_id, c, page=i, total_pages=n))


# ── View do embed ─────────────────────────────────────────────────────────────

class EventEmbedView(discord.ui.View):
    """Recriada a cada refresh com botões apropriados ao estado. Não persistente
    (timeout=None mas sem custom_id registrado) — o loop de 10s repõe uma view
    nova, igual ao MassinfoView. Botões fazem gate de cargo local.

    Um restart do bot mata os botões da mensagem antiga até ela ser reeditada
    (a View velha não existe mais no processo novo). on_ready força esse
    reedit imediatamente (sync_event_embeds(..., force=True) em main.py) em
    vez de esperar uma mutação nova marcar dirty — sem isso, um evento em
    review/finalizado ficaria com botões mortos ("interaction failed")
    indefinidamente após um restart, já que aqui não existe staleness-timer
    de fallback como no mass-info."""

    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item) -> None:
        # Default do discord.ui.View só loga via logging (_log.error) — bot-v2
        # chama bot.start() em vez de bot.run(), então NENHUM handler de log
        # é configurado (bot.run() faz isso sozinho), e a exceção real fica
        # muda: o botão só "parece não responder" (Discord mostra "This
        # interaction failed" pro clique que nunca foi reconhecido), sem
        # nenhum rastro em lugar nenhum. Print no mesmo padrão do resto do
        # cog pra aparecer sempre, com ou sem logging configurado.
        import traceback
        print(f"[event_embeds] erro no botão '{item}' (evento {self.event_id}): "
              f"{type(error).__name__}: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message(t(self.lang, "ev_update_fail"), ephemeral=True)
            except (discord.Forbidden, discord.HTTPException):
                pass

    def __init__(self, lang: str, event_id: int, state: str,
                 allowed: list[str], ev: dict, nodes: list[dict] | None = None):
        super().__init__(timeout=None)
        self.lang = lang
        self.event_id = event_id
        self.state = state
        self.ev = ev
        self.nodes = nodes or []

        # Callout: IN_PROGRESS → REVIEW (freeze voice%, abre thread de embed).
        if state == "in_progress" and "review" in allowed:
            self._add_transition("ev_callout", "review", discord.ButtonStyle.primary)

        # Botões gerenciadores de revisão: add, %, remover, split, finalizar.
        # Sem emoji= (ícone próprio do botão) — o emoji já está no label
        # (i18n), ícone + texto duplicava o mesmo emoji no botão.
        if state == "review":
            parts = ev.get("participants") or []
            add_btn = discord.ui.Button(label=t(lang, "ev_add_participant"), style=discord.ButtonStyle.secondary)
            add_btn.callback = self._on_add
            self.add_item(add_btn)
            if parts:
                edit_btn = discord.ui.Button(label=t(lang, "ev_edit_pct"), style=discord.ButtonStyle.secondary)
                edit_btn.callback = self._on_edit_pct
                self.add_item(edit_btn)
                rm_btn = discord.ui.Button(label=t(lang, "ev_remove_participant"), style=discord.ButtonStyle.secondary)
                rm_btn.callback = self._on_remove
                self.add_item(rm_btn)
            split_btn = discord.ui.Button(label=t(lang, "ev_set_split"), style=discord.ButtonStyle.secondary)
            split_btn.callback = self._on_split
            self.add_item(split_btn)
            # Concluir: SEMPRE disponível em review (sem guard, assume 0 se faltar).
            if "finalized" in allowed:
                self._add_transition("ev_finalize", "finalized", discord.ButtonStyle.success)

    def _add_transition(self, label_key: str, to: str, style) -> None:
        btn = discord.ui.Button(label=t(self.lang, label_key), style=style)
        async def _cb(interaction: Interaction) -> None:
            if not _is_manager(interaction.user):
                await interaction.response.send_message(t(self.lang, "ev_only_manage"), ephemeral=True)
                return
            # acka antes do POST pra não estourar o timeout de 3s do Discord.
            try:
                await interaction.response.defer()
            except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
                pass
            res = await _post(
                f"/bot/events/{interaction.guild_id}/{self.event_id}/transition",
                {"to": to, "actor_id": interaction.user.id, "actor_name": interaction.user.display_name},
            )
            if res is None:
                await interaction.followup.send(t(self.lang, "ev_update_fail"), ephemeral=True)
                return
            if to == "finalized":
                await interaction.followup.send(t(self.lang, "ev_done"), ephemeral=True)
            # transições rotineiras (ex.: 📢 callout): sem mensagem, já deferiu acima.
            asyncio.create_task(_trigger_embed_refresh(interaction.client, interaction.guild, self.event_id))
        btn.callback = _cb
        self.add_item(btn)

    async def _on_edit_pct(self, interaction: Interaction) -> None:
        if not _is_manager(interaction.user):
            await interaction.response.send_message(t(self.lang, "ev_only_manage"), ephemeral=True)
            return
        parts = self.ev.get("participants") or []
        view = discord.ui.View(timeout=120)
        for sel in _build_percent_selects(self.lang, self.event_id, parts):
            view.add_item(sel)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _on_add(self, interaction: Interaction) -> None:
        if not _is_manager(interaction.user):
            await interaction.response.send_message(t(self.lang, "ev_only_manage"), ephemeral=True)
            return
        # Exclui quem já é participante — UserSelect não tem como filtrar
        # (mostra o server inteiro sempre), por isso o Add usa Select comum
        # com a lista de membros já sem quem está escalado.
        existing_ids = {p["user_id"] for p in (self.ev.get("participants") or [])}
        candidates = [m for m in (interaction.guild.members if interaction.guild else [])
                     if not m.bot and m.id not in existing_ids]
        if not candidates:
            await interaction.response.send_message(t(self.lang, "ev_no_candidates"), ephemeral=True)
            return
        candidates.sort(key=lambda m: (m.display_name or m.name).lower())
        view = discord.ui.View(timeout=120)
        for sel in _build_add_selects(self.lang, self.event_id, candidates):
            view.add_item(sel)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _on_remove(self, interaction: Interaction) -> None:
        if not _is_manager(interaction.user):
            await interaction.response.send_message(t(self.lang, "ev_only_manage"), ephemeral=True)
            return
        parts = self.ev.get("participants") or []
        view = discord.ui.View(timeout=120)
        for sel in _build_remove_selects(self.lang, self.event_id, parts):
            view.add_item(sel)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _on_split(self, interaction: Interaction) -> None:
        if not _is_manager(interaction.user):
            await interaction.response.send_message(t(self.lang, "ev_only_manage"), ephemeral=True)
            return
        await interaction.response.send_modal(
            TabValueModal(self.lang, self.event_id, self.nodes))


async def _trigger_embed_refresh(bot: commands.Bot, guild: discord.Guild | None,
                                 event_id: int) -> None:
    cog = bot.get_cog("EventEmbeds")
    if cog is None or guild is None:
        return
    await cog.refresh_one(guild, event_id)


# ── Cog ───────────────────────────────────────────────────────────────────────

_cog_ref: "EventEmbeds | None" = None


class EventEmbeds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[event_embeds] cog carregada — loop de embeds de evento ativo")
        if not embed_work_loop.is_running():
            embed_work_loop.start(self)

    async def cog_unload(self) -> None:
        embed_work_loop.cancel()

    async def refresh_one(self, guild: discord.Guild, event_id: int) -> None:
        dto = await _get(f"/bot/events/{guild.id}/{event_id}/embed")
        if dto is None:
            return
        await self._post_or_edit_embed(guild, event_id, dto)

    async def sync_event_embeds(self, guild: discord.Guild, *, force: bool = False) -> None:
        """Puxa /embed-work e reedita cada embed sujo. Cria a mensagem no callout
        (REVIEW) se ainda não existe.

        /embed-work já devolve o DTO completo de cada evento inline (mesmo fix
        que o mass-info já tinha) — nada de GET extra por evento aqui. No
        catch-up de restart com force=True isso era 1+N round-trips sequenciais
        (N = eventos ativos) só pra listar, sobrando pouco dos 5s de timeout
        por chamada e fazendo os últimos da lista falharem em cascata.

        force=True (só no catch-up de on_ready, main.py) reedita TODO embed
        ativo (sujo ou não) — um restart mata os botões da EventEmbedView
        antiga em memória, e sem isso o site não teria motivo pra marcar
        dirty de novo (nada mudou), deixando ✏️/🫷/💰/✅ mortos até a
        próxima mutação."""
        path = f"/bot/events/{guild.id}/embed-work"
        if force:
            path += "?force=true"
        work = await _get(path)
        if work is None:
            print(f"[event_embeds] {guild.id}: embed-work sem resposta "
                  f"(backend fora do ar ou 401) — BOT_SITE_URL={SITE_URL or '(vazio)'}")
            return
        events = work.get("events") or []
        if events:
            print(f"[event_embeds] {guild.id}: {len(events)} embed(s) pra (re)editar "
                  f"→ {[(e['event_id'], e.get('event_message_id') is not None) for e in events]}")
            for item in events:
                await self._post_or_edit_embed(guild, item["event_id"], item)

        # Arquivamento: terminais (finalizado/cancelado/excluído) com thread de
        # embed ainda não trancada — espelho do archive de regear_threads.py.
        for item in work.get("archive") or []:
            await self._archive_thread(guild, item)

    async def _archive_thread(self, guild: discord.Guild, ev: dict) -> None:
        event_id = ev.get("event_id")
        cid = ev.get("event_channel_id")
        if not event_id or not cid:
            return
        try:
            ch = guild.get_channel(int(cid))
            if ch is None:
                # get_channel só cobre cache — threads arquivadas pelo Discord
                # por inatividade saem do cache (mesmo motivo do fetch_channel
                # em regear_threads.py._archive_thread).
                ch = await guild.fetch_channel(int(cid))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
            ch = None
        if isinstance(ch, discord.Thread):
            try:
                await ch.edit(archived=True, locked=True)
            except (discord.Forbidden, discord.HTTPException):
                pass
        # Best-effort: confirma mesmo sem thread pra trancar (guilda sem sala
        # de revisão configurada, embed num canal comum) ou se ela já sumiu —
        # tira o evento da fila de arquivamento de qualquer jeito.
        await _post(f"/bot/events/{guild.id}/{event_id}/event-thread-archived", {})

    async def _post_or_edit_embed(self, guild: discord.Guild, event_id: int,
                                  dto: dict, ids: dict | None = None) -> None:
        cfg = await _guild_command_config(guild.id)
        lang = cfg["language"]
        ev = dto["event"]
        embed = _build_event_embed(lang, guild.id, event_id, dto)
        view = EventEmbedView(lang, event_id, ev["state"],
                              ev.get("allowed_transitions") or [], ev,
                              dto.get("nodes") or [])

        ids = ids or {}
        message_id = ids.get("event_message_id")
        if message_id is None:
            # embed_dto põe esses ids no topo do dto (NÃO dentro de detail) —
            # ler de ev=dto["event"] aqui retornava None e recriava a mensagem
            # a cada refresh_one (clique nos botões), duplicando o embed.
            message_id = dto.get("event_message_id")
        # event_channel_id diz onde a mensagem vive hoje (canal ou thread);
        # get_channel cobre threads em cache, fetch_channel pega as que não estão
        # (pós-restart o bot não tem a thread em memória).
        channel_id = ids.get("event_channel_id") or dto.get("event_channel_id")
        message = None
        if message_id and channel_id:
            ch = guild.get_channel(int(channel_id))
            if ch is None:
                try:
                    ch = await guild.fetch_channel(int(channel_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    ch = None
            if ch is not None:
                try:
                    message = await ch.fetch_message(int(message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    message = None

        if message is None:
            # Primeira postagem do embed. Sala de revisão configurada → abre uma
            # thread lá dentro e posta o embed como mensagem inicial. Sem sala,
            # cai no canal de eventos como mensagem simples (comportamento antigo).
            message = await self._create_embed_message(
                guild, event_id, lang, embed, view, cfg)
            if message is None:
                return
        else:
            try:
                await message.edit(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                return

        await _post(f"/bot/events/{guild.id}/{event_id}/embed-synced", {
            "event_channel_id": str(message.channel.id),
            "event_message_id": str(message.id),
            "clear_dirty": True,
        })

    async def _create_embed_message(
        self, guild: discord.Guild, event_id: int, lang: str,
        embed: discord.Embed, view: discord.ui.View, cfg: dict,
    ) -> Optional[discord.Message]:
        """Primeira postagem do embed: thread na sala de revisão (se setada) ou
        mensagem simples no canal de eventos (fallback). Devolve a mensagem
        criada pra o caller gravar os ids e editar depois."""
        target = _embed_target(cfg.get("event_review_channel_id"), cfg.get("events_channel_id"))
        print(f"[event_embeds] {guild.id} evento {event_id}: target={target} "
              f"review={cfg.get('event_review_channel_id')} events={cfg.get('events_channel_id')}")
        if target == "thread":
            room = guild.get_channel(int(cfg["event_review_channel_id"]))
            if room is None:
                # get_channel é só cache — fetch_channel cobre canais não em
                # memória (canal de revisão criado depois do start do bot, ou
                # cache evictido). Sem isto, miss de cache = skip silencioso e
                # a thread de evento nunca abre.
                try:
                    room = await guild.fetch_channel(int(cfg["event_review_channel_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    print(f"[event_embeds] sala de revisão {cfg.get('event_review_channel_id')} "
                          f"não encontrada em {guild.id}")
                    return None
            if isinstance(room, discord.TextChannel):
                print(f"[event_embeds] criando thread p/ evento {event_id} na sala {room.id}")
                try:
                    # Thread pública sem mensagem-inicial (start_thread_without_message);
                    # o embed vai DENTRO dela como 1ª mensagem, não no canal pai.
                    thread = await room.create_thread(
                        name=t(lang, "ev_thread_title", n=event_id),
                        type=discord.ChannelType.public_thread,
                    )
                    msg = await thread.send(embed=embed, view=view)
                    print(f"[event_embeds] ✓ thread {thread.id} + msg {msg.id} p/ evento {event_id}")
                    return msg
                except Exception as e:
                    print(f"[event_embeds] falhou criar thread p/ evento {event_id} "
                          f"em {room.id}: {type(e).__name__}: {e}")
                    return None
            print(f"[event_embeds] sala de revisão {cfg.get('event_review_channel_id')} "
                  f"não é canal de texto em {guild.id} (é {type(room).__name__})")
            # Sala configurada mas inválida/sem permissão: NÃO cai no fallback
            # do canal de eventos — senão vira duplicado quando o admin arrumar
            # a sala. Apenas pula; o próximo tick retry quando a sala existir.
            return None
        if target == "channel":
            channel = guild.get_channel(int(cfg["events_channel_id"]))
            if channel is None:
                try:
                    channel = await guild.fetch_channel(int(cfg["events_channel_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    return None
            if not isinstance(channel, discord.TextChannel):
                return None
            try:
                return await channel.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                return None
        return None  # skip


def _embed_target(review_room_id: str | None, events_channel_id: str | None) -> str:
    """Decide onde a 1ª postagem do embed vai: 'thread' (sala de revisão),
    'channel' (canal de eventos, fallback) ou 'skip'. Crucial: sala de revisão
    setada NUNCA cai no fallback — evita postar no canal de eventos e, quando o
    admin arrumar a sala, criar uma SEGUNDA mensagem (embed duplicado)."""
    if review_room_id:
        return "thread"
    if events_channel_id:
        return "channel"
    return "skip"


@tasks.loop(seconds=10)
async def embed_work_loop(cog: "EventEmbeds") -> None:
    for guild in cog.bot.guilds:
        try:
            await cog.sync_event_embeds(guild)
        except Exception as e:
            print(f"[event_embeds] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@embed_work_loop.before_loop
async def _before() -> None:
    # discord.py chama before_loop SEM os args de .start(cog) (só o corpo
    # principal do loop recebe) — declarar `cog` aqui derruba a task com
    # TypeError a CADA .start(), antes do primeiro tick (raiz do "só funciona
    # quando o bot inicia": tudo que "funcionava" vinha só das chamadas
    # diretas do on_ready em main.py, nunca deste loop). Usa o _cog_ref
    # global (setado em cog_load) em vez de receber como parâmetro.
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@embed_work_loop.error
async def _on_error(error: BaseException) -> None:
    # Confirmado empiricamente: se ISTO roda, o loop MORREU — tasks.loop só
    # chama .error() pra log e deixa a task terminar, nunca reagenda sozinho.
    # Sem handler nenhum (como era antes), essa morte é 100% silenciosa.
    # Loga alto E reinicia — autocura em vez de ficar morto pro resto do
    # processo (mesmo espírito do retry em battle_price_reprocessor.py).
    import traceback
    print(f"[event_embeds] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        # .error() roda ANTES do _loop() interno terminar a task de verdade —
        # chamar .start()/.restart() aqui de forma síncrona corre com esse
        # encerramento. call_soon empurra pro próximo tick do event loop,
        # depois que a task atual já terminou.
        asyncio.get_running_loop().call_soon(lambda: embed_work_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventEmbeds(bot))