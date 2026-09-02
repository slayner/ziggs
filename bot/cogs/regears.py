"""
Sistema de regears.

Fluxo:
1. Listener detecta imagem (.png/.jpg/.jpeg) em:
     · channel_zergregear (canal principal OU qualquer thread dele)
     · channel_bombregear (canal principal — não tem threads)
2. Faz download do attachment, posta um embed com a IMAGEM RE-UPLOADADA
   (assim a mensagem original pode ser deletada com segurança), e deleta
   a mensagem do usuário.
3. Embed tem 3 botões:
     · 🗑️ Remover    (author original + logistic)
     · 🚫 Negar      (logistic)
     · 💰 Valor (logistic) → modal → paga do guild_bank
"""
import os
import io
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands
from discord import ui
from dotenv import load_dotenv

import database
from database import (
    is_server_activated,
    load_economy_config,
    create_regear, get_regear_by_message_id, update_regear_status,
    get_guild_bank_balance, remove_guild_bank, add_user_money,
)
from cogs.economy import has_configured_role
from utils import (parse_silver, format_silver, EMBED_OK, EMBED_ERR, EMBED_WARN,
                   send_err, send_ok, send_warn, guild_filesize_limit, human_size)

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))


# Aceita somente .png / .jpg / .jpeg conforme requisito
ALLOWED_IMAGE_EXTS = ('.png', '.jpg', '.jpeg')


def _is_allowed_image(att: discord.Attachment) -> bool:
    """Aceita só .png/.jpg/.jpeg. Outros formatos (gif, webp, vídeos, etc) são ignorados."""
    name = (att.filename or '').lower()
    return name.endswith(ALLOWED_IMAGE_EXTS)


def _attachment_filename_from_url(url):
    """Extrai o nome do anexo a partir da URL de CDN salva (descarta a query string)."""
    return url.split('?', 1)[0].rsplit('/', 1)[-1] if url else None


def _apply_regear_image(embed, image_url):
    """Aponta o embed pra imagem do regear. Igual ao tabsell: se for anexo do Discord,
    referencia via `attachment://<filename>` (o anexo é PRESERVADO nas edições e fica
    DENTRO do embed); caso contrário usa a URL direta."""
    if not image_url:
        return
    fname = _attachment_filename_from_url(image_url)
    if fname and ('discordapp.com' in image_url or 'discordapp.net' in image_url):
        embed.set_image(url=f"attachment://{fname}")
    else:
        embed.set_image(url=image_url)


async def _finalize_regear_message(interaction, message_id, embed, image_url):
    """Finaliza a mensagem do regear (pago/negado): EDITA a mensagem original no lugar,
    mantendo o print DENTRO do embed e removendo os botões.

    Padrão copiado do tabsell (comprovadamente funcional): NÃO passamos `attachments=`
    no edit — assim o Discord PRESERVA o anexo já existente — e apontamos o embed pra
    `attachment://<filename>` derivado da `image_url` salva no DB.

    Por que NÃO ler de `msg.attachments`? Quando o anexo já foi consumido por um embed,
    essa lista volta VAZIA num fetch; passar `attachments=[]` (lista vazia) no edit
    APAGARIA a imagem — exatamente o bug que estávamos vendo. Editar (em vez de reenviar)
    também preserva o `message_id` linkado no DB e a posição da mensagem no canal."""
    # ACK primeiro: fecha o modal / para o "loading" do botão dentro dos 3s.
    try:
        await interaction.response.defer()
    except discord.HTTPException:
        pass  # já respondida (esperado em alguns caminhos)

    _apply_regear_image(embed, image_url)
    try:
        # get_partial_message + edit SEM `attachments=` → anexo preservado, imagem
        # revinculada no embed. `view=None` remove os botões (regear finalizado).
        await interaction.channel.get_partial_message(message_id).edit(embed=embed, view=None)
    except Exception as e:
        print(f"✗ Erro editando regear finalizado: {e}")


