"""Lootlog anônimo: botão no embed da thread → modal com FileUpload → ingest privado.

Espelho do bot-v1: o .csv NUNCA aparece em canal público. O usuário clica em
'📤 Enviar log' no embed da thread de lootlog do evento, anexa o arquivo num modal
(discord.ui.FileUpload, discord.py 2.7+) e a submissão é ephemeral (só o bot lê).
O bot repassa o .csv ao backend (POST /bot/guilds/{g}/lootlog/ingest, auth Bearer
BOT_API_SECRET) que resolve event_id pelo id da thread (Event.lootlog_thread_id).

Após ingest: só confirmação ephemeral pro logger. NENHUMA mensagem é postada na
thread (sem audit, sem standings pinado) — a % de peso de cada logger vai no MESMO
embed do botão, editado a cada ingest (_update_header_embed). on_message apaga
qualquer msg não-bot na thread (defesa em profundidade — Send Messages negado).

O event_id não vai no botão: a rota ingest resolve pelo thread_id (igual v1 usa
get_event_by_logger_thread). custom_id fixo → View persistente sobrevive a restart.
"""
import asyncio
import traceback

import aiohttp
import discord
from discord.ext import commands

import http_client
from cogs.general import _guild_command_config, guild_lang_for
from i18n import t

# custom_id fixo: o event_id é resolvido no backend pelo thread_id (igual v1).
SUBMIT_CID = "lootlog:submit_v2"


