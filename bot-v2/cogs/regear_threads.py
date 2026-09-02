"""Threads de regear por evento (outbox).

Quando um evento entra em IN_PROGRESS o site marca `regear_thread_dirty`; este
loop puxa `/bot/events/{g}/regear-thread-work`, cria uma thread pública no canal
dedicado `regear_thread_channel_id` (header localizado) e chama
`/regear-thread-synced` gravando `Event.regear_thread_id`. Prints postadas na
thread viram RegearRequests atrelados ao evento (ver cogs/regears.py).

Eventos em estado terminal (CANCELLED/DELETED/FINALIZED) com thread ativa são
arquivados (lock) — best-effort; falha só reintenta no próximo tick."""
from __future__ import annotations

import asyncio
import os

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout
from cogs.general import _guild_command_config, clear_unavailable_channel, guild_lang_for
from i18n import t

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _get(path: str) -> dict | None:
    return await http_client.get_json(path, tag="regear_threads")


async def _post(path: str, body: dict) -> dict | None:
    return await http_client.post_json(
        path, body, tag="regear_threads", attempts=2,
    )


_cog_ref: "RegearThreads | None" = None


class RegearThreads(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._thread_ids: dict[tuple[int, int], int] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[regear_threads] cog carregada — loop de criação de threads ativo")
        if not regear_thread_work_loop.is_running():
            regear_thread_work_loop.start(self)

    async def cog_unload(self) -> None:
        regear_thread_work_loop.cancel()

    # Backlog scan no restart: sem listener de on_ready próprio de propósito —
    # um on_ready de cog dispara na hora que o gateway conecta, ANTES do
    # backend estar necessariamente de pé (start-all.cmd sobe os dois em
    # paralelo, bot costuma ganhar a corrida). Rodar sync_guild nesse momento
    # sempre pegava _guild_command_config em erro de rede → config vazia →
    # "regear_thread_channel_id não veio" e nada era criado, silenciosamente,
    # sem nenhum benefício sobre esperar o loop de 10s. main.py chama
    # sync_guild explicitamente DEPOIS de _wait_for_backend() (mesmo padrão
    # já usado pra EventEmbeds/BotAuditLog) — ver on_ready em main.py.

    async def sync_guild(self, guild: discord.Guild) -> None:
        lock = self._guild_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._sync_guild_unlocked(guild)

    async def _sync_guild_unlocked(self, guild: discord.Guild) -> None:
        cfg = await _guild_command_config(guild.id)
        channel_id = cfg.get("regear_thread_channel_id")
        if not channel_id:
            return  # feature off (sem canal) ou backend fora — não logar por-tick
                    # (queda do backend já é logada 1× por http_client)
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            return
        # get_channel é só cache; fetch_channel pega canais que não estão em
        # memória (canal criado pós-start sem o evento on_guild_channel_create
        # chegar, ou cache evictido). Sem o fetch, miss de cache = skip
        # silencioso e a thread nunca abre.
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await dtimeout(guild.fetch_channel(cid))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError) as e:
                print(f"[regear_threads] canal {cid} inacessível em {guild.id}: "
                      f"{type(e).__name__}: {e}")
                if isinstance(e, discord.NotFound):
                    await clear_unavailable_channel(guild.id, "regear_thread_channel_id", cid)
                return
        if not isinstance(channel, discord.TextChannel):
            print(f"[regear_threads] canal {cid} não é de texto em {guild.id}")
            return
        lang = await guild_lang_for(guild.id)

        work = await _get(f"/bot/events/{guild.id}/regear-thread-work")
        if work is None:
            return  # backend fora/401 já logado 1× (http_client / tag) — próximo tick cobre

        create = work.get("create") or []
        if create:
            print(f"[regear_threads] {guild.id}: {len(create)} thread(s) pra criar no canal "
                  f"{channel.id} → {[ev.get('event_id') for ev in create]}")
        # Criação: IN_PROGRESS/REVIEW com regear_thread_dirty.
        for ev in create:
            await self._create_thread(guild, channel, lang, ev)

        # Arquivamento: terminais com thread ativa.
        for ev in work.get("archive") or []:
            await self._archive_thread(guild, lang, ev)

    async def _create_thread(self, guild: discord.Guild, channel: discord.TextChannel,
                             lang: str, ev: dict) -> None:
        event_id = ev.get("event_id")
        title = ev.get("title") or ""
        if not event_id:
            return
        name = t(lang, "ev_regear_thread_title", n=event_id, title=title)[:100]
        key = (guild.id, int(event_id))
        thread = guild.get_thread(self._thread_ids.get(key, 0))
        if thread is None:
            thread = next((item for item in channel.threads if item.name == name), None)
        if thread is None:
            print(f"[regear_threads] criando thread '{name}' p/ evento {event_id} no canal {channel.id}")
            try:
                thread = await dtimeout(channel.create_thread(
                    name=name, type=discord.ChannelType.public_thread,
                ))
            except Exception as e:
                print(f"[regear_threads] falhou criar thread p/ evento {event_id} "
                      f"em {channel.id}: {type(e).__name__}: {e}")
                return
            print(f"[regear_threads] ✓ thread {thread.id} criada p/ evento {event_id}")
            try:
                await dtimeout(thread.send(t(lang, "ev_regear_thread_header", n=event_id)))
            except SKIP_EXC:
                pass
        self._thread_ids[key] = thread.id
        await _post(
            f"/bot/events/{guild.id}/{event_id}/regear-thread-synced",
            {"regear_thread_id": str(thread.id), "clear_dirty": True},
        )

    async def _archive_thread(self, guild: discord.Guild, lang: str, ev: dict) -> None:
        tid = ev.get("regear_thread_id")
        event_id = ev.get("event_id")
        if not tid or not event_id:
            return
        try:
            thread = guild.get_thread(int(tid))
            if thread is None:
                # get_thread só cobre cache — discord.py não retém threads
                # arquivadas por inatividade nele (confirmado na doc de
                # get_thread: "does not always retrieve archived threads").
                # Sem este fallback, uma thread auto-arquivada pelo Discord
                # antes do evento finalizar era tratada como "deletada" abaixo
                # e NUNCA era trancada de verdade (só marcada arquivada no
                # backend pra sair da fila).
                thread = await dtimeout(guild.fetch_channel(int(tid)))
        except (TypeError, ValueError, *SKIP_EXC):
            thread = None
        if thread is None:
            # Thread sumiu (deletada) — marca arquivado pra sair da fila.
            await _post(
                f"/bot/events/{guild.id}/{event_id}/regear-thread-archived", {})
            return
        try:
            await dtimeout(thread.edit(archived=True, locked=True))
        except SKIP_EXC:
            return
        # Sinaliza p/ o backend tirar o evento da lista de arquivamento — evita
        # re-arquivar a cada 10s (rate-limit do Discord).
        await _post(
            f"/bot/events/{guild.id}/{event_id}/regear-thread-archived", {})