async def _download_regear_image(image_url):
    """Baixa os bytes do print pela URL salva (anexo do bot). Best-effort: retorna
    `(bytes, filename)` ou `(None, None)`. Usado só pra reanexar no log e deixar a
    imagem PERMANENTE lá, independente da expiração da URL de CDN do Discord."""
    if not image_url:
        return None, None
    fname = _attachment_filename_from_url(image_url) or "regear.png"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    return await resp.read(), fname
                print(f"✗ Download do print do regear retornou HTTP {resp.status}")
    except Exception as e:
        print(f"✗ Erro baixando print do regear pra log: {e}")
    return None, None


# ==================================================================
# View persistente: 3 botões do regear
# ==================================================================
class RegearView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🗑️", style=discord.ButtonStyle.secondary, custom_id="regear:remove_v1")
    async def remove_btn(self, interaction: discord.Interaction, _b: ui.Button):
        regear = await get_regear_by_message_id(interaction.message.id)
        if not regear:
            await send_err(interaction, "Regear não encontrado.")
            return
        if regear['status'] != 'pending':
            await send_err(interaction, f"Regear já está com status `{regear['status']}`, "
                                        f"não pode ser removido.")
            return

        # Permissão: author original OU logistic OU owner
        is_owner    = interaction.user.id == OWNER_ID
        is_author   = interaction.user.id == regear['user_id']
        is_logistic = await has_configured_role(interaction.user, 'role_logistic')
        if not (is_owner or is_author or is_logistic):
            await send_err(interaction, "Apenas o autor original ou logistic pode remover.")
            return

        await update_regear_status(
            interaction.message.id, status='removed', handled_by=interaction.user.id,
        )
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass  # msg já apagada / sem permissão (esperado)
        await send_ok(interaction, "Regear removido.")

    @ui.button(label="🚫 Negar", style=discord.ButtonStyle.danger, custom_id="regear:deny_v1")
    async def deny_btn(self, interaction: discord.Interaction, _b: ui.Button):
        regear = await get_regear_by_message_id(interaction.message.id)
        if not regear:
            await send_err(interaction, "Regear não encontrado.")
            return
        if regear['status'] != 'pending':
            await send_err(interaction, f"Regear já está com status `{regear['status']}`.")
            return

        # Permissão: logistic
        if interaction.user.id != OWNER_ID and not await has_configured_role(
            interaction.user, 'role_logistic'
        ):
            await send_err(interaction, "Apenas logistic pode negar regears.")
            return

        await update_regear_status(
            interaction.message.id, status='denied', handled_by=interaction.user.id,
        )

        # Edita o embed pro estado "negado" (mantendo o print dentro do embed)
        embed = discord.Embed(
            title=f"🚫  Regear NEGADO — <t:{int(datetime.now(timezone.utc).timestamp())}:f>",
            description=(
                f"**Usuário:** <@{regear['user_id']}>\n"
                f"**Negado por:** {interaction.user.mention}"
            ),
            color=EMBED_ERR,
            timestamp=datetime.now(timezone.utc),
        )
        await _finalize_regear_message(
            interaction, interaction.message.id, embed, regear['image_url'],
        )

    @ui.button(label="💰 Valor", style=discord.ButtonStyle.success, custom_id="regear:setvalue_v1")
    async def set_value_btn(self, interaction: discord.Interaction, _b: ui.Button):
        regear = await get_regear_by_message_id(interaction.message.id)
        if not regear:
            await send_err(interaction, "Regear não encontrado.")
            return
        if regear['status'] != 'pending':
            await send_err(interaction, f"Regear já está com status `{regear['status']}`.")
            return

        # Permissão: logistic
        if interaction.user.id != OWNER_ID and not await has_configured_role(
            interaction.user, 'role_logistic'
        ):
            await send_err(interaction, "Apenas logistic pode definir valor de regears.")
            return

        await interaction.response.send_modal(RegearValueModal(message_id=interaction.message.id))


