import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import asyncio, os, time
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from localization import ZiggsTranslator
import error_handler
import http_client
import offline_queue
import ephemeral_guard

load_dotenv()

TOKEN      = os.getenv("DISCORD_TOKEN", "")
SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

# members e message_content são privilegiados: precisam estar ligados em
# "Server Members Intent" e "Message Content Intent" no Discord Developer
# Portal (Bot → Privileged Gateway Intents), senão on_member_remove/
# on_member_update (registro) e os comandos de prefixo (!addmoney etc.) não
# funcionam — sem message_content, ctx.message.content chega vazio.
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
# voice_states: necessário p/ ler channel.members da sala CTA no snapshot loop
# (cogs/voice_presence.py) — alimenta ParticipationMode.VOICE_PERCENT.
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


async def _post(path: str, body: dict | None = None) -> dict | None:
    """Confirma escritas de outbox; se o backend cair, a fila tenta novamente."""
    return await http_client.post_json(
        path, body or {}, tag="worker", attempts=2, queue_on_failure=True,
    )


async def _get(path: str) -> dict | None:
    """GET no site (best-effort) — usado pelo polling de trabalho pendente."""
    return await http_client.get_json(path)


async def heartbeat(guild: discord.Guild) -> None:
    body = {
        "guild_name": guild.name,
        "guild_icon": guild.icon.key if guild.icon else None,
    }
    await http_client.post_best_effort(f"/bot/heartbeat/{guild.id}", body)


@tasks.loop(minutes=5)
async def heartbeat_loop() -> None:
    # ponytail: paralelo como event_work_loop — com muitas guildas o
    # sequencial soma latência à toa (heartbeat é fire-and-forget anyway).
    await asyncio.gather(*(heartbeat(g) for g in bot.guilds), return_exceptions=True)


@tasks.loop(seconds=5)
async def event_work_loop() -> None:
    """Site não consegue empurrar trabalho pro bot (sem servidor HTTP de
    entrada) — o bot pergunta periodicamente se há mass-info pra postar/
    atualizar. Ver app/services/event_signups.py (has_pending_work) no site.

    Ciclo de 5s: mutações vindas do SITE (criar evento, transição de estado,
    liberação de funções) chegam aqui. Mutações vindas do BOT (signup/remoção)
    disparam refresh imediato via _trigger_massinfo_refresh no cog Events —
    não esperam este ciclo.

    Rebind pós-restart: guildas em cog._rebind_pending fazem o poll com
    force=true (ignora o gate de staleness e NÃO consome o outbox de pings —
    não pingua no rebind). O site pode não ter respondido no on_ready
    (start-all.cmd pode ligar o bot antes do backend), então o reedit do
    on_ready pode ter falhado; aqui reedita no primeiro poll bom pra religar
    os botões mortos pelo restart. Pings pendentes disparam no próximo poll
    normal (não-force).

    Paralelo: com N guildas o loop sequencial (await _get uma atrás da outra)
    nunca terminava a tempo — 50 guildas a 200ms cada = 10s, mas o ciclo é
    5s. asyncio.gather faz todas as requisições em paralelo (o TCPConnector
    limita a 32 conexões simultâneas por host, o que é o teto natural)."""
    cog = bot.get_cog("Events")
    if cog is None:
        return

    async def _poll_guild(guild) -> None:
        rebind = guild.id in cog._rebind_pending
        path = f"/bot/events/{guild.id}/pending-work"
        if rebind:
            path += "?force=true"
        data = await _get(path)
        if data is None:
            return  # site fora do ar — rebind fica pendente pro próximo tick
        if rebind:
            cog._rebind_pending.discard(guild.id)
        prompt_deletes = data.get("function_prompt_deletes") or []
        prompt_deletes_done = True
        if prompt_deletes and not rebind:
            deleted = await cog.delete_function_prompts(prompt_deletes)
            if deleted:
                await _post(
                    f"/bot/events/{guild.id}/function-prompt-deletes-acked",
                    {"message_ids": deleted},
                )
            prompt_deletes_done = len(deleted) == len(prompt_deletes)
        prompts = data.get("function_prompts") or []
        if prompts and not rebind and prompt_deletes_done:
            sent = await cog.send_function_prompts(guild, prompts)
            # O endpoint grava os IDs enviados e limpa o outbox na mesma
            # transação. Lista vazia também confirma tentativas impossíveis
            # (DM fechada), evitando reenviar para sempre.
            await _post(
                f"/bot/events/{guild.id}/function-prompt-messages",
                {"messages": sent},
            )
        if data.get("events") or data.get("needs_rebuild"):
            await cog.sync_massinfo(
                guild, data.get("events") or [],
                ping_triggers=data.get("ping_triggers") or [],
                purge_orphans=rebind,
            )

    await asyncio.gather(*(_poll_guild(g) for g in bot.guilds), return_exceptions=True)