# ── helpers de mensagem ephemeral (tolerantes a interação já respondida) ─────
async def _ephemeral(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        pass


class EnviarLogModal(discord.ui.Modal, title="Enviar log do evento"):
    """Modal com upload de arquivo (.csv/.txt do lootlogger). Submissão ephemeral."""
    def __init__(self, lang: str):
        super().__init__(title=t(lang, "ev_lootlog_modal_title"))
        self.lang = lang
        self.arquivo = discord.ui.FileUpload(
            custom_id="log_csv", min_values=1, max_values=1, required=True)
        # Label.text tem limite de 45 chars no Discord; detalhe vai na description.
        self.add_item(discord.ui.Label(
            text=t(lang, "ev_lootlog_modal_label"),
            description=t(lang, "ev_lootlog_modal_desc"),
            component=self.arquivo,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        files = list(self.arquivo.values or [])
        if not files:
            await _ephemeral(interaction, t(self.lang, "ev_lootlog_no_file"))
            return
        await _process_submission(interaction, files[0], self.lang)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception) -> None:
        traceback.print_exc()
        print(f"[lootlogs] erro no modal: {type(error).__name__}: {error}")
        await _ephemeral(interaction, t(self.lang, "ev_lootlog_ingest_err"))


class LootlogSubmitView(discord.ui.View):
    """View persistente (custom_id fixo) — sobrevive a restart via bot.add_view().
    O label do botão é localizado no __init__ (a posted message mostra esse label;
    o callback vem do custom_id registrado em add_view)."""
    def __init__(self, lang: str = "pt"):
        super().__init__(timeout=None)
        self.lang = lang
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == SUBMIT_CID:
                item.label = t(lang, "ev_lootlog_submit_btn")

    @discord.ui.button(label="📤 Enviar log", style=discord.ButtonStyle.primary,
                       custom_id=SUBMIT_CID)
    async def enviar(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        lang = guild_lang_for(interaction.guild.id) if interaction.guild else "pt"
        try:
            await interaction.response.send_modal(EnviarLogModal(lang))
        except discord.HTTPException as e:
            print(f"[lootlogs] erro abrindo modal: {type(e).__name__}: {e}")
            await _ephemeral(interaction, t(lang, "ev_lootlog_modal_err"))


async def _process_submission(interaction: discord.Interaction,
                               arquivo: discord.Attachment, lang: str) -> None:
    """Lê o anexo, posta no backend e confirma ephemeral. NENHUMA mensagem é
    postada na thread — a % de peso de cada logger vai no MESMO embed do botão
    (editado a cada ingest via _update_header_embed)."""
    # Ler o anexo pode passar de 3s → ack ephemeral antes.
    try:
        await interaction.response.defer(ephemeral=True)
    except (discord.InteractionResponded, discord.HTTPException):
        pass
    thread = interaction.channel
    thread_id = thread.id if isinstance(thread, discord.Thread) else None
    try:
        data = await arquivo.read()
    except Exception:
        await _ephemeral(interaction, t(lang, "ev_lootlog_read_err"))
        return

    out = None
    for attempt in range(2):
        form = aiohttp.FormData()
        form.add_field("file", data, filename=arquivo.filename or "log.csv",
                       content_type="text/csv")
        form.add_field("submitter_name", interaction.user.display_name)
        form.add_field("submitter_user_id", str(interaction.user.id))
        form.add_field("thread_id", str(thread_id) if thread_id else "")
        out = await http_client.post_form(
            f"/bot/guilds/{interaction.guild.id}/lootlog/ingest",
            form, timeout=20, tag="lootlogs",
        )
        if out is not None:
            break
        if attempt == 0:
            await asyncio.sleep(0.2)

    if out is None:
        await _ephemeral(interaction, t(lang, "ev_lootlog_ingest_fail"))
        return

    n = (out or {}).get("row_count") or 0
    await _ephemeral(interaction, t(lang, "ev_lootlog_thanks", n=n))

    # Atualiza a % de cada logger no embed do botão (mesma msg, sem postar nada).
    if isinstance(thread, discord.Thread):
        standings = (out or {}).get("standings")
        if standings is not None:
            await _update_header_embed(thread, lang, standings)


async def _update_header_embed(thread: discord.Thread, lang: str,
                                standings: list[dict]) -> None:
    """Edita o embed de cabeçalho da thread (o do botão 'Enviar log') p/ mostrar
    a % de peso de cada logger. Não posta nem pina nada — só edita a 1ª msg do
    bot, mantendo o botão (view não é tocado)."""
    rows = [s for s in standings if s.get("user_id")]
    try:
        msgs = [m async for m in thread.history(limit=1, oldest_first=True)]
    except (discord.Forbidden, discord.HTTPException):
        return
    if not msgs:
        return
    msg = msgs[0]
    if not msg.embeds:
        return
    embed = msg.embeds[0]
    # Header do lootlog só tem title+description; limpa fields e re-adiciona o
    # de loggers (idempotente entre ingests).
    embed.clear_fields()
    if rows:
        body = "\n".join(f"<@{s['user_id']}> {s.get('percent') or 0}%" for s in rows)
        embed.add_field(name=t(lang, "ev_lootlog_standings_title", n=len(rows)),
                        value=body, inline=False)
    try:
        await msg.edit(embed=embed)  # view omitido → botão continua lá
    except (discord.Forbidden, discord.HTTPException):
        pass


class Lootlogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Registra o callback do botão persistente (sobrevive a restart).
        self.bot.add_view(LootlogSubmitView())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Defesa em profundidade: apaga qualquer msg não-bot em thread filha do
        canal de lootlog. A prevenção real é Send Messages negado no canal; isto
        só age sobre quem fura o bloqueio (ex.: admin). O .csv só entra pelo botão."""
        if message.guild is None or message.author.bot:
            return
        ch = message.channel
        if not isinstance(ch, discord.Thread):
            return
        parent_id = ch.parent_id
        if parent_id is None:
            return
        lootlog_chan = await _lootlog_thread_channel_id(message.guild.id)
        if lootlog_chan is None or parent_id != lootlog_chan:
            return
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass


async def _lootlog_thread_channel_id(guild_id: int) -> int | None:
    """Canal dedicado onde o bot cria threads de lootlog por evento."""
    cfg = await _guild_command_config(guild_id)
    cid = cfg.get("lootlog_thread_channel_id")
    try:
        return int(cid) if cid else None
    except (TypeError, ValueError):
        return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Lootlogs(bot))