# ==================================================================
# Modal de definir valor
# ==================================================================
class RegearValueModal(ui.Modal, title="Definir valor do regear"):
    value_input = ui.TextInput(
        label="Valor a pagar (prata)",
        placeholder="Ex: 500,000  ou  500k  ou  1.500.000",
        max_length=30,
    )

    def __init__(self, message_id: int):
        super().__init__()
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        parsed = parse_silver(self.value_input.value)
        if parsed is None or parsed <= 0:
            await send_err(interaction, "Valor inválido.")
            return

        regear = await get_regear_by_message_id(self.message_id)
        if not regear:
            await send_err(interaction, "Regear não encontrado.")
            return
        if regear['status'] != 'pending':
            await send_err(interaction, f"Regear já está com status `{regear['status']}`.")
            return

        # Checa saldo do banco da guild
        bank_balance = await get_guild_bank_balance()
        if bank_balance < parsed:
            await send_err(interaction, f"Banco da guild sem saldo suficiente.\n"
                                        f"Disponível: `{format_silver(bank_balance)}`  ·  "
                                        f"Necessário: `{format_silver(parsed)}`.")
            return

        # Debita guild bank + paga o usuário
        await remove_guild_bank(parsed)
        await add_user_money(regear['user_id'], parsed)

        # Atualiza DB
        await update_regear_status(
            self.message_id, status='paid',
            handled_by=interaction.user.id, value=parsed,
        )

        # Edita o embed pro estado "pago" (mantendo o print dentro do embed).
        new_bank = await get_guild_bank_balance()
        embed = discord.Embed(
            title=f"✅  Regear Pago",
            description=(
                f"<@{regear['user_id']}> recebeu {format_silver(parsed)}\n"
                f"Aprovado por: {interaction.user.mention}\n\n"
            ),
            color=EMBED_OK,
        )
        await _finalize_regear_message(
            interaction, self.message_id, embed, regear['image_url'],
        )

        # Posta no economylogs — baixa o print pela URL salva e REANEXA, pra ficar
        # permanente lá (independente da expiração da URL de CDN do Discord).
        cfg = await load_economy_config()
        elogs_id = cfg.get('channel_economylogs')
        if elogs_id and interaction.guild:
            ch = interaction.guild.get_channel(elogs_id)
            if ch:
                try:
                    log_embed = discord.Embed(
                        title="💰  𝐑𝐄𝐆𝐄𝐀𝐑 𝐏𝐀𝐆𝐎",
                        description=(f"<@{regear['user_id']}>  💰 **+{format_silver(parsed)}**\n"
                                     f"banco `{format_silver(new_bank)}`"),
                        color=EMBED_OK,
                    )
                    log_embed.set_footer(text=f"por {interaction.user.display_name}")
                    img_bytes, img_name = await _download_regear_image(regear['image_url'])
                    if img_bytes and img_name:
                        log_file = discord.File(io.BytesIO(img_bytes), filename=img_name)
                        log_embed.set_image(url=f"attachment://{img_name}")
                        await ch.send(embed=log_embed, file=log_file)
                    elif regear['image_url']:
                        # Fallback: referencia a URL direta (best-effort, pode expirar).
                        log_embed.set_image(url=regear['image_url'])
                        await ch.send(embed=log_embed)
                    else:
                        await ch.send(embed=log_embed)
                except Exception as e:
                    print(f"✗ Erro postando regear no economylogs: {e}")


