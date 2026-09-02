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
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")
# Link CLICÁVEL no Discord (frontend) — separado de SITE_URL (backend, onde o
# bot chama a API). Em dev os dois rodam em portas diferentes (5173 vs 8000);
# sem BOT_PUBLIC_URL configurado, cai em SITE_URL (funciona quando front e back
# dividem a mesma origem, ex.: produção atrás de um proxy reverso).
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL

MAX_EVENT_BUTTONS = 20   # 4 rows; the remaining row belongs to the select
MAX_PARTY_FIELDS = 3     # não deixa o embed estourar com muito CTA simultâneo

# Funções/categorias por página no dropdown (25 = limite do Discord; reservamos
# ◀️ e ▶️ pra navegar quando a lista passa de 25).
FN_PER_PAGE  = 23
CAT_PER_PAGE = 23

# ponytail: review não aparece no mass-info (só scheduled/in_progress) — sem emoji p/ ele.
_STATUS_EMOJI = {"scheduled": "🗓️", "in_progress": "🟢"}
# Emojis conhecidos para categorias que o CompBuilder já usa. Categorias novas
# (custom fn types que o usuário inventar) ganham ❔ — o nome aparece do lado.
_CATEGORY_EMOJI = {"tank": "🛡️", "healer": "🕊️", "support": "✨", "dps": "⚔️", "pierce": "🏹", "battlemount": "🐴", "other": "❔"}
# Ordem preferida das categorias conhecidas; categorias desconhecidas vão pro
# final em ordem alfabética (other por último de qualquer jeito).
_CATEGORY_ORDER = {"tank": 0, "healer": 1, "support": 2, "dps": 3, "pierce": 4, "battlemount": 5}


def _roster_url(token: str | None) -> str | None:
    return f"{PUBLIC_URL}/e/{token}" if PUBLIC_URL and token else None


def _category_sort_key(cat: str) -> tuple:
    """Categorias conhecidas primeiro (na ordem de _CATEGORY_ORDER), depois
    alfabético, 'other' sempre por último."""
    if cat == "other":
        return (2, "")
    if cat in _CATEGORY_ORDER:
        return (0, str(_CATEGORY_ORDER[cat]))
    return (1, cat)


def _fn_key_norm(fn: str | None) -> str:
    """Mesma normalização do backend (event_gates.fn_key): casefold/strip,
    vazio vira 'other'."""
    return " ".join((fn or "").casefold().split()) or "other"


def _fn_of(option: dict) -> str:
    """Categoria de uma opção = o fn do par."""
    return _fn_key_norm(option.get("fn"))


def _pair_display(option: dict | None, key: str) -> str:
    """Nome da arma de uma opção — a categoria vem pelo emoji."""
    if not option:
        return key
    return option.get("weapon_name") or key.partition(":")[0]


async def _get(path: str, *, interactive: bool = False) -> Optional[dict]:
    return await http_client.get_json(
        path, raise_on_unavailable=interactive,
    )


async def _post(
    path: str, body: dict, *, timeout: float = 5, tag: str = "",
    attempts: int = 1, queue_on_failure: bool = True,
) -> Optional[dict]:
    return await http_client.post_json(
        path, body, timeout=timeout, tag=tag, attempts=attempts,
        queue_on_failure=queue_on_failure,
    )


async def _delete(
    path: str, *, attempts: int = 1, queue_on_failure: bool = True,
) -> Optional[dict]:
    return await http_client.delete_json(
        path, tag="signup", attempts=attempts, queue_on_failure=queue_on_failure,
    )


def _member_role_ids(user) -> list[int]:
    return [r.id for r in user.roles] if isinstance(user, discord.Member) else []


def _signup_matches(data: dict | None, options: list[str]) -> bool:
    """Compara pela IDENTIDADE do signup: pair keys (weapon, fn)."""
    return bool(
        data
        and data.get("exists", data.get("ok", False))
        and set(data.get("options") or []) == set(options)
    )


