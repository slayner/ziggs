"""Presença na sala de voz CTA → ParticipationMode.VOICE_PERCENT.

Snapshot loop de 30s: p/ cada guilda, busca eventos IN_PROGRESS com
participation_mode=voice_percent no site, lê `voice_cta_channel_id` da config
de comandos, e posta um snapshot com os membros não-bot presentes em
`/bot/events/{g}/{eid}/voice-snapshot`. O freeze (base_percent/percent) roda no
site quando o evento vai pra DEFINITION (callout) — este cog só acumula.

ponytail: conta todo mundo não-bot presente (sem gate de cargo por enquanto —
gate de roles elegíveis fica como follow-up se pedir, espelhando
SNAPSHOT_ELIGIBLE_ROLE_KEYS do v1).
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs.general import _guild_command_config

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path, tag="voice_presence")


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(path, body, tag="voice_presence")


_cog_ref: "VoicePresence | None" = None


class VoicePresence(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[voice_presence] cog carregada — loop de snapshot de voz ativo")
        if not snapshot_loop.is_running():
            snapshot_loop.start(self)

    async def cog_unload(self) -> None:
        snapshot_loop.cancel()

    async def _snapshot_guild(self, guild: discord.Guild) -> None:
        # Só há trabalho com evento IN_PROGRESS + VOICE_PERCENT — o site filtra
        # (list_voice_active). Sem evento ativo, nada a fazer: a detecção de
        # voz só vale durante o andamento do evento.
        data = await _get(f"/bot/events/{guild.id}/voice-active")
        if not data or not data.get("events"):
            return
        cfg = await _guild_command_config(guild.id)
        ch_id = cfg.get("voice_cta_channel_id")
        if not ch_id:
            print(f"[voice_presence] {guild.id}: voice_cta_channel_id não configurado")
            return
        try:
            cid = int(ch_id)
        except (TypeError, ValueError):
            print(f"[voice_presence] {guild.id}: voice_cta_channel_id inválido: {ch_id!r}")
            return
        # get_channel é só cache; fetch_channel pega canais fora de memória
        # (bot recém-subido, canal criado depois) — sem o fallback, miss de
        # cache = skip silencioso e a detecção nunca roda.
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await guild.fetch_channel(cid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                print(f"[voice_presence] {guild.id}: canal {cid} não encontrado/sem acesso")
                return
        if not isinstance(channel, discord.VoiceChannel):
            print(f"[voice_presence] {guild.id}: canal {cid} não é de voz")
            return
        # trial_role_id vem como string da config (como os outros cogs) —
        # comparar com int(Role.id) exige o cast, senão is_trial é sempre False.
        try:
            trial_rid = int(cfg.get("trial_role_id")) if cfg.get("trial_role_id") else None
        except (TypeError, ValueError):
            trial_rid = None
        # Membros não-bot atualmente na sala — trial role vira is_trial (o
        # backend usa no freeze p/ aplicar o desconto de trial_percent).
        present = [
            {
                "user_id": m.id,
                "user_name": m.display_name,
                "is_trial": bool(trial_rid) and any(r.id == trial_rid for r in m.roles),
            }
            for m in channel.members if not m.bot
        ]
        print(f"[voice_presence] {guild.id} canal {channel.id} ({channel.name}): "
              f"{len(present)} presentes → eventos {[ev['id'] for ev in data['events']]}")
        for ev in data["events"]:
            await _post(f"/bot/events/{guild.id}/{ev['id']}/voice-snapshot",
                        {"present": present})


_tick_n = 0


@tasks.loop(seconds=30)
async def snapshot_loop(cog: "VoicePresence") -> None:
    # ponytail: tick sempre imprime algo (mesmo sem trabalho) — sem isto,
    # silêncio é ambíguo entre "nada pra fazer" e "o loop morreu". Só remover
    # depois de confirmar que o loop sobrevive entre restarts.
    global _tick_n
    _tick_n += 1
    print(f"[voice_presence] tick #{_tick_n}")
    for guild in cog.bot.guilds:
        try:
            await cog._snapshot_guild(guild)
        except Exception as e:
            print(f"[voice_presence] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@snapshot_loop.before_loop
async def _before() -> None:
    # discord.py chama before_loop SEM os args de .start(cog) (só o corpo
    # principal do loop recebe) — declarar `cog` aqui derruba a task com
    # TypeError a CADA .start(), antes do primeiro tick. Era por isto que a
    # detecção de presença NUNCA funcionava (este cog não tem catch-up
    # direto no on_ready do main.py como os outros — depende 100% deste
    # loop). Usa o _cog_ref global (setado em cog_load) em vez de receber
    # como parâmetro.
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@snapshot_loop.error
async def _on_error(error: BaseException) -> None:
    # Confirmado empiricamente: se ISTO roda, o loop MORREU — tasks.loop só
    # chama .error() pra log e deixa a task terminar, nunca reagenda sozinho.
    # Sem handler nenhum (como era antes), essa morte é 100% silenciosa.
    # Loga alto E reinicia — autocura em vez de ficar morto pro resto do
    # processo (mesmo espírito do retry em battle_price_reprocessor.py).
    import traceback
    print(f"[voice_presence] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        # .error() roda ANTES do _loop() interno terminar a task de verdade —
        # chamar .start()/.restart() aqui de forma síncrona corre com esse
        # encerramento. call_soon empurra pro próximo tick do event loop,
        # depois que a task atual já terminou.
        asyncio.get_running_loop().call_soon(lambda: snapshot_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoicePresence(bot))