# ==================================================================
# Cog principal
# ==================================================================
class RegearsCog(commands.Cog, name="RegearsCog"):
    """Detecta attachments no canal de zergregear e cria embeds de regear."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pending_scanned = False  # garante que o scan só rode 1× por sessão

    async def cog_load(self):
        # Registra a view persistente para os botões funcionarem após restart
        self.bot.add_view(RegearView())
        print("✓ Regears Cog carregada")
        # Agenda o scan de pendentes pra rodar logo após o bot conectar
        self.bot.loop.create_task(self._scan_pending_when_ready())

    async def _scan_pending_when_ready(self):
        """Espera o bot conectar e então faz a varredura de mensagens pendentes."""
        await self.bot.wait_until_ready()
        if self._pending_scanned:
            return
        self._pending_scanned = True
        for gid in await database.get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._scan_pending_on_startup()
                except Exception as e:
                    print(f"✗ Erro no scan inicial de regears [{gid}]: {e}")

    async def _scan_pending_on_startup(self):
        """
        Varre os canais de regear (e threads ativas do zerg) procurando attachments
        que ficaram lá enquanto o bot estava offline. Como o bot SEMPRE deleta a
        mensagem original do user após processar, qualquer mensagem com attach
        ainda existente é uma pendência.
        """
        cfg     = await load_economy_config()
        zerg_id = cfg.get('channel_zergregear')
        bomb_id = cfg.get('channel_bombregear')

        targets: list = []
        if zerg_id:
            ch = self.bot.get_channel(zerg_id)
            if ch:
                targets.append(ch)
                # Threads ativas do canal de zerg (não inclui arquivadas)
                for t in getattr(ch, 'threads', []) or []:
                    targets.append(t)
        if bomb_id:
            ch = self.bot.get_channel(bomb_id)
            if ch and ch.id != zerg_id:  # evita duplicação se setup batendo
                targets.append(ch)

        if not targets:
            return

        total_processed = 0
        for ch in targets:
            try:
                # Coleta mensagens mais novas primeiro, depois reverte para
                # processar em ordem cronológica (mais antiga primeiro)
                recent = []
                async for msg in ch.history(limit=100):
                    if msg.author.bot or not msg.attachments:
                        continue
                    recent.append(msg)
                recent.reverse()

                if recent:
                    print(f"  → {len(recent)} mensagem(ns) pendente(s) em {ch.name if hasattr(ch, 'name') else ch.id}")

                for msg in recent:
                    try:
                        await self._process_regear_message(msg)
                        total_processed += 1
                    except Exception as e:
                        print(f"✗ Erro processando msg pendente {msg.id}: {e}")
            except discord.Forbidden:
                print(f"✗ Sem permissão de ler histórico de {ch}")
            except Exception as e:
                print(f"✗ Erro scaneando {ch}: {e}")

        if total_processed > 0:
            print(f"✓ Scan inicial de regears concluído — {total_processed} processada(s)")

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            return False
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Delega tudo pro método compartilhado (que faz o gating do canal)."""
        await self._process_regear_message(message)

    async def _process_regear_message(self, message: discord.Message):
        """
        Processa UMA mensagem com attach em canal de regear.
        Self-contained: faz o gating de bot/guild/attach/canal antes de qualquer ação.
        É usado tanto pelo listener (tempo real) quanto pelo scan inicial (offline backlog).
        """
        if message.author.bot or not message.guild or not message.attachments:
            return
        database.set_current_guild(message.guild.id)   # multi-tenant: banco do servidor

        # Gating de canal/thread
        cfg     = await load_economy_config()
        zerg_id = cfg.get('channel_zergregear')
        bomb_id = cfg.get('channel_bombregear')
        channel = message.channel

        # zerg → canal principal OU qualquer thread dele
        in_zerg = (
            (zerg_id and channel.id == zerg_id)
            or (zerg_id and isinstance(channel, discord.Thread) and channel.parent_id == zerg_id)
        )
        # bomb → só o canal principal (sem threads, conforme spec)
        in_bomb = (bomb_id and channel.id == bomb_id)

        if not (in_zerg or in_bomb):
            return

        # ---- Bloqueia múltiplos attaches: deleta msg + avisa + auto-some em 10s ----
        if len(message.attachments) > 1:
            try:
                await message.delete()
            except discord.Forbidden:
                print(f"✗ Sem permissão para deletar a mensagem no canal {channel.id}")
            except discord.NotFound:
                pass
            try:
                await channel.send(
                    f"{message.author.mention} ⚠️ Envie **apenas 1 imagem por vez** "
                    "no canal de regear. Sua mensagem foi removida — tente novamente "
                    "anexando uma única imagem.",
                    delete_after=10,
                )
            except Exception as e:
                print(f"✗ Erro enviando aviso de múltiplos attaches: {e}")
            return

        # A partir daqui sabemos que tem EXATAMENTE 1 attachment
        att = message.attachments[0]

        # Filtra: aceita só .png/.jpg/.jpeg — ignora silenciosamente outros formatos
        if not _is_allowed_image(att):
            return

        # Grande demais pro teto de upload do servidor? O fluxo re-hospeda a imagem
        # (e apaga a original), então sem conseguir re-enviar não dá pra processar:
        # APAGA a imagem pesada e avisa o autor (aviso some em 15s).
        limit = guild_filesize_limit(message.guild)
        if att.size and att.size > limit:
            try:
                await message.delete()
            except discord.Forbidden:
                print(f"✗ Sem permissão para apagar imagem pesada no canal {channel.id}")
            except discord.NotFound:
                pass
            try:
                await channel.send(
                    f"{message.author.mention} ⚠️ Sua imagem ({human_size(att.size)}) é muito "
                    f"pesada — passa do limite de upload do servidor ({human_size(limit)}). "
                    f"Comprima/redimensione e reenvie pra abrir o regear.",
                    delete_after=15,
                )
            except Exception as e:
                print(f"✗ Erro avisando regear acima do limite: {e}")
            return

        regear_kind = "bomb" if in_bomb else "zerg"
        now = datetime.now(timezone.utc)

        # 1) Baixa o conteúdo do attachment original
        try:
            file_bytes = await att.read()
        except Exception as e:
            print(f"✗ Erro lendo attachment {att.filename}: {e}")
            return

        # 2) Cria um File com nome seguro e referencia via attachment://
        safe_name = att.filename or f"regear_{now.timestamp():.0f}.png"
        safe_name = "".join(c if c.isalnum() or c in ('.', '-', '_') else '_' for c in safe_name)
        file_obj  = discord.File(io.BytesIO(file_bytes), filename=safe_name)

        # 3) Monta o embed apontando para o file inline
        embed = discord.Embed(
            title=f"⏳ Regear Pendente",
            description=(
                f"{message.author.mention}"
            ),
            color=EMBED_WARN,
        )
        embed.set_image(url=f"attachment://{safe_name}")

        # 4) Envia (embed + file + view)
        try:
            bot_msg = await channel.send(
                embed=embed,
                file=file_obj,
                view=RegearView(),
            )
        except discord.Forbidden:
            print(f"✗ Sem permissão para postar regear no canal {channel.id}")
            return
        except Exception as e:
            print(f"✗ Erro criando embed de regear: {e}")
            return

        # 5) Persistir no DB (image_url é a URL do attachment do BOT — permanente)
        bot_image_url = bot_msg.attachments[0].url if bot_msg.attachments else att.url
        try:
            await create_regear(
                user_id    = message.author.id,
                user_name  = message.author.display_name,
                guild_id   = message.guild.id,
                channel_id = channel.id,
                message_id = bot_msg.id,
                image_url  = bot_image_url,
            )
        except Exception as e:
            print(f"✗ Erro salvando regear no DB: {e}")
            return

        # 6) Apaga a mensagem original do usuário (só depois que o embed foi postado e salvo)
        try:
            await message.delete()
        except discord.Forbidden:
            print(f"✗ Sem permissão para deletar a mensagem original no canal {channel.id}")
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"✗ Erro deletando mensagem original: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RegearsCog(bot))
