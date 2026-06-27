"""
Salas de voz temporárias.

Modelo simples: o `/setup` define um canal de voz **MÃE** (`voice_temp_mother`).
A mãe é o MODELO — o bot a clona (mesmas permissões/bitrate/limite/categoria) e só
troca o número no nome, mantendo todas as salas na MESMA categoria da mãe.

Regra: sempre manter `FREE_TARGET` salas LIVRES. Os `FIXED_CHANNELS` primeiros
canais (números 1..5) são FIXOS (nunca apagados); o bot cria 6, 7, … conforme
enchem e apaga os excedentes VAZIOS quando sobram livres.

Nomes seguem o padrão da mãe com o número em NEGRITO (𝟏-𝟗):
  🔊  𝐕𝐎𝐈𝐂𝐄 𝟏 (mãe)  ·  🔊  𝐕𝐎𝐈𝐂𝐄 𝟔 (criado pelo bot).
"""
import re
import asyncio

import discord
from discord.ext import commands, tasks

import database
from database import (
    is_server_activated, load_economy_config, get_activated_guild_ids,
)
from utils import bold_number

# Quantos canais são FIXOS (números <= isto nunca são apagados pelo bot).
FIXED_CHANNELS = 5
# Quantas salas LIVRES manter sempre disponíveis.
FREE_TARGET = 3
# Teto de segurança de canais no sistema (evita criação descontrolada).
MAX_CHANNELS = 60
# Teto de criações por ciclo de reconciliação (defesa extra).
MAX_CREATE_PER_CYCLE = 20

# Dígitos em negrito matemático (𝟎-𝟗) p/ ler o número do nome de um canal.
_BOLD_DIGIT_BASE = 0x1D7CE
_TRAILING_NUM_RE = re.compile(r'[0-9\U0001D7CE-\U0001D7D7]+\s*$')


def _digit_val(c: str):
    """Valor de um dígito normal (0-9) ou em negrito (𝟎-𝟗); None se não for dígito."""
    if c.isdigit():
        return int(c)
    o = ord(c)
    if _BOLD_DIGIT_BASE <= o <= _BOLD_DIGIT_BASE + 9:
        return o - _BOLD_DIGIT_BASE
    return None


def _name_prefix(mother_name: str) -> str:
    """Prefixo do nome (tudo menos o número final), preservando o espaço separador.
    Ex.: '🔊  𝐕𝐎𝐈𝐂𝐄 𝟏' → '🔊  𝐕𝐎𝐈𝐂𝐄 '."""
    return _TRAILING_NUM_RE.sub('', mother_name)


def _channel_number(name: str, prefix: str):
    """Número (int) de um canal que casa com `prefix` + dígitos; None se não casar."""
    if not name.startswith(prefix):
        return None
    rem = name[len(prefix):].strip()
    if not rem:
        return None
    val = 0
    for c in rem:
        d = _digit_val(c)
        if d is None:
            return None
        val = val * 10 + d
    return val


def _name_for(prefix: str, n: int) -> str:
    """Nome do canal de número `n` (número em negrito)."""
    return f"{prefix}{bold_number(n)}"


