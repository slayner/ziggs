"""Mass-info de eventos: embed único por guilda listando os CTAs ativos/
agendados, com um botão por CTA que abre a auto-inscrição de função —
substitui `bot/cogs/massinfo.py` (embed + Google Sheets) pela versão
site-nativa. O bot NUNCA calcula o gate de vagas/cargos sozinho, só chama
`/bot/events/*` e renderiza o que o site manda (fonte da verdade é
`app/services/event_gates.py` + `event_signups.py` no backend, ver
`main.py`'s `event_work_loop` pro polling que aciona `sync_massinfo`)."""
import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import Interaction
from discord.ext import commands

import http_client
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")
# Link CLICÁVEL no Discord (frontend) — separado de SITE_URL (backend, onde o
# bot chama a API). Em dev os dois rodam em portas diferentes (5173 vs 8000);
# sem BOT_PUBLIC_URL configurado, cai em SITE_URL (funciona quando front e back
# dividem a mesma origem, ex.: produção atrás de um proxy reverso).
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL

MAX_EVENT_BUTTONS = 25   # limite de componentes numa View do Discord
MAX_PARTY_FIELDS = 3     # não deixa o embed estourar com muito CTA simultâneo

# Funções/categorias por página no dropdown (25 = limite do Discord; reservamos
# ◀️ e ▶️ pra navegar quando a lista passa de 25).
FN_PER_PAGE  = 23
CAT_PER_PAGE = 23

# ponytail: review não aparece no mass-info (só scheduled/in_progress) — sem emoji p/ ele.
_STATUS_EMOJI = {"scheduled": "🗓️", "in_progress": "🟢"}
_CATEGORY_EMOJI = {"tank": "🛡️", "healer": "🕊️", "support": "✨", "dps": "⚔️", "pierce": "🏹", "other": "❔"}


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path)


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body)


async def _delete(path: str) -> Optional[dict]:
    return await http_client.delete_json(path)


def _member_role_ids(user) -> list[int]:
    return [r.id for r in user.roles] if isinstance(user, discord.Member) else []


async def _purge_bot_messages(channel: discord.TextChannel, *, keep_id: int | None = None) -> int:
    """Apaga TODAS as mensagens do próprio bot no canal (exceto `keep_id`), em
    vez de tentar achar "a última mensagem que enviei" por id persistido —
    entre restarts/bumps falhos esse id podia não bater com o que estava no
    canal e ficavam embeds órfãs acumuladas. Varre o histórico recente e apaga
    tudo que seja nosso (mensagem própria não exige permissão e não tem limite
    de 14d como o bulk-delete). Best-effort: ignora já-apagadas/sem permissão."""
    me = channel.guild.me.id if channel.guild.me else 0
    if not me:
        return 0
    deleted = 0
    try:
        async for m in channel.history(limit=200):
            if m.author.id != me:
                continue
            if keep_id is not None and m.id == keep_id:
                continue
            try:
                await m.delete()
                deleted += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # best-effort — já apagada / sem permissão
    except (discord.Forbidden, discord.HTTPException):
        pass  # sem read_history_history → não dá pra varrer; melhor sorte na próxima
    return deleted


def _fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "--:--"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return "--:--"


