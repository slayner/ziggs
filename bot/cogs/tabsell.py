"""
Sistema de leilão de tabs (Ponto 5).

Fluxo (modelado no sistema de regears):
1. Qualquer um posta uma imagem (.png/.jpg/.jpeg) no canal `channel_tabsell`.
2. O bot re-uploada a imagem, posta um embed de leilão em estado SETUP e
   deleta a mensagem original.
3. Council/logistic definem, pelos botões do embed:
     · 💰 Valor inicial  (mínimo do 1º lance)
     · 🏁 Valor de arremate  (compra imediata)
   e clicam ▶️ Iniciar leilão.
4. Ao iniciar: todos são pingados (mensagem de ping separada, temporária — some
   sozinha em 5 min) e abre-se a fase de lances por 10 minutos.
5. Qualquer membro dá lances pelo botão 🙋 Dar lance (modal). Bater o valor de
   arremate encerra na hora. Senão, após 10 min o maior lance vence.
6. O embed final mostra o comprador e tem um botão 🔁 Reroll (council/logistic)
   para refazer o leilão com os mesmos valores.
"""
import os
import io
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv

import database
from database import (
    is_server_activated,
    load_economy_config,
    create_auction, get_auction_by_message_id, get_auction_by_id,
    update_auction, add_bid, get_highest_bid, get_bids_for_auction,
    get_bidding_auctions, get_open_auctions, clear_bids,
)
from cogs.economy import has_configured_role
from utils import (parse_silver, format_silver,
                   EMBED_INFO, EMBED_OK, EMBED_WARN, EMBED_ERR, send_err, send_ok)

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

ALLOWED_IMAGE_EXTS = ('.png', '.jpg', '.jpeg')
AUCTION_DURATION = timedelta(minutes=10)   # 10 minutos de lances
EXPIRY_CHECK_INTERVAL = 20                  # checa expirados a cada 20s
TEMP_MSG_SECONDS = 300                      # @everyone e msg de vencedor somem após 5 min


def _is_allowed_image(att: discord.Attachment) -> bool:
    name = (att.filename or '').lower()
    return name.endswith(ALLOWED_IMAGE_EXTS)


