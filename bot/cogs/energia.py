"""
Energia da guilda — saldo de energia por jogador.

A guilda pede um depósito (ex.: 500) pra liberar o acesso; o player vai sacando
energia conforme precisa. A staff cola a LOG do jogo (texto copiado, NÃO csv) num
modal e o bot aplica os lançamentos no saldo de energia de cada um (por usuário do
Discord, casando o nick do jogo via /register).

Formato da log (campos entre aspas, tab-separados; horários em UTC):
    "Date"  "Player"  "Reason"  "Amount"
    "2026-06-01 01:04:26"  "Andzada"  "Deposit"  "6"
    "2026-06-01 00:29:55"  "S1GNE"    "Withdrawal"  "-10"

`Amount` já vem com sinal. Deduplica por (data, player, amount), então colar logs
sobrepostas não conta em dobro. Quando o saldo de alguém CRUZA pra baixo do limite
(energy_alert_threshold), posta um alerta no canal dedicado pingando o player.

Comandos de controle: council / lead / OWNER (via has_configured_role).
"""
import os
import re
from collections import defaultdict

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from database import (
    is_server_activated, load_economy_config, update_economy_config,
    get_registration_by_nick,
    get_user_energy, add_energy, set_energy, record_energy_entry,
    add_energy_whitelist, remove_energy_whitelist, get_energy_whitelist,
)
from cogs.economy import has_configured_role
from utils import format_silver, send_err, send_ok, send_warn, send_info
import utils

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

DEFAULT_ENERGY_THRESHOLD = 50
_QUOTED = re.compile(r'"([^"]*)"')


def _parse_energy_log(text: str):
    """
    Parseia a log colada (campos entre aspas). Robusto a tab OU espaços, desde que
    os campos venham entre aspas. Retorna lista de (ts, player, reason, amount:int).
    Pula o cabeçalho e linhas inválidas.
    """
    entries = []
    for line in (text or "").splitlines():
        fields = _QUOTED.findall(line)
        if len(fields) < 4:
            continue
        ts, player, reason, amount = (f.strip() for f in fields[:4])
        if ts.lower() == 'date' or player.lower() == 'player':
            continue  # cabeçalho
        if not player:
            continue
        try:
            amt = int(amount.replace(',', ''))
        except ValueError:
            continue
        entries.append((ts, player, reason, amt))
    return entries