async def _save_signup(
    guild_id: int, event_id: int, user, options: list[str],
    discord_role_ids: list[int],
) -> Optional[dict]:
    """Grava e confirma a inscrição. POST é um upsert, então repetir após uma
    conexão resetada é seguro; se só a resposta se perdeu, o GET confirma o
    estado já persistido."""
    path = f"/bot/events/{guild_id}/{event_id}/signups"
    result = await _post(
        path,
        {
            "user_id": user.id,
            "user_name": user.display_name or str(user),
            "options": options,
            "discord_role_ids": discord_role_ids,
        },
        timeout=20, tag="signup", attempts=2, queue_on_failure=False,
    )
    if result is not None and result.get("ok") and _signup_matches(result, options):
        return result
    saved = await _get(f"{path}/{user.id}", interactive=True)
    return saved if _signup_matches(saved, options) else None


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
                await dtimeout(m.delete())
                deleted += 1
            except SKIP_EXC:
                pass  # best-effort — já apagada / sem permissão / timeout
    except SKIP_EXC:
        pass  # sem read_history → não dá pra varrer; melhor sorte na próxima
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
        if url := _roster_url(e.get("escalation_token")):
            time_disp = f"[**{time_str}**]({url})"
        else:
            time_disp = f"**{time_str}**"
        comp = e.get("comp_name") or "—"
        title = (e.get("title") or f"Evento #{e['event_id']}")[:80]
        msg = f" · **{e['message'].upper()}**" if e.get("message") else ""
        signups = "—" if e.get("signup_mode") == "announcement" else str(e["signup_count"])
        lines.append(f"{emoji} {time_disp}{_rel_ts(e['scheduled_at'])} · **{title}** · `{signups}` · {comp}{msg}")

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


class FunctionPromptView(discord.ui.View):
    """DM enviado quando a administração libera a escolha de roles."""

    def __init__(self, guild_id: int, event_id: int, lang: str):
        super().__init__(timeout=86400)
        button = discord.ui.Button(
            label=t(lang, "signup_choose_roles"),
            style=discord.ButtonStyle.primary,
            custom_id=f"ziggs:functions:{guild_id}:{event_id}",
        )
        button.callback = self._on_click
        self.guild_id = guild_id
        self.event_id = event_id
        self.add_item(button)

    async def _on_click(self, interaction: Interaction) -> None:
        await _open_signup_flow(interaction, self.event_id, self.guild_id)


def _build_function_prompt_embed(lang: str, prompt: dict) -> discord.Embed:
    event_id = int(prompt["event_id"])
    title = prompt.get("title") or f"Evento #{event_id}"
    comp = prompt.get("comp_name") or "—"
    scheduled_at = prompt.get("scheduled_at")
    when = f"{_fmt_time(scheduled_at)} UTC{_rel_ts(scheduled_at)}" if scheduled_at else "—"
    description_key = {
        "defined": "signup_roles_dm_defined",
        "changed": "signup_roles_dm_changed",
        "released": "signup_roles_dm_released",
    }.get(prompt.get("reason"), "signup_roles_dm_defined")
    embed = discord.Embed(
        title=t(lang, "signup_roles_dm_title", eid=event_id),
        description=t(lang, description_key),
        color=discord.Color.blurple(),
    )
    embed.add_field(name=t(lang, "signup_roles_dm_event"), value=title[:1024], inline=False)
    embed.add_field(name=t(lang, "signup_roles_dm_comp"), value=comp[:1024], inline=True)
    embed.add_field(name=t(lang, "signup_roles_dm_time"), value=when, inline=True)
    embed.set_footer(text=t(lang, "signup_roles_dm_footer"))
    return embed