def _iso_unix(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _is_expired(iso_str) -> bool:
    ts = _iso_unix(iso_str)
    if ts is None:
        return False
    return datetime.now(timezone.utc).timestamp() >= ts


async def _can_manage(member: discord.Member) -> bool:
    """Council/logistic (ou owner) podem gerenciar o leilão."""
    if member is None:
        return False
    if member.id == OWNER_ID:
        return True
    return await has_configured_role(member, 'role_council', 'role_logistic')


# ==================================================================
# Embeds
# ==================================================================
def _build_embed(auction: dict, highest: dict | None = None) -> discord.Embed:
    status = auction.get('status')
    iv = auction.get('initial_value')
    bo = auction.get('buyout_value')

    if status == 'setup':
        embed = discord.Embed(
            title="🪙  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄",
            description=f"Postado por <@{auction['poster_id']}>",
            color=EMBED_INFO,
        )
        embed.add_field(
            name="💰 Bid Inicial",
            value=f"{format_silver(iv)}" if iv else "*não definido*",
            inline=True,
        )
        embed.add_field(
            name="🏁 Valor de Arremate",
            value=f"{format_silver(bo)}" if bo else "*não definido*",
            inline=True,
        )
        embed.set_footer(text="💰 Bid Inicial  ·  🏁 Valor de Arremate  ·  ▶️ Iniciar leilão  ·  🗑️ Cancelar Leilão")

    elif status == 'bidding':
        end_unix = _iso_unix(auction.get('ends_at'))
        if highest:
            lance_str = (
                f"{format_silver(highest['amount'])} — <@{highest['user_id']}>"
            )
        else:
            lance_str = "*nenhum lance ainda*"
        embed = discord.Embed(
            title="🔨  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄",
            description=f"Postado por <@{auction['poster_id']}>",
            color=EMBED_WARN,
        )
        embed.add_field(name="💰 Bid Inicial", value=f"{format_silver(iv)}", inline=True)
        embed.add_field(name="🏁 Valor de Arremate", value=f"{format_silver(bo)}", inline=True)
        embed.add_field(name="🥇 Maior lance", value=lance_str, inline=False)
        if end_unix:
            embed.add_field(name="⏳ Encerra", value=f"<t:{end_unix}:R> (<t:{end_unix}:t>)", inline=False)
            embed.set_footer(text="🛑 Cancelar Leilão")

    elif status == 'finished':
        if auction.get('winner_id'):
            desc = (
                f"**Comprador:** <@{auction['winner_id']}>\n"
                f"**Lance vencedor:** `{format_silver(auction.get('winning_bid'))}`"
            )
            color = EMBED_OK
            title = "✅  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄"
            set_footer = "🔁 Reroll"
        else:
            desc = "*Nenhum lance foi feito — leilão encerrado sem comprador.*"
            color = EMBED_INFO
            title = "🚫  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄"
        embed = discord.Embed(
            title=title, description=desc, color=color,
        )
        embed.add_field(name="💰 Bid Inicial", value=f"{format_silver(iv)}" if iv else "—", inline=True)
        embed.add_field(name="🏁 Valor de Arremate", value=f"{format_silver(bo)}" if bo else "—", inline=True)
        embed.set_footer(text=set_footer)
    else:  # cancelled
        embed = discord.Embed(
            title="🗑️  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄",
            description=f"Leilão cancelado. Postado por <@{auction['poster_id']}>.",
            color=EMBED_ERR,
        )

    if auction.get('image_url'):
        url = auction['image_url']
        # Referencia o anexo da PRÓPRIA mensagem (attachment://) em vez da URL de
        # CDN: a URL de CDN do Discord expira e a imagem some quando o embed é
        # reeditado (ex.: ao iniciar o leilão). O anexo é mantido nas edições.
        fname = url.split('?', 1)[0].rsplit('/', 1)[-1]
        if fname and ('discordapp.com' in url or 'discordapp.net' in url):
            embed.set_image(url=f"attachment://{fname}")
        else:
            embed.set_image(url=url)
    return embed


def _view_for(auction: dict) -> ui.View | None:
    status = auction.get('status')
    if status == 'setup':
        return AuctionSetupView()
    if status == 'bidding':
        return AuctionBiddingView()
    if status == 'finished':
        return AuctionFinishedView()
    return None


# ==================================================================
# Modais
# ==================================================================
class _ValueModal(ui.Modal):
    value_input = ui.TextInput(
        label="Valor (prata)",
        placeholder="Ex: 1,000,000  ou  1m  ou  1.000.000",
        max_length=30,
    )

    def __init__(self, title: str, field: str, auction_id: int):
        super().__init__(title=title)
        self.field = field  # 'initial_value' ou 'buyout_value'
        self.auction_id = auction_id

    async def on_submit(self, interaction: discord.Interaction):
        parsed = parse_silver(self.value_input.value)
        if parsed is None or parsed <= 0:
            await send_err(interaction, "Valor inválido.")
            return

        auction = await get_auction_by_id(self.auction_id)
        if not auction or auction['status'] != 'setup':
            await send_err(interaction, "Este leilão não está mais na fase de configuração.")
            return

        # Validação cruzada inicial < arremate
        other = auction['buyout_value'] if self.field == 'initial_value' else auction['initial_value']
        if other:
            if self.field == 'initial_value' and parsed >= other:
                await send_err(interaction, f"O valor inicial deve ser MENOR que o arremate "
                                            f"(`{format_silver(other)}`).")
                return
            if self.field == 'buyout_value' and parsed <= other:
                await send_err(interaction, f"O valor de arremate deve ser MAIOR que o inicial "
                                            f"(`{format_silver(other)}`).")
                return

        await update_auction(auction['id'], {self.field: parsed})
        auction = await get_auction_by_id(auction['id'])
        # edit_message a partir de um modal aberto por botão edita a msg do botão
        try:
            await interaction.response.edit_message(embed=_build_embed(auction), view=AuctionSetupView())
        except Exception as e:
            print(f"✗ Erro editando embed de setup do leilão: {e}")
            try:
                await send_ok(interaction, "Valor salvo.")
            except discord.HTTPException:
                pass  # interação já respondida (esperado)


class BidModal(ui.Modal, title="Dar lance no leilão"):
    amount_input = ui.TextInput(
        label="Seu lance (prata)",
        placeholder="Ex: 1,500,000  ou  1.5m",
        max_length=30,
    )

    def __init__(self, auction_id: int):
        super().__init__()
        self.auction_id = auction_id

    async def on_submit(self, interaction: discord.Interaction):
        amount = parse_silver(self.amount_input.value)
        if amount is None or amount <= 0:
            await send_err(interaction, "Lance inválido.")
            return

        auction = await get_auction_by_id(self.auction_id)
        if not auction or auction['status'] != 'bidding':
            await send_err(interaction, "Este leilão não está aceitando lances.")
            return
        if _is_expired(auction.get('ends_at')):
            await send_err(interaction, "O tempo do leilão já acabou.")
            return

        highest = await get_highest_bid(auction['id'])
        if highest:
            if amount <= highest['amount']:
                await send_err(interaction, f"Seu lance precisa ser MAIOR que o atual "
                                            f"({format_silver(highest['amount'])}).")
                return
        else:
            iv = auction.get('initial_value') or 0
            if amount < iv:
                await send_err(interaction, f"O primeiro lance precisa ser pelo menos o valor "
                                            f"inicial ({format_silver(iv)}).")
                return

        await add_bid(auction['id'], interaction.user.id, interaction.user.display_name, amount)

        bo = auction.get('buyout_value')
        cog = interaction.client.cogs.get('TabSellCog')

        # Bateu o arremate → encerra na hora
        if bo and amount >= bo:
            if cog:
                await cog.finalize_auction(auction['id'], reason='buyout')
            return

        await send_ok(interaction, f"Lance de `{format_silver(amount)}` registrado!")
        if cog:
            await cog.refresh_auction(auction['id'])


# ==================================================================
# Views persistentes
# ==================================================================
class AuctionSetupView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💰", style=discord.ButtonStyle.primary, custom_id="tab:set_initial")
    async def set_initial(self, interaction: discord.Interaction, _b: ui.Button):
        if not await self._guard(interaction):
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'setup':
            await send_err(interaction, "Leilão não está em configuração.")
            return
        await interaction.response.send_modal(
            _ValueModal("Definir valor inicial", "initial_value", auction['id'])
        )

    @ui.button(label="🏁", style=discord.ButtonStyle.primary, custom_id="tab:set_buyout")
    async def set_buyout(self, interaction: discord.Interaction, _b: ui.Button):
        if not await self._guard(interaction):
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'setup':
            await send_err(interaction, "Leilão não está em configuração.")
            return
        await interaction.response.send_modal(
            _ValueModal("Definir valor de arremate", "buyout_value", auction['id'])
        )

    @ui.button(label="▶️", style=discord.ButtonStyle.success, custom_id="tab:start")
    async def start(self, interaction: discord.Interaction, _b: ui.Button):
        if not await self._guard(interaction):
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'setup':
            await send_err(interaction, "Leilão não está em configuração.")
            return
        if not auction.get('initial_value') or not auction.get('buyout_value'):
            await send_err(interaction, "Defina o **valor inicial** E o **valor de arremate** "
                                        "antes de iniciar.")
            return
        cog = interaction.client.cogs.get('TabSellCog')
        if not cog:
            await send_err(interaction, "Cog indisponível.")
            return
        await interaction.response.defer()
        await cog.start_bidding(auction, interaction.user.id)

    @ui.button(label="🗑️", style=discord.ButtonStyle.danger, custom_id="tab:cancel")
    async def cancel(self, interaction: discord.Interaction, _b: ui.Button):
        if not await self._guard(interaction):
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'setup':
            await send_err(interaction, "Leilão não está em configuração.")
            return
        await update_auction(auction['id'], {'status': 'cancelled'})
        auction = await get_auction_by_id(auction['id'])
        await interaction.response.edit_message(embed=_build_embed(auction), view=None)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not await _can_manage(interaction.user):
            await send_err(interaction, "Apenas council ou logistic podem configurar o leilão.")
            return False
        return True


class AuctionBiddingView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🙋 Bid", style=discord.ButtonStyle.success, custom_id="tab:bid")
    async def bid(self, interaction: discord.Interaction, _b: ui.Button):
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'bidding':
            await send_err(interaction, "Este leilão não está aceitando lances.")
            return
        await interaction.response.send_modal(BidModal(auction['id']))

    @ui.button(label="🛑", style=discord.ButtonStyle.danger, custom_id="tab:endnow")
    async def end_now(self, interaction: discord.Interaction, _b: ui.Button):
        if not await _can_manage(interaction.user):
            await send_err(interaction, "Apenas council ou logistic podem encerrar.")
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'bidding':
            await send_err(interaction, "Leilão não está em andamento.")
            return
        await interaction.response.defer()
        cog = interaction.client.cogs.get('TabSellCog')
        if cog:
            await cog.finalize_auction(auction['id'], reason='manual')


class AuctionFinishedView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔁", style=discord.ButtonStyle.secondary, custom_id="tab:reroll")
    async def reroll(self, interaction: discord.Interaction, _b: ui.Button):
        if not await _can_manage(interaction.user):
            await send_err(interaction, "Apenas council ou logistic podem refazer o leilão.")
            return
        auction = await get_auction_by_message_id(interaction.message.id)
        if not auction or auction['status'] != 'finished':
            await send_err(interaction, "Leilão não pode ser refeito.")
            return
        cog = interaction.client.cogs.get('TabSellCog')
        if not cog:
            await send_err(interaction, "Cog indisponível.")
            return
        await interaction.response.defer()
        await cog.reroll_auction(auction, interaction.user.id)


# ==================================================================
# Cog principal
# ==================================================================
class TabSellCog(commands.Cog, name="TabSellCog"):
    """Leilão de tabs no canal channel_tabsell."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pending_scanned = False

    async def cog_load(self):
        self.bot.add_view(AuctionSetupView())
        self.bot.add_view(AuctionBiddingView())
        self.bot.add_view(AuctionFinishedView())
        print("✓ TabSell Cog carregada")
        self.bot.loop.create_task(self._scan_pending_when_ready())
        if not self.expiry_loop.is_running():
            self.expiry_loop.start()

    async def cog_unload(self):
        if self.expiry_loop.is_running():
            self.expiry_loop.cancel()

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            return False
        return True

    # ------------------------------------------------------------------
    # Loop de expiração: encerra leilões cujos 10 min acabaram
    # ------------------------------------------------------------------
    @tasks.loop(seconds=EXPIRY_CHECK_INTERVAL)
    async def expiry_loop(self):
        for gid in await database.get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._expiry_once()
                except Exception as e:
                    print(f"✗ expiry_loop [{gid}]: {e}")

    async def _expiry_once(self):
        try:
            auctions = await get_bidding_auctions()
        except Exception as e:
            print(f"✗ expiry_loop: erro buscando leilões: {e}")
            return
        for a in auctions:
            if _is_expired(a.get('ends_at')):
                try:
                    await self.finalize_auction(a['id'], reason='timeout')
                except Exception as e:
                    print(f"✗ expiry_loop: erro finalizando leilão #{a.get('id')}: {e}")

    @expiry_loop.before_loop
    async def _before_expiry(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Helpers de canal/mensagem
    # ------------------------------------------------------------------
    def _get_channel(self, chan_id: int):
        if not chan_id:
            return None
        return self.bot.get_channel(chan_id)

    async def _fetch_message(self, auction: dict):
        channel = self._get_channel(auction['channel_id'])
        if not channel:
            return None, None
        try:
            return channel, await channel.fetch_message(auction['message_id'])
        except discord.NotFound:
            return channel, None
        except Exception as e:
            print(f"✗ Erro buscando msg do leilão #{auction.get('id')}: {e}")
            return channel, None

    async def refresh_auction(self, auction_id: int):
        """Reconstrói embed+view do leilão a partir do estado atual no DB."""
        auction = await get_auction_by_id(auction_id)
        if not auction:
            return
        _, msg = await self._fetch_message(auction)
        if not msg:
            return
        highest = await get_highest_bid(auction_id) if auction['status'] == 'bidding' else None
        try:
            await msg.edit(embed=_build_embed(auction, highest), view=_view_for(auction))
        except Exception as e:
            print(f"✗ Erro refrescando leilão #{auction_id}: {e}")

    # ------------------------------------------------------------------
    # Iniciar lances (a partir do botão Iniciar ou do reroll)
    # ------------------------------------------------------------------
    async def start_bidding(self, auction: dict, started_by: int):
        ends_at = datetime.now(timezone.utc) + AUCTION_DURATION
        await update_auction(auction['id'], {
            'status': 'bidding',
            'ends_at': ends_at.isoformat(),
            'started_by': started_by,
        })
        auction = await get_auction_by_id(auction['id'])

        channel, msg = await self._fetch_message(auction)
        if channel is None:
            return

        embed = _build_embed(auction, None)

        # @everyone ACIMA do embed, na MESMA mensagem. REENVIAMOS (em vez de editar)
        # porque editar pra adicionar @everyone NÃO dispara o ping — só o envio dispara.
        # A imagem é reaproveitada do anexo da mensagem de setup.
        img_file = None
        if msg and msg.attachments:
            try:
                img_file = await msg.attachments[0].to_file()
            except Exception as e:
                print(f"✗ Erro reaproveitando imagem do leilão #{auction['id']}: {e}")

        new_msg = None
        try:
            kwargs = dict(
                content="@everyone",
                embed=embed,
                view=AuctionBiddingView(),
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
            if img_file:
                kwargs['file'] = img_file
            new_msg = await channel.send(**kwargs)
        except Exception as e:
            print(f"✗ Erro reenviando leilão p/ bidding (#{auction['id']}): {e}")

        if new_msg:
            updates = {'message_id': new_msg.id, 'ping_message_id': None}
            if new_msg.attachments:
                updates['image_url'] = new_msg.attachments[0].url
            await update_auction(auction['id'], updates)
            if msg:                        # apaga a mensagem antiga (setup), já reenviada
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass
        elif msg:                          # fallback: não reenviou → edita no lugar (sem ping)
            try:
                await msg.edit(embed=embed, view=AuctionBiddingView())
            except Exception as e:
                print(f"✗ Erro editando leilão p/ bidding (fallback) (#{auction['id']}): {e}")

    async def _delete_ping(self, auction: dict):
        ping_id = auction.get('ping_message_id')
        if not ping_id:
            return
        channel = self._get_channel(auction['channel_id'])
        if not channel:
            return
        try:
            ping_msg = await channel.fetch_message(ping_id)
            await ping_msg.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"✗ Erro deletando ping do leilão #{auction.get('id')}: {e}")
        await update_auction(auction['id'], {'ping_message_id': None})

    # ------------------------------------------------------------------
    # Finalizar leilão (timeout / buyout / manual)
    # ------------------------------------------------------------------
    async def finalize_auction(self, auction_id: int, reason: str = 'timeout'):
        auction = await get_auction_by_id(auction_id)
        if not auction or auction['status'] != 'bidding':
            return  # já finalizado / cancelado

        highest = await get_highest_bid(auction_id)
        bo = auction.get('buyout_value')
        updates = {'status': 'finished'}
        win_value = None
        if highest:
            # Se o lance passou do arremate, o que VALE é o arremate (não o lance inflado).
            win_value = highest['amount']
            if bo and win_value > bo:
                win_value = bo
            updates.update({
                'winner_id': highest['user_id'],
                'winner_name': highest['user_name'],
                'winning_bid': win_value,
            })
        await update_auction(auction_id, updates)

        # Deleta o ping de "todos"
        await self._delete_ping(auction)

        auction = await get_auction_by_id(auction_id)
        channel, msg = await self._fetch_message(auction)
        if msg:
            try:
                await msg.edit(content=None, embed=_build_embed(auction, highest), view=AuctionFinishedView())
            except Exception as e:
                print(f"✗ Erro editando leilão finalizado: {e}")

        # Anuncia o vencedor (temporária: some sozinho em 5 min)
        if channel and highest:
            tag = {'buyout': "por arremate", 'manual': "(encerrado pela staff)", 'timeout': ""}.get(reason, "")
            try:
                await channel.send(
                    f"🏆 <@{highest['user_id']}> arrematou o tab por "
                    f"{format_silver(win_value)} {tag}".strip() + "!",
                    allowed_mentions=discord.AllowedMentions(users=True),
                    delete_after=TEMP_MSG_SECONDS,   # some sozinho em 5 min
                )
            except Exception as e:
                print(f"✗ Erro anunciando vencedor: {e}")

    # ------------------------------------------------------------------
    # Reroll: refaz o leilão com os mesmos valores
    # ------------------------------------------------------------------
    async def reroll_auction(self, auction: dict, started_by: int):
        # Mantém initial_value/buyout_value; só limpa lances/vencedor e recomeça.
        await clear_bids(auction['id'])
        await self._delete_ping(auction)
        await update_auction(auction['id'], {
            'ends_at': None,
            'winner_id': None,
            'winner_name': None,
            'winning_bid': None,
        })
        auction = await get_auction_by_id(auction['id'])
        await self.start_bidding(auction, started_by)

    # ------------------------------------------------------------------
    # Scan inicial: imagens postadas enquanto o bot estava offline
    # ------------------------------------------------------------------
    async def _scan_pending_when_ready(self):
        await self.bot.wait_until_ready()
        if self._pending_scanned:
            return
        self._pending_scanned = True
        for gid in await database.get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._scan_pending_on_startup()
                except Exception as e:
                    print(f"✗ Erro no scan inicial de leilões [{gid}]: {e}")

    async def _scan_pending_on_startup(self):
        cfg     = await load_economy_config()
        chan_id = cfg.get('channel_tabsell')
        channel = self._get_channel(chan_id)
        if not channel:
            return

        # message_ids de leilões já criados (não reprocessar embeds do bot)
        try:
            known = {a['message_id'] for a in await get_open_auctions()}
            known |= {a['ping_message_id'] for a in await get_open_auctions() if a.get('ping_message_id')}
        except Exception:
            known = set()

        recent = []
        try:
            async for msg in channel.history(limit=100):
                if msg.author.bot or not msg.attachments:
                    continue
                if msg.id in known:
                    continue
                recent.append(msg)
        except discord.Forbidden:
            print(f"✗ Sem permissão de ler histórico de {channel}")
            return
        except Exception as e:
            print(f"✗ Erro scaneando tabsell: {e}")
            return
        recent.reverse()

        for msg in recent:
            try:
                await self._process_tabsell_message(msg)
            except Exception as e:
                print(f"✗ Erro processando msg pendente de leilão {msg.id}: {e}")

    # ------------------------------------------------------------------
    # Listener: nova imagem no canal de tabsell
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._process_tabsell_message(message)

    async def _process_tabsell_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.attachments:
            return
        database.set_current_guild(message.guild.id)   # multi-tenant: banco do servidor

        cfg     = await load_economy_config()
        chan_id = cfg.get('channel_tabsell')
        if not chan_id or message.channel.id != chan_id:
            return
        channel = message.channel

        # Bloqueia múltiplos attaches
        if len(message.attachments) > 1:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            try:
                await channel.send(
                    f"{message.author.mention} ⚠️ Envie **apenas 1 imagem por vez** "
                    "no canal de leilões. Sua mensagem foi removida.",
                    delete_after=10,
                )
            except discord.HTTPException:
                pass  # sem permissão / canal indisponível (esperado)
            return

        att = message.attachments[0]
        if not _is_allowed_image(att):
            return

        now = datetime.now(timezone.utc)
        try:
            file_bytes = await att.read()
        except Exception as e:
            print(f"✗ Erro lendo attachment do leilão {att.filename}: {e}")
            return

        safe_name = att.filename or f"tab_{now.timestamp():.0f}.png"
        safe_name = "".join(c if c.isalnum() or c in ('.', '-', '_') else '_' for c in safe_name)
        file_obj  = discord.File(io.BytesIO(file_bytes), filename=safe_name)

        embed = discord.Embed(
            title="🪙  𝐀𝐔𝐂𝐓𝐈𝐎𝐍 𝐇𝐎𝐔𝐒𝐄",
            description=(
                f"**Leilão criado por:** {message.author.mention}\n\n"),
            color=EMBED_INFO,
        )
        embed.add_field(name="💰 Bid Inicial", value="*não definido*", inline=True)
        embed.add_field(name="🏁 Valor de Arremate", value="*não definido*", inline=True)
        embed.set_image(url=f"attachment://{safe_name}")
        embed.set_footer(text="💰 Bid Inicial  ·  🏁 Valor de Arremate  ·  ▶️ Iniciar leilão  ·  🗑️ Cancelar Leilão")

        try:
            bot_msg = await channel.send(embed=embed, file=file_obj, view=AuctionSetupView())
        except discord.Forbidden:
            print(f"✗ Sem permissão para postar leilão no canal {channel.id}")
            return
        except Exception as e:
            print(f"✗ Erro criando embed de leilão: {e}")
            return

        bot_image_url = bot_msg.attachments[0].url if bot_msg.attachments else att.url
        try:
            await create_auction(
                guild_id    = message.guild.id,
                channel_id  = channel.id,
                message_id  = bot_msg.id,
                poster_id   = message.author.id,
                poster_name = message.author.display_name,
                image_url   = bot_image_url,
            )
        except Exception as e:
            print(f"✗ Erro salvando leilão no DB: {e}")
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        except Exception as e:
            print(f"✗ Erro deletando mensagem original do leilão: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(TabSellCog(bot))
