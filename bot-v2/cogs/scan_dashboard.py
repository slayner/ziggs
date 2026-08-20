"""Scan dashboard — painel compacto da frota de scan distribuído.

Um ÚNICO embed que se edita a cada 30s. Em transição grave (stream → critical,
todos workers mortos), o bot DELETA o embed atual e re-envia com @everyone no
content — Discord só pinga em mensagem nova, não em edit. Na recuperação,
deleta e re-envia limpo pra remover o alerta.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout

DASH_CHANNEL_ID = 1535135345874567279
REFRESH_SECS = 30
EMBED_TITLE = "🛡️ Scan Fleet"

COLOR_OK = 0x2ecc71
COLOR_WARN = 0xf1c40f
COLOR_BAD = 0xe74c3c

PING_COOLDOWN_S = 300

_cog_ref: "ScanDashboard | None" = None


def _fmt_age(s: Optional[float]) -> str:
    if not isinstance(s, (int, float)):
        return "—"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.1f}h"


def _sla_icon(status: str) -> str:
    return {"healthy": "🟢", "at_risk": "🟡", "critical": "🔴"}.get(status, "⚪")


def _stream_line(s: dict, ckt: dict) -> str:
    """Uma linha compacta de stream pro grid."""
    st = s.get("status", "?")
    icon = _sla_icon(st)
    if ckt.get("state") == "open":
        return f"{icon} **OPEN**"
    if st == "critical":
        return f"{icon} {_fmt_age(s.get('recent_age_s'))}"
    rate = s.get("scan_items_per_min") or 0
    return f"{icon} {rate:.0f}/m"


def _build_embed(data: dict) -> discord.Embed:
    sla = data.get("sla") or {}
    circuits = data.get("circuits") or {}
    workers = data.get("workers") or []
    tasks_d = data.get("tasks") or {}
    alerts = data.get("alerts") or []
    processing = data.get("processing") or {}

    any_critical = any(v.get("status") == "critical" for v in sla.values())
    any_warn = any(v.get("status") == "at_risk" for v in sla.values())
    active = sum(1 for w in workers if (w.get("status") or "").lower() == "active")
    all_dead = bool(workers) and active == 0

    color = COLOR_BAD if (any_critical or all_dead) else COLOR_WARN if any_warn else COLOR_OK

    pending = tasks_d.get("pending") or 0
    done = tasks_d.get("done") or 0
    failed = tasks_d.get("failed") or 0

    embed = discord.Embed(title=EMBED_TITLE, color=color)
    embed.description = f"`{active}` workers · `{pending}` pendente · `{done}` done" + (
        f" · `{failed}` falhou" if failed else ""
    )

    # ── Grid 3 colunas: Americas, Europe, Asia ──
    for region, flag in (("americas", "🌎"), ("europe", "🌍"), ("asia", "🌏")):
        lines = []
        for feed, label in (("battles", "Btl"), ("kills", "Kls")):
            key = f"{region}/{feed}"
            s = sla.get(key) or {}
            ckt = circuits.get(key) or {}
            lines.append(f"**{label}** {_stream_line(s, ckt)}")
        embed.add_field(name=f"{flag} {region.title()}", value="\n".join(lines), inline=True)

    # ── Processamento de batalhas (light vs deep) ──
    proc_lines: list[str] = []
    total_light = 0
    total_deep = 0
    for region, flag in (("americas", "🌎"), ("europe", "🌍"), ("asia", "🌏")):
        p = processing.get(region) or {}
        light = p.get("light", 0)
        deep = p.get("deep", 0)
        total_light += light
        total_deep += deep
        pct = round(deep / (light + deep) * 100) if (light + deep) > 0 else 100
        proc_lines.append(f"{flag} `{deep}` deep · `{light}` light · {pct}%")
    embed.add_field(
        name=f"⚙️ Processamento ({total_deep} prontas · {total_light} pendentes)",
        value="\n".join(proc_lines),
        inline=False,
    )

    # ── Incidentes ──
    incident_lines: list[str] = []
    for stream in sorted(sla):
        ckt = circuits.get(stream) or {}
        if ckt.get("state") == "open":
            errs = ckt.get("consecutive_errors") or 0
            incident_lines.append(f"🔴 `{stream}` circuit open ({errs} errs)")
        if ckt.get("paused"):
            incident_lines.append(f"⏸ `{stream}` paused")

    for a in alerts:
        t = a.get("type")
        stream = a.get("stream", "?")
        if t == "lap_stalled":
            incident_lines.append(f"⚠ `{stream}` lap stalled {_fmt_age(a.get('age_s'))}")
        elif t == "page_failed":
            incident_lines.append(f"⚠ `{stream}` pg {a.get('offset', '?')} ({a.get('attempts', '?')}x)")

    shown = incident_lines[:6]
    if len(incident_lines) > 6:
        shown.append(f"*... +{len(incident_lines) - 6} mais*")

    embed.add_field(
        name="⚠️ Incidentes" if incident_lines else "✅ Tudo operacional",
        value="\n".join(shown) if shown else "Nenhum incidente ativo",
        inline=False,
    )

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    embed.set_footer(text=f"{ts} · atualiza em {REFRESH_SECS}s")
    return embed


class ScanDashboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._dashboard_msg_id: Optional[int] = None
        self._msg_is_ping = False  # a msg atual foi enviada com @everyone?
        self._last_status: dict[str, str] = {}
        self._pinged_at: dict[str, float] = {}

    async def cog_load(self) -> None:
        global _cog_ref
        _cog_ref = self
        print("[scan_dashboard] cog carregada")
        if not scan_dashboard_loop.is_running():
            scan_dashboard_loop.start(self)

    async def cog_unload(self) -> None:
        scan_dashboard_loop.cancel()

    def _detect_grave(self, data: dict) -> Optional[str]:
        """Retorna texto de ping se houve transição grave, senão None."""
        sla = data.get("sla") or {}
        workers = data.get("workers") or []
        circuits = data.get("circuits") or {}
        now_ts = datetime.now(timezone.utc).timestamp()
        reasons: list[str] = []

        for stream, s in sla.items():
            st = s.get("status", "unknown")
            prev = self._last_status.get(stream)
            if st == "critical" and prev != "critical":
                if now_ts - self._pinged_at.get(stream, 0) > PING_COOLDOWN_S:
                    ckt = circuits.get(stream) or {}
                    reason = "circuit open" if ckt.get("state") == "open" else "data stale"
                    reasons.append(f"🔴 **{stream}** — {reason}")
                    self._pinged_at[stream] = now_ts
            self._last_status[stream] = st

        all_dead = bool(workers) and all(
            (w.get("status") or "").lower() != "active" for w in workers
        )
        if all_dead and self._last_status.get("__all_dead__") != "True":
            reasons.append("💀 **Todos os workers estão mortos**")
        self._last_status["__all_dead__"] = str(all_dead)

        return ("@everyone\n" + "\n".join(reasons)) if reasons else None

    def _any_critical(self, data: dict) -> bool:
        sla = data.get("sla") or {}
        workers = data.get("workers") or []
        active = sum(1 for w in workers if (w.get("status") or "").lower() == "active")
        return any(v.get("status") == "critical" for v in sla.values()) or (bool(workers) and active == 0)

    async def _find_dashboard(self, channel: discord.TextChannel) -> Optional[discord.Message]:
        if self._dashboard_msg_id is not None:
            try:
                return await dtimeout(channel.fetch_message(self._dashboard_msg_id))
            except SKIP_EXC:
                pass
        try:
            async for m in channel.history(limit=20):
                if m.author.id == self.bot.user.id and m.embeds and m.embeds[0].title == EMBED_TITLE:
                    return m
        except SKIP_EXC:
            pass
        return None

    async def _send_fresh(self, channel: discord.TextChannel, embed: discord.Embed, content: str = "") -> None:
        """Deleta a msg atual e envia nova (necessário pra @everyone pingar)."""
        old = await self._find_dashboard(channel)
        if old is not None:
            try:
                await dtimeout(old.delete())
            except SKIP_EXC:
                pass
        msg = await dtimeout(channel.send(content=content, embed=embed))
        self._dashboard_msg_id = msg.id
        self._msg_is_ping = bool(content)

    async def _update(self) -> None:
        channel = self.bot.get_channel(DASH_CHANNEL_ID)
        if channel is None:
            return

        data = await http_client.get_json("/scan/stats", tag="scan_dashboard")
        if data is None:
            return

        embed = _build_embed(data)

        try:
            msg = await self._find_dashboard(channel)
            if msg is None:
                msg = await dtimeout(channel.send(embed=embed))
                self._dashboard_msg_id = msg.id
                self._msg_is_ping = False
            else:
                await dtimeout(msg.edit(embed=embed))
        except SKIP_EXC as e:
            print(f"[scan_dashboard] erro: {e}")


@tasks.loop(seconds=REFRESH_SECS)
async def scan_dashboard_loop(cog: ScanDashboard) -> None:
    try:
        await cog._update()
    except Exception as e:
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