def _rel_ts(iso: Optional[str]) -> str:
    """Timestamp relativo nativo do Discord ("em 2 horas"/"há 20 min", no
    idioma e fuso de CADA membro). Complementa o HH:MM UTC — que fica, porque
    UTC é a língua franca de CTA no Albion — sem ninguém precisar converter
    de cabeça. String vazia se o ISO não parsear."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f" (<t:{int(dt.timestamp())}:R>)"


def _build_massinfo_embed(lang: str, guild_id: int, events: list[dict]) -> discord.Embed:
    embed = discord.Embed(color=discord.Color.blurple(), title=t(lang, "massinfo_title"))
    if not events:
        embed.description = t(lang, "massinfo_empty")
        return embed

    running = sum(1 for e in events if e["state"] == "in_progress")
    scheduled = sum(1 for e in events if e["state"] == "scheduled")
    lines = [t(lang, "massinfo_summary", running=running, scheduled=scheduled), ""]

    party_blocks: list[str] = []
    for e in events:
        emoji = _STATUS_EMOJI.get(e["state"], "🗓️")
        time_str = _fmt_time(e["scheduled_at"])
        # Horário vira link pro roster (escalação) do evento — deep link do site.
        if PUBLIC_URL:
            time_disp = f"[**{time_str}**]({PUBLIC_URL}/events/{guild_id}/{e['event_id']}/escalation)"
        else:
            time_disp = f"**{time_str}**"
        comp = e.get("comp_name") or "—"
        msg = f" · **{e['message'].upper()}**" if e.get("message") else ""
        lines.append(f"{emoji} {time_disp}{_rel_ts(e['scheduled_at'])} · `{e['signup_count']}` · {comp}{msg}")

        if e.get("lupa_active") and e.get("parties"):
            header_key = "massinfo_massing_now" if e["state"] == "in_progress" else "massinfo_massing_soon"
            block = [f"**{emoji} {t(lang, header_key)} · {comp}**"]
            for p in e["parties"]:
                block.append(f"PARTY {p['party']} ~ ({p['filled']}/{p['total']})")
            party_blocks.append("\n".join(block))

    embed.description = "\n".join(lines)
    for block in party_blocks[:MAX_PARTY_FIELDS]:
        embed.add_field(name="​", value=block, inline=False)
    return embed


class EventSignupButton(discord.ui.Button):
    def __init__(self, event: dict):
        # custom_id estável (event_id) — espelha o bot-v1 (massinfo.py): a View
        # em memória não sobrevive a restart, então o botão precisa de identidade
        # estável pra o reedit pós-restart religar o callback (ver _rebind_pending
        # em main.py's event_work_loop). Sem isso, edit gera custom_ids novos a
        # cada processo e o clique na mensagem velha volta "interaction failed".
        super().__init__(
            label=_fmt_time(event["scheduled_at"]),
            emoji=_STATUS_EMOJI.get(event["state"], "🗓️"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"ziggs:signup:{event['event_id']}",
        )
        self.event_id = event["event_id"]

    async def callback(self, interaction: Interaction) -> None:
        await _open_signup_flow(interaction, self.event_id)


class MassinfoView(discord.ui.View):
    """View persistente (timeout=None + botões com custom_id estável). Não
    expira sozinha; é recriada a cada sync do polling loop (ver
    event_work_loop em main.py). Um restart mata os botões da mensagem antiga
    até a mensagem ser reeditada (a View velha não existe mais no processo
    novo → cliques voltam "interaction failed"). on_ready tenta o reedit
    imediatamente (refresh_massinfo(force=True)); se o backend ainda não
    respondeu (start-all.cmd pode ligar o bot antes do site), a guilda entra
    em _rebind_pending e o event_work_loop reedita no primeiro poll bom —
    senão os botões ficariam mortos até a próxima mutação (o gate de
    staleness do site não reedita nada "fresco")."""

    def __init__(self, events: list[dict]):
        super().__init__(timeout=None)
        for event in events[:MAX_EVENT_BUTTONS]:
            self.add_item(EventSignupButton(event))


import ephemeral_guard

async def _replace_ephemeral(interaction: Interaction, content: str, view: Optional[discord.ui.View]) -> None:
    """Reenvia o ephemeral: apaga o velho e manda um novo via followup. Nunca
    edita uma mensagem antiga — a cada passo do wizard nasce um ephemeral
    fresco (timeout renovado), contornando o problema clássico do usuário
    deixar a mensagem aberta e a interação expirar: não dependemos de
    `edit_message` numa interação stale, sempre criamos uma nova.

    Message.delete() normal não funciona em mensagem ephemeral (não é uma
    mensagem "de canal" de verdade) — o único jeito é delete_original_response()
    na MESMA interação que a serviu. defer() sem thinking=True, numa interação
    de componente (botão/select), manda um DEFERRED_UPDATE_MESSAGE em vez de
    criar mensagem nova — isso deixa a mensagem antiga (a do componente
    clicado) endereçável como "@original" pra delete_original_response() apagar.

    defer() e delete_original_response() ficam em tries SEPARADOS: _on_done já
    deferiu mais cedo (antes do POST, pra não estourar os 15s do Discord) —
    nesse caso o defer() daqui levanta InteractionResponded, mas a mensagem
    original ainda existe e precisa ser apagada do mesmo jeito.

    Auto-delete: o followup.send(ephemeral=True) é interceptado pelo
    ephemeral_guard (monkey-patch) e agenda o auto-delete de 60s. O timer
    da mensagem original é cancelado aqui (ela vai ser apagada explicitamente
    abaixo, não precisa de timer)."""
    try:
        await interaction.response.defer()
    except (discord.InteractionResponded, discord.HTTPException):
        pass  # já deferida antes (ex.: _on_done) — segue OK, mensagem original ainda dá pra apagar
    # Cancela o timer da mensagem original — ela vai ser apagada explicitamente.
    if interaction.token:
        ephemeral_guard.cleanup(interaction.token)
    try:
        await interaction.delete_original_response()
    except (discord.NotFound, discord.HTTPException):
        pass  # best-effort — se já sumiu, tudo bem
    try:
        # discord.py 2.7+ rejeita view=None em webhook.send (followup); None aqui
        # significa "sem view nesta nova msg" → só omitir o kwarg.
        if view is not None:
            msg = await interaction.followup.send(content, view=view, ephemeral=True)
        else:
            msg = await interaction.followup.send(content, ephemeral=True)
        # followup.send patched já agendou o auto-delete, mas o patch usa o
        # token do webhook — garante que track() registrou com a interaction
        # pra que touch() (on_interaction) reset o timer corretamente.
        if msg is not None and hasattr(msg, "id"):
            ephemeral_guard.track(interaction, msg.id)
    except (discord.HTTPException, discord.NotFound):
        pass


async def _open_signup_flow(interaction: Interaction, event_id: int) -> None:
    lang = await guild_lang_for(interaction.guild_id)
    role_ids = ",".join(str(i) for i in _member_role_ids(interaction.user))
    data = await _get(
        f"/bot/events/{interaction.guild_id}/{event_id}/eligible-functions"
        f"?discord_user_id={interaction.user.id}&discord_role_ids={role_ids}"
    )
    if data is None:
        await interaction.response.send_message(t(lang, "signup_fetch_fail"), ephemeral=True)
        return

    current = data.get("current_signup")
    if current:
        view = AlreadyRegisteredView(event_id=event_id, lang=lang)
        await interaction.response.send_message(
            t(lang, "signup_already_registered", functions=", ".join(current["functions"]) or "—"),
            view=view, ephemeral=True,
        )
        return

    await _send_function_pick(interaction, event_id, lang, data, replace_prev=False)


async def _send_function_pick(interaction: Interaction, event_id: int, lang: str, data: dict, *, replace_prev: bool) -> None:
    functions = data.get("functions") or []
    reason = data.get("denial_reason")
    if not functions:
        key = "signup_no_slots" if reason == "no_slots" else "signup_no_role"
        content = t(lang, key)
        if replace_prev:
            await _replace_ephemeral(interaction, content, None)
        else:
            await interaction.response.send_message(content, ephemeral=True)
        return

    categories = data.get("categories") or {}
    flex_names = set(data.get("flex_names") or [])
    view = FunctionPickView(
        event_id=event_id, lang=lang, functions=functions, categories=categories,
        max_builds=data.get("signup_max_builds"), min_builds=data.get("signup_min_builds"),
        flex_names=flex_names,
    )
    content = view._initial_content()
    if replace_prev:
        await _replace_ephemeral(interaction, content, view)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)


class AlreadyRegisteredView(discord.ui.View):
    """Já tem inscrição nesse evento — mudar funções, remover, ou não fazer nada."""

    def __init__(self, *, event_id: int, lang: str):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.lang = lang

        change_btn = discord.ui.Button(label=t(lang, "signup_change_btn"), style=discord.ButtonStyle.primary)
        change_btn.callback = self._on_change
        remove_btn = discord.ui.Button(label=t(lang, "signup_remove_btn"), style=discord.ButtonStyle.danger)
        remove_btn.callback = self._on_remove
        cancel_btn = discord.ui.Button(label=t(lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._on_cancel
        self.add_item(change_btn)
        self.add_item(remove_btn)
        self.add_item(cancel_btn)

    async def _on_change(self, interaction: Interaction) -> None:
        role_ids = ",".join(str(i) for i in _member_role_ids(interaction.user))
        data = await _get(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/eligible-functions"
            f"?discord_user_id={interaction.user.id}&discord_role_ids={role_ids}"
        )
        if data is None:
            await _replace_ephemeral(interaction, t(self.lang, "signup_fetch_fail"), None)
            return
        await _send_function_pick(interaction, self.event_id, self.lang, data, replace_prev=True)

    async def _on_remove(self, interaction: Interaction) -> None:
        await _delete(f"/bot/events/{interaction.guild_id}/{self.event_id}/signups/{interaction.user.id}")
        await _replace_ephemeral(interaction, t(self.lang, "signup_removed"), None)
        # Refresh imediato — remoção de signup já foi persistida no site.
        asyncio.create_task(_trigger_massinfo_refresh(interaction.client, interaction.guild))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await _replace_ephemeral(interaction, t(self.lang, "prefix_cancelled"), None)


class FunctionPickView(discord.ui.View):
    """Wizard de inscrição em etapas, espelhando o bot-v1 (massinfo): categoria
    → função (uma por vez) → revisão ('adicionar mais' ou 'confirmar').

    Diferenças do bot-v1: cada passo REENVIA o ephemeral (delete + novo) em vez
    de editar — contorna o problema do usuário deixar a mensagem aberta até a
    interação expirar. E o dropdown pagina com ◀️/▶️ quando a categoria (ou a
    lista de categorias) passa de 25 opções (limite do Discord).

    Cap = `max_builds` (default 3); min = `min_builds` (>1 exige esse tanto de
    builds NÃO-flex; flex é opcional por cima)."""

    def __init__(self, *, event_id: int, lang: str, functions: list[str],
                 categories: dict[str, str], max_builds: int | None = None,
                 min_builds: int | None = None, flex_names: Optional[set[str]] = None):
        super().__init__(timeout=600)
        self.event_id = event_id
        self.lang = lang
        self.max_builds = max_builds
        self.min_builds = min_builds
        self.flex_names = flex_names or set()
        self.chosen: list[str] = []
        self._active_category: str = ""
        self._cat_page = 0
        self._fn_page = 0

        self.by_category: dict[str, list[str]] = {}
        for fn in functions:
            self.by_category.setdefault(categories.get(fn, "other"), []).append(fn)

        if len(self.by_category) <= 1:
            self._active_category = next(iter(self.by_category), "other")
            self._build_function_step()
        else:
            self._build_category_step()

    def _cap(self) -> int:
        return self.max_builds or 3

    def _available(self, cat: str) -> list[str]:
        """Funções da categoria que ainda não foram escolhidas (dedup)."""
        chosen = {c.lower() for c in self.chosen}
        return [f for f in self.by_category.get(cat, []) if f.lower() not in chosen]

    def _initial_content(self) -> str:
        if len(self.by_category) > 1:
            return t(self.lang, "signup_pick_category_prompt")
        return t(self.lang, "signup_pick_function_prompt")

    # --- renderização: cada etapa monta a view; o callback reenvia o ephemeral ---

    def _build_category_step(self) -> None:
        self.clear_items()
        cats = [c for c in self.by_category if self._available(c)]
        if not cats:
            return
        pages = max(1, -(-len(cats) // CAT_PER_PAGE))
        self._cat_page %= pages
        start = self._cat_page * CAT_PER_PAGE
        chunk = cats[start:start + CAT_PER_PAGE]
        options: list[discord.SelectOption] = []
        if pages > 1 and self._cat_page > 0:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_prev"), value="__prev__"))
        for cat in chunk:
            options.append(discord.SelectOption(label=f"{_CATEGORY_EMOJI.get(cat, '❔')} {cat}", value=cat))
        if pages > 1 and self._cat_page < pages - 1:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_next"), value="__next__"))
        select = discord.ui.Select(
            placeholder=t(self.lang, "signup_pick_category_ph"),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_category
        self.add_item(select)
        cancel = discord.ui.Button(label=t(self.lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _build_function_step(self) -> None:
        self.clear_items()
        fns = self._available(self._active_category)
        pages = max(1, -(-len(fns) // FN_PER_PAGE))
        self._fn_page %= pages
        start = self._fn_page * FN_PER_PAGE
        chunk = fns[start:start + FN_PER_PAGE]
        options: list[discord.SelectOption] = []
        if pages > 1 and self._fn_page > 0:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_prev"), value="__prev__"))
        for fn in chunk:
            options.append(discord.SelectOption(label=fn[:100], value=fn))
        if pages > 1 and self._fn_page < pages - 1:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_next"), value="__next__"))
        ph = t(self.lang, "signup_pick_function_ph", cat=self._active_category)
        if pages > 1:
            ph = f"{ph}  ({self._fn_page + 1}/{pages})"
        select = discord.ui.Select(placeholder=ph[:150], min_values=1, max_values=1, options=options)
        select.callback = self._on_function
        self.add_item(select)
        if len(self.by_category) > 1:
            back = discord.ui.Button(label=t(self.lang, "signup_back_to_categories"), style=discord.ButtonStyle.secondary)
            back.callback = self._on_back_to_categories
            self.add_item(back)
        cancel = discord.ui.Button(label=t(self.lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _build_review_step(self) -> None:
        self.clear_items()
        if len(self.chosen) < self._cap():
            add_btn = discord.ui.Button(label=t(self.lang, "signup_add_more"), style=discord.ButtonStyle.primary)
            add_btn.callback = self._on_add_more
            self.add_item(add_btn)
        done_btn = discord.ui.Button(label=t(self.lang, "signup_done_btn"), style=discord.ButtonStyle.success)
        done_btn.callback = self._on_done
        self.add_item(done_btn)
        cancel = discord.ui.Button(label=t(self.lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _review_content(self) -> str:
        body = "\n".join(f"• {c}" for c in self.chosen) or f"*{t(self.lang, 'signup_none_yet')}*"
        header = t(self.lang, "signup_chosen_header", n=len(self.chosen), cap=self._cap())
        prompt = t(self.lang, "signup_review_prompt")
        return f"{header}\n{body}\n\n{prompt}"

    # --- callbacks ---

    async def _on_category(self, interaction: Interaction) -> None:
        val = self.children[0].values[0]
        if val == "__prev__":
            self._cat_page = max(0, self._cat_page - 1)
            self._build_category_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_category_prompt"), self)
            return
        if val == "__next__":
            self._cat_page += 1
            self._build_category_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_category_prompt"), self)
            return
        self._active_category = val
        self._fn_page = 0
        self._build_function_step()
        await _replace_ephemeral(interaction, t(self.lang, "signup_pick_function_prompt"), self)

    async def _on_function(self, interaction: Interaction) -> None:
        val = self.children[0].values[0]
        if val == "__prev__":
            self._fn_page = max(0, self._fn_page - 1)
            self._build_function_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_function_prompt"), self)
            return
        if val == "__next__":
            self._fn_page += 1
            self._build_function_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_function_prompt"), self)
            return
        # escolheu UMA função → vai pra revisão (adicionar mais ou confirmar).
        self.chosen.append(val)
        self._build_review_step()
        await _replace_ephemeral(interaction, self._review_content(), self)

    async def _on_back_to_categories(self, interaction: Interaction) -> None:
        self._cat_page = 0
        self._build_category_step()
        await _replace_ephemeral(interaction, t(self.lang, "signup_pick_category_prompt"), self)

    async def _on_add_more(self, interaction: Interaction) -> None:
        if len(self.by_category) > 1:
            self._build_category_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_category_prompt"), self)
        else:
            self._fn_page = 0
            self._build_function_step()
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_function_prompt"), self)

    async def _on_done(self, interaction: Interaction) -> None:
        if not self.chosen:
            await _replace_ephemeral(interaction, t(self.lang, "signup_pick_at_least_one"), None)
            return
        non_flex = [c for c in self.chosen if c not in self.flex_names]
        if self.min_builds is not None and self.min_builds > 1 and len(non_flex) < self.min_builds:
            await _replace_ephemeral(interaction, t(self.lang, "signup_min_builds_needed", n=self.min_builds), None)
            return
        # acka antes do POST pra não estourar o timeout de 15s do Discord.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        result = await _post(
            f"/bot/events/{interaction.guild_id}/{self.event_id}/signups",
            {
                "user_id": interaction.user.id,
                "user_name": interaction.user.display_name or str(interaction.user),
                "functions": self.chosen,
                "discord_role_ids": _member_role_ids(interaction.user),
            },
        )
        if result is None or not result.get("ok"):
            await _replace_ephemeral(interaction, t(self.lang, "signup_fail"), None)
            return
        applied = result.get("functions") or self.chosen
        await _replace_ephemeral(interaction, t(self.lang, "signup_success", functions=", ".join(applied)), None)
        # Refresh imediato do mass-info — o signup já foi persistido no site,
        # não precisa esperar o próximo ciclo do polling (5s) pro embed
        # refletir o novo contador.
        asyncio.create_task(_trigger_massinfo_refresh(interaction.client, interaction.guild))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await _replace_ephemeral(interaction, t(self.lang, "prefix_cancelled"), None)


async def _trigger_massinfo_refresh(bot: commands.Bot, guild: discord.Guild) -> None:
    """Dispara sync_massinfo imediatamente após uma mutação do bot (signup/
    remoção). Roda em background (asyncio.create_task) pra não bloquear a
    resposta da interação. Best-effort: se falhar, o polling de 5s do
    event_work_loop cobre no próximo tick.

    Throttle: coalesce múltiplos disparos numa janela de 1s — o embed só
    muda o contador de inscritos, e 30 pessoas clicando no botão ao mesmo
    tempo não precisam de 30 edits do mesmo embed (backend + Discord). O
    refresh pegou o estado final; o poll de 5s cobre qualquer drift residual."""
    gid = guild.id
    existing = _refresh_tasks.get(gid)
    if existing is not None and not existing.done():
        return  # já tem refresh pendente — coalesce
    cog = bot.get_cog("Events")
    if cog is None:
        return

    async def _delayed() -> None:
        try:
            await asyncio.sleep(_REFRESH_COALESCE)
            await cog.refresh_massinfo(guild)
        except Exception:
            pass
        finally:
            _refresh_tasks.pop(gid, None)

    _refresh_tasks[gid] = asyncio.create_task(_delayed())


# Throttle de refresh do mass-info (ver _trigger_massinfo_refresh).
_refresh_tasks: dict[int, asyncio.Task] = {}
_REFRESH_COALESCE = 1.0  # segundos


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guildas que precisam de um reedit do mass-info pós-restart pra
        # religar os botões (a View do processo anterior sumiu). on_ready tenta
        # o reedit imediato; se o site ainda não respondeu, marca aqui e o
        # event_work_loop reedita no primeiro poll bom (force=true ignora o gate
        # de staleness e não consome o outbox de pings — não pingua no rebind).
        self._rebind_pending: set[int] = set()

    async def sync_massinfo(
        self, guild: discord.Guild, events: list[dict],
        ping_triggers: list[dict] | None = None,
        *, purge_orphans: bool = False,
    ) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("events_channel_id")
        if not channel_id:
            return  # sem canal configurado (Config -> Canal de Eventos) — nada a fazer

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return

        lang = cfg["language"]
        embed = _build_massinfo_embed(lang, guild.id, events)
        view = MassinfoView(events)

        # Pings de @everyone (plataforma configurável no site, ver
        # app/services/event_signups.py). O site decide SE cada gatilho pinga
        # (ping=True/False por entrada); o bot só executa:
        #  - bump = há gatilho E há eventos p/ mostrar (delete+resend move o
        #    embed pro fim do canal, "bumpando"; sem eventos não faria sentido
        #    bumpar uma lista vazia).
        #  - @everyone só se algum gatilho veio com ping=True.
        # t10min desligado nem chega aqui (o site não enqueue). Status triggers
        # (created/in_progress/review) sempre bumpam, com @everyone só se ligados.
        triggers = ping_triggers or []
        should_ping = any(t.get("ping") for t in triggers)
        do_bump = bool(triggers) and bool(events)

        # Cache local é o "fresco" (evita reenviar por causa do TTL de 60s do
        # config cacheado logo após o primeiro post); se vazio (bot acabou de
        # subir), cai pro que o site já tinha persistido da última sync.
        message_id = _massinfo_message_ids.get(guild.id)
        if message_id is None:
            raw = cfg.get("massinfo_message_id")
            message_id = int(raw) if raw else None

        # Rebind/startup (force=true, sem bump): limpa embeds ÓRFÃS deixadas pelo
        # run anterior (desligamento/crash) mantendo só a persistida pra editar
        # in-place. Não roda no bump (lá apaga TUDO e reenvia).
        if purge_orphans and not do_bump:
            await _purge_bot_messages(channel, keep_id=message_id)

        message = None
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        sent = False
        if do_bump:
            # Bump: apaga TODAS as embeds nossas no canal (não só a persistida)
            # e posta uma nova. Editar não pingua quem já recebeu a menção —
            # só reenviar dispara o @everyone. Varrer tudo garante que órfãs de
            # restarts/bumps falhos (id persistido ≠ mensagem no canal) sumam.
            content = "@everyone" if should_ping else None
            mentions = discord.AllowedMentions(everyone=should_ping, roles=False, users=False)
            await _purge_bot_messages(channel)
            try:
                message = await channel.send(content=content, embed=embed, view=view,
                                             allowed_mentions=mentions)
                sent = True
            except (discord.Forbidden, discord.HTTPException):
                # Falhou o reenvio — tenta cair no edit in-place abaixo como fallback.
                message = None

        if not sent:
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

        _massinfo_message_ids[guild.id] = message.id
        await _post(f"/bot/events/{guild.id}/massinfo-synced", {"message_id": str(message.id)})
        # Limpa o outbox de pings SÓ quando o bot consumiu gatilhos de verdade
        # (on_ready com force=True vem sem ping_triggers e não deve limpar —
        # os pings pendentes ficam pro próximo poll normal disparar).
        if triggers:
            await _post(f"/bot/events/{guild.id}/ping-triggers-acked", {})

    async def refresh_massinfo(self, guild: discord.Guild, *, force: bool = False) -> bool:
        """Sincronização imediata do mass-info após uma mutação que o bot mesmo
        causou (signup/remoção) — não espera o próximo ciclo do polling. Busca
        o estado fresco no site e repassa pra sync_massinfo. Best-effort: se
        falhar, o polling de 5s cobre no próximo tick.

        force=True ignora o gate de dirty/staleness do site — usado no
        catch-up de on_ready (main.py): um restart invalida os botões anexados
        em memória (a View do processo anterior sumiu), então o embed precisa
        ser reeditado mesmo se nada mudou no site, senão os botões ficam mortos
        até o próximo evento marcar dirty.

        Devolve True se o site respondeu (rebind feito ou dispensado), False se
        o site estava fora do ar — nesse caso o caller (on_ready) deixa a
        guilda em _rebind_pending pra o event_work_loop reeditar no primeiro
        poll bom."""
        path = f"/bot/events/{guild.id}/pending-work"
        if force:
            path += "?force=true"
        data = await _get(path)
        if data is None:
            return False  # site fora do ar — rebind fica pendente pro loop
        # Site respondeu: o sync abaixo reedita (rebind), então limpa o pendente.
        self._rebind_pending.discard(guild.id)
        if data.get("events") or data.get("needs_rebuild"):
            await self.sync_massinfo(
                guild, data.get("events") or [],
                ping_triggers=data.get("ping_triggers") or [],
                purge_orphans=force,
            )
        else:
            # Não há eventos ativos — garante que o dirty flag seja limpo
            # mesmo se o último evento acabou de ser finalizado.
            await _post(f"/bot/events/{guild.id}/massinfo-synced", {"message_id": "0"})
        return True


# {guild_id: message_id} — fast-path que não depende do cache de 60s do
# config; o site também persiste em Guild.settings.massinfo_message_id (lido
# via cfg["massinfo_message_id"]) como fallback pra depois de um restart.
_massinfo_message_ids: dict[int, int] = {}


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