@tasks.loop(seconds=3)
async def offline_queue_loop() -> None:
    """Drena a fila de escritas que falharam por erro de conexão (ver
    offline_queue.py). Roda a cada 3s: se a fila tem itens, tenta um drain —
    drain() para no primeiro erro de conexão (backend ainda fora), então é
    barato quando o backend está de pé e a fila está vazia (no-op)."""
    if not offline_queue.pending():
        return
    n = await offline_queue.drain()
    if n:
        print(f"✓ offline_queue: {n} escrita(s) re-enviada(s) ({offline_queue.pending()} pendentes)")


async def _wait_for_backend() -> None:
    """Bloqueia até o backend responder /health. start-all.cmd liga bot e
    backend em paralelo e o bot costuma ganhar a corrida — sem isto, o
    catch-up do on_ready dispara contra um backend ainda fora do ar e
    falha em silêncio (one-shot), deixando os botões mortos até a próxima
    mutação marcar dirty. Sonda a cada 2s; loga a cada ~10s pra não floodar."""
    if not SITE_URL:
        print("✗ BOT_SITE_URL vazio — bot vai rodar sem backend (apenas comandos locais)")
        return
    url = f"{SITE_URL}/health"
    last_log = 0.0
    while True:
        try:
            async with http_client.session().get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status == 200:
                    print(f"✓ backend online ({SITE_URL})")
                    return
        except Exception:
            pass
        now = time.monotonic()
        if now - last_log >= 10:
            print(f"… esperando backend em {SITE_URL} …")
            last_log = now
        await asyncio.sleep(2)


@bot.event
async def on_ready() -> None:
    print(f"✓ {bot.user} — {len(bot.guilds)} servidor(es)")
    # Confirma config essencial no console — sem isto, um BOT_SITE_URL/SECRET
    # vazio faz todo _get/_post virar no-op silencioso (nenhum erro, nenhuma
    # thread, nenhum snapshot) e não há como saber olhando só pro Discord.
    print(f"  BOT_SITE_URL={SITE_URL or '(vazio!)'} "
          f"BOT_API_SECRET={'definido' if API_SECRET else '(vazio!)'}")
    for guild in bot.guilds:
        await heartbeat(guild)
    if not heartbeat_loop.is_running():
        heartbeat_loop.start()
    if not event_work_loop.is_running():
        event_work_loop.start()
    if not offline_queue_loop.is_running():
        offline_queue_loop.start()
    # Catch-up imediato pós-(re)conexão: se o bot esteve offline, eventos cujo
    # horário chegou nesse meio ainda estão SCHEDULED no site. O pending-work faz
    # a transição automática SCHEDULED->IN_PROGRESS — força esse poll agora em
    # vez de esperar o primeiro tick do loop (~5s). Eventos que já estavam
    # IN_PROGRESS também voltam pro mass-info no mesmo ciclo (o estado vive no
    # site, não no bot). force=True: reedita o embed do mass-info AGORA, mesmo
    # se o site não tiver nada de novo — um restart mata os botões antigos
    # (View sem custom_id, ver MassinfoView) e só religa quando a mensagem é
    # reeditada; sem force, isso ficava esperando o gate de staleness do site
    # (podia deixar clique em "interaction failed" por dezenas de segundos).
    # Best-effort: falhou aqui, o loop de 5s cobre depois (sem force).
    # Antes de qualquer catch-up: garante que o backend subiu. start-all.cmd
    # liga os dois em paralelo; sem esta espera, o catch-up one-shot abaixo
    # dispara contra um backend ainda fora do ar e falha em silêncio.
    await _wait_for_backend()
    cog = bot.get_cog("Events")
    if cog is not None:
        # Marca TODAS as guildas pra rebind ANTES de tentar — o event_work_loop
        # (startado acima) pode tickar durante este catch-up e precisa enxergar
        # o rebind pendente. refresh_massinfo limpa o flag de cada guilda que
        # conseguir reeditar com sucesso; as que falharem (site fora do ar)
        # ficam pendentes pro loop reeditar no primeiro poll bom.
        for guild in bot.guilds:
            cog._rebind_pending.add(guild.id)
        for guild in bot.guilds:
            try:
                await cog.refresh_massinfo(guild, force=True)
            except Exception:
                pass
    # Mesmo catch-up pros embeds de evento (thread 📑 EVENTO #N em review/
    # finalizado) — EventEmbedView tem o mesmo problema de botão morto pós-
    # restart, e sem staleness-timer de fallback (ver EventEmbedView).
    embeds_cog = bot.get_cog("EventEmbeds")
    if embeds_cog is not None:
        for guild in bot.guilds:
            try:
                await embeds_cog.sync_event_embeds(guild, force=True)
            except Exception:
                pass
    # Mesmo catch-up pras threads de regear (🛠️ Regear — Evento #N) — de
    # propósito SEM listener de on_ready próprio no cog (ver regear_threads.py):
    # rodar antes do backend estar de pé sempre pegava config vazia (erro de
    # rede) e não criava nada, silenciosamente. Fazendo aqui, depois de
    # _wait_for_backend(), o catch-up imediato realmente funciona.
    regear_cog = bot.get_cog("RegearThreads")
    if regear_cog is not None:
        for guild in bot.guilds:
            try:
                await regear_cog.sync_guild(guild)
            except Exception:
                pass
    # Mesmo catch-up pras threads de lootlog (🪵 Log — Evento #N).
    lootlog_cog = bot.get_cog("LootlogThreads")
    if lootlog_cog is not None:
        for guild in bot.guilds:
            try:
                await lootlog_cog.sync_guild(guild)
            except Exception:
                pass
    # Mesmo catch-up pro calendário de nodes — sem isto, o update_calendar
    # (loop de 5min, cogs/nodes.py) faz a 1ª tentativa gated só por
    # wait_until_ready() (gateway do Discord), sem esperar o backend. Bot
    # costuma ganhar a corrida do start-all.cmd contra o backend, então essa
    # 1ª tentativa quase sempre batia num backend ainda fora do ar antes desse
    # catch-up existir — _sync_calendar já tem a defesa (não cria mensagem
    # nova se a requisição falhar), mas sem chamar aqui o calendário só
    # relinkava depois de até 5min esperando o próximo tick do loop.
    nodes_cog = bot.get_cog("Nodes")
    if nodes_cog is not None:
        for guild in bot.guilds:
            try:
                await nodes_cog.refresh_calendar(guild)
            except Exception:
                pass
    # Canal de logs (feature sempre ativa, ver cogs/audit_log.py): garante o
    # canal já na reconexão em vez de esperar o primeiro tick do loop (8s) —
    # guildas sem logs_channel_id ainda ganham o canal admin-only assim que o
    # bot sobe, sem depender de nenhuma mutação acontecer primeiro.
    audit_cog = bot.get_cog("BotAuditLog")
    if audit_cog is not None:
        for guild in bot.guilds:
            try:
                await audit_cog.sync_guild(guild)
            except Exception:
                pass
    try:
        await bot.tree.set_translator(ZiggsTranslator())
        synced = await bot.tree.sync()
        print(f"✓ {len(synced)} comando(s) sincronizado(s)")
    except Exception as e:
        print(f"✗ sync: {e}")


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    await heartbeat(guild)
    print(f"✓ Entrei em: {guild.name} ({guild.id})")