class TempVoiceCog(commands.Cog, name="TempVoiceCog"):
    """Cria/apaga salas de voz pra manter sempre algumas livres, clonando a mãe."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict = {}   # guild_id -> asyncio.Lock (serializa reconcile por guild)

    async def cog_load(self):
        print("✓ TempVoice Cog carregada")
        self.reconcile_loop.start()

    def cog_unload(self):
        self.reconcile_loop.cancel()

    def _lock_for(self, gid: int) -> asyncio.Lock:
        lock = self._locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[gid] = lock
        return lock

    # ------------------------------------------------------------------
    # Listener: alguém entrou/saiu de uma sala → reconcilia
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or member.guild is None:
            return
        if before.channel == after.channel:
            return   # mudou mute/deafen, não trocou de sala
        guild = member.guild
        database.set_current_guild(guild.id)   # multi-tenant
        try:
            if not await is_server_activated(guild.id):
                return
            cfg = await load_economy_config()
            mother_id = cfg.get('voice_temp_mother')
            if not mother_id:
                return
            mother = guild.get_channel(mother_id)
            if not isinstance(mother, discord.VoiceChannel):
                return
            # Só reage se o evento tocou a categoria da mãe.
            cat_id = mother.category_id
            touched = {before.channel.category_id if before.channel else None,
                       after.channel.category_id if after.channel else None}
            if cat_id not in touched:
                return
            await self._reconcile(guild, mother)
        except Exception as e:
            print(f"✗ tempvoice (voice update) [{guild.id}]: {e}")

    # ------------------------------------------------------------------
    # Loop de reconciliação (cobre eventos perdidos / startup)
    # ------------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def reconcile_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    guild = self.bot.get_guild(gid)
                    if not guild:
                        continue
                    cfg = await load_economy_config()
                    mother = guild.get_channel(cfg.get('voice_temp_mother') or 0)
                    if isinstance(mother, discord.VoiceChannel):
                        await self._reconcile(guild, mother)
                except Exception as e:
                    print(f"✗ tempvoice reconcile_loop [{gid}]: {e}")

    @reconcile_loop.before_loop
    async def _before_reconcile(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Núcleo: mantém FREE_TARGET salas livres (cria/apaga clones da mãe)
    # ------------------------------------------------------------------
    async def _reconcile(self, guild: discord.Guild, mother: discord.VoiceChannel):
        async with self._lock_for(guild.id):
            prefix = _name_prefix(mother.name)
            category = mother.category
            pool = category.voice_channels if category else guild.voice_channels

            managed = []
            for ch in pool:
                n = _channel_number(ch.name, prefix)
                if n is not None:
                    managed.append((n, ch))
            managed.sort(key=lambda x: x[0])

            def is_free(ch):
                return not any((not m.bot) for m in ch.members)

            free = [(n, ch) for n, ch in managed if is_free(ch)]

            # 1) Cria salas até ter FREE_TARGET livres.
            created = 0
            while (len(free) < FREE_TARGET and len(managed) < MAX_CHANNELS
                   and created < MAX_CREATE_PER_CYCLE):
                next_num = (managed[-1][0] + 1) if managed else 1
                try:
                    new_ch = await mother.clone(
                        name=_name_for(prefix, next_num),
                        reason="salas temporárias: manter salas livres",
                    )
                except discord.HTTPException as e:
                    print(f"✗ tempvoice: erro clonando a sala mãe ({type(e).__name__}: {e})")
                    break
                # Posiciona logo após o último canal gerenciado (best-effort).
                if managed:
                    try:
                        await new_ch.move(after=managed[-1][1])
                    except discord.HTTPException:
                        pass
                managed.append((next_num, new_ch))
                free.append((next_num, new_ch))
                created += 1

            # 2) Apaga o EXCESSO de salas livres TEMPORÁRIAS (número > FIXED), do
            #    maior número pro menor, até voltar a FREE_TARGET livres.
            #    Re-verifica is_free() no momento da exclusão para evitar apagar uma
            #    sala que alguém acabou de entrar (race condition). Se estiver ocupada,
            #    pula — o on_voice_state_update vai disparar quando ela esvaziar.
            excess = sorted([(n, ch) for n, ch in free if n > FIXED_CHANNELS],
                            key=lambda x: -x[0])
            i = 0
            while len(free) > FREE_TARGET and i < len(excess):
                n, ch = excess[i]
                i += 1
                if not is_free(ch):
                    # Alguém entrou desde a última checagem — não apaga.
                    free = [x for x in free if x[1].id != ch.id]
                    continue
                try:
                    await ch.delete(reason="salas temporárias: excesso de salas livres")
                except discord.HTTPException as e:
                    print(f"✗ tempvoice: erro apagando sala {ch.name} ({type(e).__name__}: {e})")
                    continue
                free = [x for x in free if x[1].id != ch.id]
                managed = [x for x in managed if x[1].id != ch.id]


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoiceCog(bot))
