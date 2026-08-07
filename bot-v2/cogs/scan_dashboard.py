"""Scan dashboard — painel ao vivo da frota de scan distribuído.

Polla o backend em `GET /scan/stats` a cada 30s e mantém um ÚNICO embed
atualizado num canal fixo do Discord — envia na 1ª vez, EDITA nas seguintes
(em vez de inundar o canal). Se a mensagem sumir ou o bot reiniciar, rebusca
o ID nas últimas 20 mensagens do canal pra reusar, evitando dashboards
duplicados.

Mesma estrutura de cogs/battle_feed.py: @tasks.loop, _cog_ref global,
before_loop espera wait_until_ready, .error auto-reinicia via call_soon.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client

# Canal fixo do dashboard de scan. Sem config por-guilda: é um painel global
# do backend, a guilda onde ele vive é decisão do dono, não do usuário.
DASH_CHANNEL_ID = 1535135345874567279
REFRESH_SECS = 30
EMBED_TITLE = "🛡️ Ziggs Scan Fleet"

# Limiares de heartbeat (em segundos). <30 saudável, 30-90 staleness, >90 (ou
# status != active) = morto.
STALE_AFTER_S = 30
DEAD_AFTER_S = 90

COLOR_OK = 0x2ecc71      # green
COLOR_WARN = 0xf1c40f    # yellow (stale)
COLOR_BAD = 0xe74c3c     # red (dead / no workers)

_cog_ref: "ScanDashboard | None" = None


def _fmt_age(age: Optional[float]) -> str:
    if not isinstance(age, (int, float)):
        return "—"
    return f"{age:.1f}s ago"


def _worker_line(w: dict) -> tuple[str, str]:
    """Devolve (ícone de saúde, linha formatada) pra um worker."""
    name = w.get("name") or "?"
    region = w.get("region_pref") or "?"
    age = w.get("last_heartbeat_age_s")
    status = (w.get("status") or "").lower()

    # Regra da task: 🔴 pra dead (status != active OU heartbeat >90s),
    # 🟡 pra staleness 30-90s, 🟢 pra fresco. Status não-active implica DEAD
    # mesmo sem age (backend pode enviar chave vazia num worker novo).
    if status != "active" or not isinstance(age, (int, float)) or age > DEAD_AFTER_S:
        return "🔴", f"🔴 {name} ({region}) — DEAD ({_fmt_age(age)})"

    tasks_done = w.get("total_tasks_done") or 0
    found = w.get("total_battles_found") or 0
    body = f" — {tasks_done} tasks, {found} found, {age:.1f}s ago"
    if age > STALE_AFTER_S:
        return "🟡", f"🟡 {name} ({region})" + body
    return "🟢", f"🟢 {name} ({region})" + body


def _build_embed(data: dict) -> discord.Embed:
    workers = data.get("workers") or []
    tasks_d = data.get("tasks") or {}
    per_region = data.get("per_region") or {}

    any_stale = False
    any_dead = False
    lines: list[str] = []
    for w in workers:
        icon, line = _worker_line(w)
        if icon == "🔴":
            any_dead = True
        elif icon == "🟡":
            any_stale = True
        lines.append(line)

    if any_dead or not workers:
        color = COLOR_BAD
    elif any_stale:
        color = COLOR_WARN
    else:
        color = COLOR_OK

    workers_text = "\n".join(lines) if lines else "⚠️ Nenhum worker registrado"

    embed = discord.Embed(title=EMBED_TITLE, color=color)
    embed.add_field(name="Workers", value=workers_text, inline=False)

    pending = tasks_d.get("pending") or 0
    claimed = tasks_d.get("claimed") or 0
    done = tasks_d.get("done") or 0
    # Backlog (>100) em negrito, igual à task.
    pending_str = f"**{pending}**" if isinstance(pending, int) and pending > 100 else str(pending)
    embed.add_field(
        name="Queue",
        value=f"pending: {pending_str} | claimed: {claimed} | done: {done}",
        inline=False,
    )

    region_parts = []
    for region in sorted(per_region):
        r = per_region[region] or {}
        region_parts.append(
            f"{region}: {r.get('pending', 0)}P {r.get('claimed', 0)}C {r.get('done', 0)}D"
        )
    embed.add_field(
        name="Per-Region",
        value=" | ".join(region_parts) or "—",
        inline=False,
    )

    total_found = sum((w.get("total_battles_found") or 0) for w in workers)
    last_found = sum((w.get("last_found") or 0) for w in workers)
    embed.add_field(
        name="Throughput",
        value=f"Total found: {total_found} | Last batch: {last_found}",
        inline=False,
    )

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    embed.set_footer(text=f"Updated {ts} · {REFRESH_SECS}s · /scan/stats")
    return embed


class ScanDashboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._dashboard_msg_id: Optional[int] = None

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[scan_dashboard] cog carregada — loop de dashboard ativo")
        if not scan_dashboard_loop.is_running():
            scan_dashboard_loop.start(self)

    async def cog_unload(self) -> None:
        scan_dashboard_loop.cancel()

    async def _update(self) -> None:
        channel = self.bot.get_channel(DASH_CHANNEL_ID)
        if channel is None:
            # Sem fetch — canal fora do cache só rola se o bot não tá na guilda,
            # e aí não tem permissão pra falar de qualquer forma.
            return

        data = await http_client.get_json("/scan/stats", tag="scan_dashboard")
        if data is None:
            return  # backend fora do ar — skip este tick, mantém o último embed

        embed = _build_embed(data)

        msg: Optional[discord.Message] = None
        if self._dashboard_msg_id is not None:
            try:
                msg = await channel.fetch_message(self._dashboard_msg_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                msg = None

        if msg is None:
            # Startup / restart: rebusca o dashboard nas últimas 20 mensagens
            # pra não duplicar. Mesma pegadinha do restart do bot — o ID em
            # memória se perdeu mas o painel antigo segue vivo no canal.
            try:
                async for m in channel.history(limit=20):
                    if m.author.id == self.bot.user.id and m.embeds and m.embeds[0].title == EMBED_TITLE:
                        msg = m
                        break
            except (discord.Forbidden, discord.HTTPException):
                msg = None

        try:
            if msg is None:
                msg = await channel.send(embed=embed)
            else:
                await msg.edit(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[scan_dashboard] erro ao postar/editar: {type(e).__name__}: {e}")
            return

        if msg is not None:
            self._dashboard_msg_id = msg.id


@tasks.loop(seconds=REFRESH_SECS)
async def scan_dashboard_loop(cog: ScanDashboard) -> None:
    try:
        await cog._update()
    except Exception as e:
        # Erro inesperado não mata o loop de verdade (o .error cuida disso),
        # mas loga e segue — próximo tick reedita. Mesma aba do battle_feed.
        print(f"[scan_dashboard] tick falhou: {type(e).__name__}: {e}")


@scan_dashboard_loop.before_loop
async def _before() -> None:
    if _cog_ref is not None:
        await _cog_ref.bot.wait_until_ready()


@scan_dashboard_loop.error
async def _on_error(error: BaseException) -> None:
    import traceback
    print(f"[scan_dashboard] LOOP MORREU, reiniciando: {type(error).__name__}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if _cog_ref is not None:
        asyncio.get_running_loop().call_soon(lambda: scan_dashboard_loop.start(_cog_ref))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScanDashboard(bot))