class EnergiaCog(commands.Cog, name="EnergiaCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        print("✓ Energia Cog carregada")

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    async def _is_control(self, member) -> bool:
        # has_configured_role já inclui OWNER e o cargo lead.
        return await has_configured_role(member, 'role_council')

    # ==================================================================
    # Núcleo: aplicar a log
    # ==================================================================
    async def process_energy_log(self, text: str) -> str:
        entries = _parse_energy_log(text)
        if not entries:
            return ("⚠️ Nenhum lançamento válido encontrado. Cole a log **com as aspas**, "
                    "no formato do jogo (Date / Player / Reason / Amount).")

        cfg = await load_economy_config()
        threshold = cfg.get('energy_alert_threshold')
        if threshold is None:
            threshold = DEFAULT_ENERGY_THRESHOLD

        # Whitelist: quem cuida da energia da guilda é ignorado por completo.
        whitelist = set(await get_energy_whitelist())

        applied = dups = ignored_wl = 0
        unregistered: dict[str, int] = {}
        new_deltas: dict[int, int] = defaultdict(int)
        nick_to_uid: dict[str, int | None] = {}

        for ts, player, reason, amt in entries:
            if player not in nick_to_uid:
                reg = await get_registration_by_nick(player)
                nick_to_uid[player] = reg['user_id'] if reg else None
            uid = nick_to_uid[player]
            if uid is None:
                unregistered[player] = unregistered.get(player, 0) + 1
                continue
            if uid in whitelist:
                ignored_wl += 1   # ignorado de propósito (não registra, não aplica)
                continue
            if await record_energy_entry(ts, player, reason, amt, uid):
                applied += 1
                new_deltas[uid] += amt
            else:
                dups += 1

        # Aplica os deltas e detecta quem CRUZOU pra baixo do limite.
        alerts = []
        for uid, delta in new_deltas.items():
            before = await get_user_energy(uid)
            after = await add_energy(uid, delta)
            if before > threshold >= after:
                alerts.append((uid, after))

        for uid, bal in alerts:
            await self._alert_low_energy(cfg, uid, bal, threshold)

        out = [f"✅ Log processada: **{applied}** lançamento(s) aplicado(s), "
               f"**{dups}** duplicado(s) ignorado(s)."]
        if ignored_wl:
            out.append(f"⭐ **{ignored_wl}** lançamento(s) ignorado(s) (whitelist de energia).")
        if alerts:
            out.append(f"⚡ **{len(alerts)}** alerta(s) de energia baixa enviado(s).")
        if unregistered:
            shown = ", ".join(f"`{n}`×{c}" for n, c in list(unregistered.items())[:20])
            extra = len(unregistered) - 20
            out.append(f"⚠️ Sem cadastro (ignorados — use `/register`): {shown}"
                       + (f" … +{extra}" if extra > 0 else ""))
        return "\n".join(out)

    async def _alert_low_energy(self, cfg: dict, uid: int, balance: int, threshold: int):
        chan_id = cfg.get('channel_energyalerts')
        if not chan_id:
            return
        ch = self.bot.get_channel(chan_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(chan_id)
            except discord.HTTPException:
                return
        try:
            await ch.send(
                f"⚡ <@{uid}> está com **energia baixa**!\nSaldo de Energia: {balance}\n"
                f"Deposite energia na guilda pra não ir de Ganley!!!",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException as e:
            print(f"✗ Energia: erro postando alerta de {uid}: {e}")

    # ==================================================================
    # /energylog  — cole o texto OU anexe o .txt (logs grandes viram .txt)
    # ==================================================================
    @commands.hybrid_command(
        name="energylog",
        description="Importa a log de energia: cole o texto OU anexe o .txt — council/lead/owner",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        arquivo="Arquivo da log (.txt) — use quando o texto é grande (Discord vira .txt sozinho)",
        texto="Cole a log aqui (para logs curtas)",
    )
    async def energylog(self, ctx: commands.Context,
                        arquivo: discord.Attachment = None, *, texto: str = None):
        if not await self._is_control(ctx.author):
            await send_err(ctx, "Apenas council, lead ou owner.")
            return
        await ctx.defer(ephemeral=True)

        # Fonte do log: anexo (slash arg OU anexo da mensagem no prefixo) ou texto.
        att = arquivo
        if att is None and getattr(ctx, 'message', None) and ctx.message.attachments:
            att = ctx.message.attachments[0]

        log_text = None
        if att is not None:
            try:
                data = await att.read()
                log_text = data.decode('utf-8-sig', errors='replace')
            except Exception as e:
                await send_err(ctx, f"Não consegui ler o anexo: {e}")
                return
        elif texto:
            log_text = texto

        if not log_text or not log_text.strip():
            await send_err(ctx, "Mande a log: **cole no campo `texto`** (logs curtas) ou "
                                "**anexe o `.txt`** (quando você cola algo grande, o Discord "
                                "transforma em `.txt` — é só anexar).")
            return

        report = await self.process_energy_log(log_text)
        await ctx.send(report, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ==================================================================
    # /energy  — mostra o saldo de energia (próprio; staff vê de outros)
    # ==================================================================
    @commands.hybrid_command(name="energy", description="Mostra o saldo de energia (seu, ou de alguém se for staff).")
    @app_commands.guild_only()
    @app_commands.describe(user="Usuário (em branco = você)")
    async def energy(self, ctx: commands.Context, user: discord.Member = None):
        target = user or ctx.author
        if target.id != ctx.author.id and not await self._is_control(ctx.author):
            await send_err(ctx, "Só staff pode ver a energia de outros.")
            return
        bal = await get_user_energy(target.id)
        await ctx.send(
            f"Energia de {target.mention}: {format_silver(bal)}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ==================================================================
    # Controle (council/lead/owner)
    # ==================================================================
    @commands.hybrid_command(name="setenergy", description="Define manualmente a energia de um usuário — council/lead/owner")
    @app_commands.guild_only()
    @app_commands.describe(user="Usuário", valor="Novo saldo de energia (absoluto)")
    async def setenergy(self, ctx: commands.Context, user: discord.Member, valor: int):
        if not await self._is_control(ctx.author):
            await send_err(ctx, "Apenas council, lead ou owner.")
            return
        await set_energy(user.id, valor)
        await send_ok(ctx, f"Energia de {user.mention} definida em **{valor}**.")

    @commands.hybrid_command(
        name="energywl",
        description="Liga/desliga um usuário na whitelist de energia (ignorado nas logs) — owner/council/logistic/lead",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Usuário que cuida da energia (será ignorado no processamento das logs)")
    async def energywl(self, ctx: commands.Context, user: discord.Member):
        # OWNER/council/logistic/lead (has_configured_role já libera OWNER + lead).
        if not await has_configured_role(ctx.author, 'role_council', 'role_logistic'):
            await send_err(ctx, "Apenas owner, council, logistic ou lead.")
            return
        if await add_energy_whitelist(user.id, ctx.author.id):
            msg = (f"⭐ {user.mention} adicionado(a) à **whitelist de energia**.\n"
                   f"A partir de agora os lançamentos dele(a) são **ignorados** nas logs de energia.")
        else:
            await remove_energy_whitelist(user.id)
            msg = (f"➖ {user.mention} **removido(a)** da whitelist de energia.\n"
                   f"Os lançamentos dele(a) voltam a contar normalmente nas logs.")
        await ctx.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(EnergiaCog(bot))