class EventDetailsButton(discord.ui.Button):
    def __init__(self, event: dict):
        super().__init__(
            label=_fmt_time(event["scheduled_at"]),
            emoji=_STATUS_EMOJI.get(event["state"], "🗓️"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"ziggs:details:{event['event_id']}",
        )
        self.event_id = event["event_id"]
        self.escalation_token = event.get("escalation_token")

    async def callback(self, interaction: Interaction) -> None:
        if url := _roster_url(self.escalation_token):
            await interaction.response.send_message(
                url,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Escalação indisponível.", ephemeral=True)


class MoreEventsSelect(discord.ui.Select):
    def __init__(self, events: list[dict]):
        options = []
        for e in events[:25]:
            title = e.get("title") or f"Evento #{e['event_id']}"
            options.append(discord.SelectOption(
                label=f"{_fmt_time(e['scheduled_at'])} · {title}"[:100],
                value=str(e["event_id"]),
            ))
        super().__init__(placeholder="Ver outro evento…", options=options)
        self.events = {str(e["event_id"]): e for e in events[:25]}

    async def callback(self, interaction: Interaction) -> None:
        event = self.events[self.values[0]]
        if url := _roster_url(event.get("escalation_token")):
            await interaction.response.send_message(
                url,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Escalação indisponível.", ephemeral=True)


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
            self.add_item(
                EventDetailsButton(event)
                if event.get("signup_mode") == "announcement"
                else EventSignupButton(event)
            )
        if len(events) > MAX_EVENT_BUTTONS:
            self.add_item(MoreEventsSelect(events[MAX_EVENT_BUTTONS:]))


import ephemeral_guard

async def _replace_ephemeral(
    interaction: Interaction,
    content: str | None,
    view: Optional[discord.ui.View],
    *,
    embed: discord.Embed | None = None,
) -> None:
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
    ephemeral_guard.cleanup(interaction)
    try:
        await interaction.delete_original_response()
    except discord.NotFound:
        return  # outro callback já substituiu
    except discord.HTTPException:
        if interaction.message is not None:
            ephemeral_guard.track(interaction, interaction.message.id)
        return
    try:
        kwargs = {"content": content, "ephemeral": True}
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        msg = await interaction.followup.send(**kwargs)
        # followup.send patched já agendou o auto-delete, mas o patch usa o
        # token do webhook — garante que track() registrou com a interaction
        # pra que touch() (on_interaction) reset o timer corretamente.
        if msg is not None and hasattr(msg, "id"):
            ephemeral_guard.track(interaction, msg.id)
    except (discord.HTTPException, discord.NotFound):
        pass


async def _open_signup_flow(
    interaction: Interaction, event_id: int, guild_id: int | None = None,
) -> None:
    target_guild_id = guild_id or interaction.guild_id
    if not target_guild_id:
        await interaction.response.send_message("Não consegui identificar a guilda deste evento.", ephemeral=True)
        return
    lang = await guild_lang_for(target_guild_id)
    member = interaction.user
    guild = interaction.client.get_guild(target_guild_id)
    if guild is not None:
        member = guild.get_member(interaction.user.id) or member
    role_ids = ",".join(str(i) for i in _member_role_ids(member))
    data = await _get(
        f"/bot/events/{target_guild_id}/{event_id}/eligible-functions"
        f"?discord_user_id={interaction.user.id}&discord_role_ids={role_ids}&lang={lang}",
        interactive=True,
    )
    if data is None:
        await interaction.response.send_message(t(lang, "signup_fetch_fail"), ephemeral=True)
        return
    data["_discord_role_ids"] = _member_role_ids(member)

    current = data.get("current_signup")
    if current:
        has_choice = bool(current.get("options") or current.get("functions"))
        if data.get("functions_released") and not has_choice:
            await _send_function_pick(
                interaction, event_id, lang, data,
                replace_prev=False, guild_id=target_guild_id,
            )
            return
        # Sem comp (functions_released=False) e sem roles no signup: não há
        # funções pra alterar — não oferecer o botão "Alterar funções".
        can_change = has_choice or bool(data.get("functions_released"))
        view = AlreadyRegisteredView(
            event_id=event_id, guild_id=target_guild_id, lang=lang, can_change=can_change,
        )
        shown = current.get("labels") or current.get("functions") or []
        if can_change:
            content = t(lang, "signup_already_registered", functions=", ".join(shown) or "—")
        else:
            # Sem comp: a inscrição é só presença. Mensagem dedicada evita o
            # "— O que deseja fazer?" sem função pra mostrar.
            content = t(lang, "signup_already_registered_no_comp")
        await interaction.response.send_message(
            content,
            view=view, ephemeral=True,
        )
        return

    if not data.get("functions_released") or data.get("assignment_mode") == "admin_assign":
        await interaction.response.send_message(
            t(lang, "signup_admin_assign_prompt"),
            view=AdminSignupView(
                event_id=event_id, guild_id=target_guild_id, lang=lang,
                discord_role_ids=data["_discord_role_ids"],
            ),
            ephemeral=True,
        )
        return

    await _send_function_pick(interaction, event_id, lang, data, replace_prev=False, guild_id=target_guild_id)


async def _send_function_pick(
    interaction: Interaction, event_id: int, lang: str, data: dict, *,
    replace_prev: bool, guild_id: int,
) -> None:
    options = data.get("options") or []
    reason = data.get("denial_reason")
    if not options:
        key = "signup_no_slots" if reason == "no_slots" else "signup_no_role"
        content = t(lang, key)
        if replace_prev:
            await _replace_ephemeral(interaction, content, None)
        else:
            await interaction.response.send_message(content, ephemeral=True)
        return

    view = FunctionPickView(
        event_id=event_id, guild_id=guild_id, lang=lang, options=options,
        category_types=data.get("category_types") or {},
        initial_options=data.get("profile_options") or [],
        min_builds=data.get("signup_min_builds"),
        discord_role_ids=data.get("_discord_role_ids") or [],
    )
    embed = view._review_embed() if view.chosen else None
    content = None if embed else view._initial_content()
    if replace_prev:
        await _replace_ephemeral(interaction, content, view, embed=embed)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)


class AdminSignupView(discord.ui.View):
    def __init__(
        self, *, event_id: int, guild_id: int, lang: str,
        discord_role_ids: list[int],
    ):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.guild_id = guild_id
        self.lang = lang
        self.discord_role_ids = discord_role_ids
        confirm = discord.ui.Button(label=t(lang, "signup_confirm_presence"), style=discord.ButtonStyle.success)
        confirm.callback = self._on_confirm
        cancel = discord.ui.Button(label=t(lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def _on_confirm(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        result = await _save_signup(
            self.guild_id, self.event_id, interaction.user, [],
            self.discord_role_ids,
        )
        if result is None:
            await _replace_ephemeral(interaction, t(self.lang, "signup_fail"), None)
            return
        await _replace_ephemeral(interaction, t(self.lang, "signup_presence_success"), None)
        guild = interaction.client.get_guild(self.guild_id)
        if guild is not None:
            asyncio.create_task(_trigger_massinfo_refresh(interaction.client, guild))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(content=t(self.lang, "prefix_cancelled"), view=None)


class AlreadyRegisteredView(discord.ui.View):
    """Já tem inscrição nesse evento — mudar funções, remover, ou não fazer nada."""

    def __init__(self, *, event_id: int, guild_id: int, lang: str, can_change: bool = True):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.guild_id = guild_id
        self.lang = lang

        remove_btn = discord.ui.Button(label=t(lang, "signup_remove_btn"), style=discord.ButtonStyle.danger)
        remove_btn.callback = self._on_remove
        cancel_btn = discord.ui.Button(label=t(lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._on_cancel
        # Só mostra "Alterar funções" se há funções pra alterar (comp definida
        # ou o signup já tinha roles). Sem comp, o botão levaria a um dead-end
        # "sem vagas/sem role" sem sentido.
        if can_change:
            change_btn = discord.ui.Button(label=t(lang, "signup_change_btn"), style=discord.ButtonStyle.primary)
            change_btn.callback = self._on_change
            self.add_item(change_btn)
        self.add_item(remove_btn)
        self.add_item(cancel_btn)

    async def _on_change(self, interaction: Interaction) -> None:
        member = interaction.user
        guild = interaction.client.get_guild(self.guild_id)
        if guild is not None:
            member = guild.get_member(interaction.user.id) or member
        role_ids = ",".join(str(i) for i in _member_role_ids(member))
        data = await _get(
            f"/bot/events/{self.guild_id}/{self.event_id}/eligible-functions"
            f"?discord_user_id={interaction.user.id}&discord_role_ids={role_ids}&lang={self.lang}",
            interactive=True,
        )
        if data is None:
            await _replace_ephemeral(interaction, t(self.lang, "signup_fetch_fail"), None)
            return
        data["_discord_role_ids"] = _member_role_ids(member)
        await _send_function_pick(interaction, self.event_id, self.lang, data, replace_prev=True, guild_id=self.guild_id)

    async def _on_remove(self, interaction: Interaction) -> None:
        path = f"/bot/events/{self.guild_id}/{self.event_id}/signups/{interaction.user.id}"
        result = await _delete(path, attempts=2, queue_on_failure=False)
        if result is None:
            current = await _get(path, interactive=True)
            if current is None or current.get("exists"):
                await _replace_ephemeral(interaction, t(self.lang, "signup_fail"), None)
                return
        await _replace_ephemeral(interaction, t(self.lang, "signup_removed"), None)
        # Refresh imediato — remoção de signup já foi persistida no site.
        guild = interaction.client.get_guild(self.guild_id)
        if guild is not None:
            asyncio.create_task(_trigger_massinfo_refresh(interaction.client, guild))

    async def _on_cancel(self, interaction: Interaction) -> None:
        await _replace_ephemeral(interaction, t(self.lang, "prefix_cancelled"), None)


class FunctionPickView(discord.ui.View):
    """Wizard de inscrição em etapas, espelhando o bot-v1 (massinfo): categoria
    (fn do slot) → opção (uma por vez) → revisão ('adicionar mais' ou
    'confirmar').

    A identidade de cada opção é o PAR (arma, fn) — `key` = "w<weapon_id>:<fn>",
    vindo do backend (`options` do /eligible-functions). Longbow+DPS e
    Longbow+Support são opções distintas; a pré-seleção vem das preferências
    globais do jogador (`profile_options`).

    Diferenças do bot-v1: cada passo REENVIA o ephemeral (delete + novo) em vez
    de editar — contorna o problema do usuário deixar a mensagem aberta até a
    interação expirar. E o dropdown pagina com ◀️/▶️ quando a categoria (ou a
    lista de categorias) passa de 25 opções (limite do Discord).

    Não há limite de pares: o usuário declara tudo que sabe fazer."""

    def __init__(
        self, *, event_id: int, guild_id: int, lang: str, options: list[dict],
        initial_options: Optional[list[str]] = None,
        min_builds: int | None = None, discord_role_ids: Optional[list[int]] = None,
        category_types: Optional[dict[str, dict]] = None,
    ):
        super().__init__(timeout=600)
        self.event_id = event_id
        self.guild_id = guild_id
        self.lang = lang
        # dedup por key, ordem preservada
        self.options: list[dict] = []
        seen: set[str] = set()
        for opt in options:
            key = opt.get("key")
            if key and key not in seen:
                seen.add(key)
                self.options.append(opt)
        self.category_types = category_types or {}
        self.min_builds = min_builds
        self.discord_role_ids = discord_role_ids or []
        allowed = {o["key"] for o in self.options}
        self.chosen: list[str] = [k for k in (initial_options or []) if k in allowed]
        self._remove_page = 0
        self._active_category: str = ""
        self._cat_page = 0
        self._fn_page = 0

        # fn da opção -> opções dessa categoria (o fn É a categoria agora).
        self.by_category: dict[str, list[dict]] = {}
        for opt in self.options:
            self.by_category.setdefault(_fn_of(opt), []).append(opt)

        if self.chosen:
            self._build_review_step()
        elif len(self.by_category) <= 1:
            self._active_category = next(iter(self.by_category), "other")
            self._build_function_step()
        else:
            self._build_category_step()

    def _cat_meta(self, cat: str) -> dict:
        meta = self.category_types.get(cat) or self.category_types.get(_fn_key_norm(cat)) or {}
        return {
            "emoji": meta.get("emoji") or _CATEGORY_EMOJI.get(cat, "❔"),
            "label": meta.get("label") or cat,
            "position": meta.get("position", 999),
        }

    def _available(self, cat: str) -> list[dict]:
        """Opções da categoria que ainda não foram escolhidas (dedup)."""
        chosen = set(self.chosen)
        return [o for o in self.by_category.get(cat, []) if o["key"] not in chosen]

    def _initial_content(self) -> str:
        if len(self.by_category) > 1:
            return t(self.lang, "signup_pick_category_prompt")
        return t(self.lang, "signup_pick_function_prompt")

    # --- renderização: cada etapa monta a view; o callback reenvia o ephemeral ---

    def _build_category_step(self) -> None:
        self.clear_items()
        cats = sorted(
            (c for c in self.by_category if self._available(c)),
            key=lambda category: (self._cat_meta(category)["position"], _category_sort_key(category)),
        )
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
            meta = self._cat_meta(cat)
            options.append(discord.SelectOption(label=f"{meta['emoji']} {meta['label']}", value=cat))
        if pages > 1 and self._cat_page < pages - 1:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_next"), value="__next__"))
        select = discord.ui.Select(
            placeholder=t(self.lang, "signup_pick_category_ph"),
            min_values=1, max_values=1, options=options,
        )
        select.callback = self._on_category
        self.add_item(select)
        if self.chosen:
            back = discord.ui.Button(label=t(self.lang, "signup_back_to_review"), style=discord.ButtonStyle.secondary)
            back.callback = self._on_remove_back
            self.add_item(back)
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
        for opt in chunk:
            # Dentro da categoria o que varia é a arma — o par é único por (arma, fn).
            options.append(discord.SelectOption(label=str(opt.get("weapon_name") or opt.get("key"))[:100], value=opt["key"]))
        if pages > 1 and self._fn_page < pages - 1:
            options.append(discord.SelectOption(label=t(self.lang, "signup_fn_next"), value="__next__"))
        meta = self._cat_meta(self._active_category)
        ph = t(self.lang, "signup_pick_function_ph", cat=meta["label"])
        if pages > 1:
            ph = f"{ph}  ({self._fn_page + 1}/{pages})"
        select = discord.ui.Select(placeholder=ph[:150], min_values=1, max_values=1, options=options)
        select.callback = self._on_function
        self.add_item(select)
        if len(self.by_category) > 1:
            back = discord.ui.Button(label=t(self.lang, "signup_back_to_categories"), style=discord.ButtonStyle.secondary)
            back.callback = self._on_back_to_categories
            self.add_item(back)
        elif self.chosen:
            back = discord.ui.Button(label=t(self.lang, "signup_back_to_review"), style=discord.ButtonStyle.secondary)
            back.callback = self._on_remove_back
            self.add_item(back)
        cancel = discord.ui.Button(label=t(self.lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _build_review_step(self) -> None:
        self.clear_items()
        if len(self.chosen) < len(self.options):
            add_btn = discord.ui.Button(label=t(self.lang, "signup_add_more"), style=discord.ButtonStyle.primary)
            add_btn.callback = self._on_add_more
            self.add_item(add_btn)
        if self.chosen:
            remove_btn = discord.ui.Button(label=t(self.lang, "signup_remove_roles"), style=discord.ButtonStyle.secondary)
            remove_btn.callback = self._on_remove_roles_open
            self.add_item(remove_btn)
        done_btn = discord.ui.Button(label=t(self.lang, "signup_done_btn"), style=discord.ButtonStyle.success)
        done_btn.callback = self._on_done
        self.add_item(done_btn)
        cancel = discord.ui.Button(label=t(self.lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def _build_remove_step(self) -> None:
        self.clear_items()
        pages = max(1, -(-len(self.chosen) // 25))
        self._remove_page %= pages
        start = self._remove_page * 25
        chunk = self.chosen[start:start + 25]
        by_key = {o["key"]: o for o in self.options}
        select = discord.ui.Select(
            placeholder=t(self.lang, "signup_remove_roles_ph"),
            min_values=1, max_values=len(chunk),
            options=[discord.SelectOption(label=_pair_display(by_key.get(k), k)[:100], value=k) for k in chunk],
        )
        select.callback = self._on_remove_roles
        self.add_item(select)
        if pages > 1 and self._remove_page > 0:
            prev = discord.ui.Button(label=t(self.lang, "signup_fn_prev"), style=discord.ButtonStyle.secondary)
            prev.callback = self._on_remove_prev
            self.add_item(prev)
        if pages > 1 and self._remove_page < pages - 1:
            nxt = discord.ui.Button(label=t(self.lang, "signup_fn_next"), style=discord.ButtonStyle.secondary)
            nxt.callback = self._on_remove_next
            self.add_item(nxt)
        back = discord.ui.Button(label=t(self.lang, "signup_back_to_review"), style=discord.ButtonStyle.secondary)
        back.callback = self._on_remove_back
        self.add_item(back)

    def _review_embed(self, error: str | None = None) -> discord.Embed:
        description = t(self.lang, "signup_review_prompt")
        if error:
            description = f"{error}\n\n{description}"
        embed = discord.Embed(
            title=t(self.lang, "signup_chosen_header", n=len(self.chosen)),
            description=description,
            color=discord.Color.blurple(),
        )
        if not self.chosen:
            embed.add_field(
                name=t(self.lang, "signup_roles_field"),
                value=f"*{t(self.lang, 'signup_none_yet')}*",
                inline=False,
            )
        by_key = {o["key"]: o for o in self.options}
        ordered = sorted(
            self.chosen,
            key=lambda key: (
                self._cat_meta(_fn_of(by_key.get(key) or {}))["position"],
                _category_sort_key(_fn_of(by_key.get(key) or {})),
                str((by_key.get(key) or {}).get("weapon_name") or key),
            ),
        )
        lines = []
        for key in ordered:
            opt = by_key.get(key) or {}
            meta = self._cat_meta(_fn_of(opt))
            lines.append(f"{meta['emoji']} {_pair_display(opt, key)}")
        column_count = min(3, max(1, -(-len(lines) // 8)))
        rows_per_column = max(1, -(-len(lines) // column_count))
        chunks: list[list[str]] = [[]]
        for line in lines:
            current = chunks[-1]
            if current and (
                len(current) >= rows_per_column
                or len("\n".join(current)) + len(line) + 1 > 900
            ):
                chunks.append([])
            chunks[-1].append(line)
        multiple_columns = len(chunks) > 1
        for chunk in chunks:
            if not chunk:
                continue
            embed.add_field(name="\u200b", value="\n".join(chunk), inline=multiple_columns)
        if self.min_builds:
            embed.set_footer(text=t(
                self.lang,
                "signup_minimum_footer",
                n=self.min_builds,
            ))
        return embed

    def _minimum_error(self) -> str | None:
        if not self.min_builds:
            return None
        if len(self.chosen) >= self.min_builds:
            return None
        return t(self.lang, "signup_min_builds_needed", n=self.min_builds)

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
        # escolheu UM par → vai pra revisão (adicionar mais ou confirmar).
        if val not in self.chosen:
            self.chosen.append(val)
        self._build_review_step()
        await _replace_ephemeral(interaction, None, self, embed=self._review_embed())

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

    async def _on_remove_roles_open(self, interaction: Interaction) -> None:
        self._remove_page = 0
        self._build_remove_step()
        await _replace_ephemeral(interaction, t(self.lang, "signup_remove_roles_prompt"), self)

    async def _on_remove_roles(self, interaction: Interaction) -> None:
        values = set(self.children[0].values)
        self.chosen = [f for f in self.chosen if f not in values]
        self._build_review_step()
        await _replace_ephemeral(interaction, None, self, embed=self._review_embed())

    async def _on_remove_prev(self, interaction: Interaction) -> None:
        self._remove_page = max(0, self._remove_page - 1)
        self._build_remove_step()
        await _replace_ephemeral(interaction, t(self.lang, "signup_remove_roles_prompt"), self)

    async def _on_remove_next(self, interaction: Interaction) -> None:
        self._remove_page += 1
        self._build_remove_step()
        await _replace_ephemeral(interaction, t(self.lang, "signup_remove_roles_prompt"), self)

    async def _on_remove_back(self, interaction: Interaction) -> None:
        self._build_review_step()
        await _replace_ephemeral(interaction, None, self, embed=self._review_embed())

    async def _on_done(self, interaction: Interaction) -> None:
        if error := self._minimum_error():
            self._build_review_step()
            await _replace_ephemeral(interaction, None, self, embed=self._review_embed(error))
            return
        # acka antes do POST pra não estourar o timeout de 15s do Discord.
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException, discord.NotFound):
            pass
        result = await _save_signup(
            self.guild_id, self.event_id, interaction.user, self.chosen,
            self.discord_role_ids,
        )
        if result is None:
            await _replace_ephemeral(interaction, t(self.lang, "signup_fail"), None)
            return
        by_key = {o["key"]: o for o in self.options}
        applied = result.get("labels") or [
            _pair_display(by_key.get(k), k) for k in (result.get("options") or self.chosen)
        ]
        mode = result.get("assignment_mode")
        key = "signup_success_self" if mode == "self_select" else "signup_success_hybrid"
        await _replace_ephemeral(
            interaction,
            t(self.lang, key, functions=", ".join(applied)), None,
        )
        # Refresh imediato do mass-info — o signup já foi persistido no site,
        # não precisa esperar o próximo ciclo do polling (5s) pro embed
        # refletir o novo contador.
        guild = interaction.client.get_guild(self.guild_id)
        if guild is not None:
            asyncio.create_task(_trigger_massinfo_refresh(interaction.client, guild))

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
        self._massinfo_locks: dict[int, asyncio.Lock] = {}
        self._function_prompt_sent: dict[
            tuple[int, int, int], tuple[str, tuple]
        ] = {}

    async def send_function_prompts(self, guild: discord.Guild, prompts: list[dict]) -> list[dict]:
        """Entrega as DMs e devolve os IDs que o backend deve rastrear."""
        if not prompts:
            return []
        lang = await guild_lang_for(guild.id)
        sent: list[dict] = []
        for prompt in prompts:
            key = (guild.id, int(prompt["event_id"]), int(prompt["user_id"]))
            signature = (
                prompt.get("title"),
                prompt.get("comp_name"),
                prompt.get("scheduled_at"),
                prompt.get("reason"),
            )
            cached = self._function_prompt_sent.get(key)
            if cached and cached[1] == signature:
                sent.append({
                    "event_id": key[1],
                    "user_id": key[2],
                    "message_id": cached[0],
                })
                continue
            user = guild.get_member(int(prompt["user_id"]))
            if user is None:
                try:
                    user = await dtimeout(self.bot.fetch_user(int(prompt["user_id"])))
                except SKIP_EXC:
                    continue
            if cached:
                try:
                    dm = user.dm_channel or await dtimeout(user.create_dm())
                    old_message = await dtimeout(dm.fetch_message(int(cached[0])))
                    await dtimeout(old_message.delete())
                except discord.NotFound:
                    pass
                except SKIP_EXC:
                    continue
                self._function_prompt_sent.pop(key, None)
            try:
                message = await dtimeout(user.send(
                    embed=_build_function_prompt_embed(lang, prompt),
                    view=FunctionPromptView(guild.id, int(prompt["event_id"]), lang),
                ))
                sent.append({
                    "event_id": int(prompt["event_id"]),
                    "user_id": int(prompt["user_id"]),
                    "message_id": str(message.id),
                })
                self._function_prompt_sent[key] = (str(message.id), signature)
            except SKIP_EXC:
                pass
        return sent

    async def delete_function_prompts(self, records: list[dict]) -> list[str]:
        """Apaga DMs de escolha de roles quando o evento deixa de aceitar signup."""
        deleted: list[str] = []
        for record in records:
            try:
                user = await dtimeout(self.bot.fetch_user(int(record["user_id"])))
                dm = user.dm_channel or await dtimeout(user.create_dm())
                message = await dtimeout(dm.fetch_message(int(record["message_id"])))
                await dtimeout(message.delete())
                deleted.append(str(record["message_id"]))
            except (discord.NotFound, discord.Forbidden):
                deleted.append(str(record["message_id"]))
            except SKIP_EXC:
                pass
            event_user = (int(record["event_id"]), int(record["user_id"]))
            for key in [key for key in self._function_prompt_sent if key[1:] == event_user]:
                self._function_prompt_sent.pop(key, None)
        return deleted

    async def sync_massinfo(
        self, guild: discord.Guild, events: list[dict],
        ping_triggers: list[dict] | None = None,
        *, purge_orphans: bool = False,
    ) -> None:
        lock = self._massinfo_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_massinfo_unlocked(
                guild, events, ping_triggers, purge_orphans=purge_orphans,
            )

    async def _sync_massinfo_unlocked(
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
                message = await dtimeout(channel.fetch_message(message_id))
            except SKIP_EXC:
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
                message = await dtimeout(channel.send(content=content, embed=embed, view=view,
                                                      allowed_mentions=mentions))
                sent = True
            except SKIP_EXC:
                # Falhou o reenvio — tenta cair no edit in-place abaixo como fallback.
                message = None

        if not sent:
            if message is None:
                try:
                    message = await dtimeout(channel.send(embed=embed, view=view))
                except SKIP_EXC:
                    return
            else:
                try:
                    await dtimeout(message.edit(embed=embed, view=view))
                except SKIP_EXC:
                    return

        _massinfo_message_ids[guild.id] = message.id
        # Persiste a mensagem e confirma os pings na mesma transação. Dois
        # ACKs separados deixavam uma janela em que o próximo poll bumpava a
        # mensagem novamente.
        await _post(
            f"/bot/events/{guild.id}/massinfo-synced",
            {
                "message_id": str(message.id),
                "ack_ping_triggers": bool(triggers),
            },
            tag="massinfo", attempts=2,
        )

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