@bot.event
async def on_guild_remove(guild: discord.Guild) -> None:
    await http_client.post_best_effort(f"/bot/goodbye/{guild.id}")
    print(f"✗ Saí de: {guild.name} ({guild.id})")


@bot.event
async def on_interaction(interaction: discord.Interaction) -> None:
    """Hook global: roda antes de qualquer dispatch de componente. Reseta
    o timer de auto-delete da ephemeral ativa dessa interação — qualquer
    clique de botão/select conta como 'atividade' e renova os 60s."""
    if interaction.type in (
        discord.InteractionType.component,
        discord.InteractionType.modal_submit,
    ):
        ephemeral_guard.touch(interaction)


async def main() -> None:
    # bot.run() configura logging sozinho; como usamos bot.start() direto
    # (pra rodar dentro do nosso próprio asyncio.run), isso NUNCA acontece
    # sem esta chamada — todo _log.error/warning interno do discord.py (ex.:
    # exceção não tratada num callback de botão, via View.on_error) fica
    # mudo, sem NENHUM handler configurado. Explica botão que parece "não
    # responder": a exceção real nunca aparecia em lugar nenhum.
    discord.utils.setup_logging()
    ephemeral_guard.install()  # auto-delete de 60s pra toda ephemeral
    error_handler.install(bot)  # resposta educada + log pra qualquer comando que exploda
    async with bot:
        await bot.load_extension("cogs.general")
        await bot.load_extension("cogs.registration")
        await bot.load_extension("cogs.economy")
        await bot.load_extension("cogs.events")
        await bot.load_extension("cogs.regears")
        await bot.load_extension("cogs.regear_threads")
        await bot.load_extension("cogs.lootlogs")
        await bot.load_extension("cogs.lootlog_threads")
        await bot.load_extension("cogs.nodes")
        await bot.load_extension("cogs.voice_presence")
        await bot.load_extension("cogs.event_embeds")
        await bot.load_extension("cogs.event_cmd")
        await bot.load_extension("cogs.audit_log")
        await bot.load_extension("cogs.battle_feed")
        await bot.load_extension("cogs.juicy_kills")
        await bot.load_extension("cogs.profile_moderation")
        await bot.load_extension("cogs.scan_dashboard")
        try:
            await bot.start(TOKEN)
        finally:
            await http_client.close()


asyncio.run(main())