@tasks.loop(seconds=10)
async def regear_thread_work_loop(cog: "RegearThreads") -> None:
    for guild in cog.bot.guilds:
        try:
            await cog.sync_guild(guild)
        except Exception as e:
            print(f"[regear_threads] erro no loop ({guild.id}): {type(e).__name__}: {e}")


@regear_thread_work_loop.before_loop
async def _before() -> None:
    # discord.py chama before_loop SEM os args de .start(cog) (só o corpo
    # principal do loop recebe) — declarar `cog` aqui derruba a task com
    # TypeError a CADA .start(), antes do primeiro tick (raiz do "só funciona
    # quando o bot inicia": tudo que "funcionava" vinha só das chamadas
    # diretas do on_ready em main.py, nunca deste loop). Usa o _cog_ref
    # global (setado em cog_load) em vez de receber como parâmetro.
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@regear_thread_work_loop.error
async def _on_error(error: BaseException) -> None:
    # Confirmado empiricamente: se ISTO roda, o loop MORREU — tasks.loop só
    # chama .error() pra log e deixa a task terminar, nunca reagenda sozinho.
    # Sem handler nenhum (como era antes), essa morte é 100% silenciosa: o
    # asyncio só emite "Task exception was never retrieved" quando o GC
    # eventualmente coletar a task morta, o que pode nunca aparecer no
    # console. Loga alto E reinicia — o sistema se autocura em vez de ficar
    # morto pro resto do processo (mesmo espírito do retry em
    # battle_price_reprocessor.py no backend).
    import traceback
    print(f"[regear_threads] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        # .error() roda ANTES do _loop() interno terminar a task de verdade —
        # chamar .start()/.restart() aqui de forma síncrona corre com esse
        # encerramento (testado: dá TypeError de "task already running").
        # call_soon empurra pro próximo tick do event loop, depois que a task
        # atual já terminou.
        asyncio.get_running_loop().call_soon(lambda: regear_thread_work_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegearThreads(bot))
