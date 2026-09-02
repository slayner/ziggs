"""Dashboard TUI do companion — terminal próprio ao lado do backend.

Loop que lê o banco a cada REFRESH_SECS e renderiza com rich:
- Painel de alertas/health (companions travando, fila crescente, erro alto, sem heartbeat)
- Sparklines 24h (ASCII) — found/done/prices/history por hora
- Telemetria por região (proxy de companion — sem companion_id no schema)
- Companions ativos (tasks claimed não expiradas)
- Totais 24h + fila pending
- Top contributors por batalhas descobertas (Battle.found_by, all-time)
- Batalhas por região

Só leitura — nunca bloqueia o backend. Rodar de dentro de backend/ (venv):
    python -m scripts.companion_dashboard
ou
    python scripts/companion_dashboard.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# Garante que o diretório backend/ está no sys.path pra `from app.db import ...`
# funcionar quando rodado direto (python scripts/companion_dashboard.py).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import func, select  # noqa: E402

from rich import box  # noqa: E402
from rich.console import Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.battles import Battle  # noqa: E402
from app.models.companion import CompanionScanTask  # noqa: E402
from app.models.prices import ItemPrice, ItemPriceHistory  # noqa: E402

REFRESH_SECS = 5
HIST_HOURS = 24  # janela dos sparklines e agregados

# Thresholds de alerta — ponytail: fixos; calibrar em produção.
ALERT_PENDING_FLOOR = 500        # fila pending acima disso = alerta
ALERT_ERR_RATE_PCT = 30          # taxa erro 24h acima disso = alerta
ALERT_NO_HEARTBEAT_MINS = 60      # sem task done nesse tempo = alerta
ALERT_CLAIM_EXPIRING_SECS = 120  # claim expirando em < que isso = alerta

# Estado do rate limiter adaptativo vive na MEMÓRIA do processo do backend —
# o dashboard é outro processo, então busca via HTTP (GET /meta/albion-gate).
# Falha silenciosa (backend fora / versão antiga sem a rota) → painel "sem dados".
_API_BASE = os.environ.get("ZIGGS_API", "http://127.0.0.1:8000")
_RATE_HIST_MAX = 60          # ~5min de tendência do rate a 5s/refresh
_rate_hist: list[float] = []  # rolling, preenchido a cada _collect
_bat0: dict = {}             # max(Battle.id) + hora na 1ª leitura → taxa de descoberta da sessão

# Blocos Unicode p/ sparkline ASCII (rich 15 não tem rich.sparkline).
_SPARK = "▁▂▃▄▅▆▇█"


def _api_get(path: str) -> dict | None:
    """GET JSON de estado em memória do backend (rate limiter, delay da API —
    não dá pra ler do banco). Timeout curto e falha silenciosa: um backend fora
    ou lento não pode travar o dashboard."""
    try:
        with urllib.request.urlopen(f"{_API_BASE}{path}", timeout=2) as r:
            return json.load(r)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite não preserva tzinfo na leitura — mesmo padrão de prices.py.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    secs = int((_now() - _aware(dt)).total_seconds())
    if secs < 0:
        return "agora"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60}s"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _dur(secs: float) -> str:
    """Duração compacta a partir de segundos (pro delay da API): 45s, 12m, 8h3m."""
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _sparkline(values: list[int]) -> Text:
    # ponytail: ASCII sparkline manual (rich 15 não expõe Sparkline).
    # 24 buckets; teto visual = max. Bucket 0 desenha ▁ (não espaço).
    if not values:
        return Text("—", style="dim")
    mx = max(values)
    if mx == 0:
        return Text("· " * len(values), style="dim")
    out = []
    for v in values:
        idx = min(7, int(round(v * 7 / mx)))
        out.append(_SPARK[idx])
    return Text("".join(out), style="cyan")


def _hour_bucket(ts: datetime, now: datetime) -> int:
    """Índice 0..HIST_HOURS-1 (0=hora atual, cresce pro passado). -1 = fora da janela."""
    h = int((now - _aware(ts)).total_seconds() // 3600)
    return h if 0 <= h < HIST_HOURS else -1


def _buckets_from_timestamps(timestamps: list[datetime], now: datetime) -> list[int]:
    """Agrega timestamps em HIST_HOURS buckets, oldest→newest (p/ desenhar esq→dir)."""
    buckets = [0] * HIST_HOURS
    for ts in timestamps:
        h = _hour_bucket(ts, now)
        if h >= 0:
            buckets[HIST_HOURS - 1 - h] += 1
    return buckets


def _collect() -> dict:
    db = SessionLocal()
    try:
        now = _now()
        cutoff_24h = now - timedelta(hours=HIST_HOURS)

        # ── Ativos (tasks claimed não expiradas) ─────────────────────────
        active = db.scalars(
            select(CompanionScanTask)
            .where(
                CompanionScanTask.status == "claimed",
                CompanionScanTask.claim_expires_at > now,
            )
            .order_by(CompanionScanTask.claimed_at.desc())
        ).all()

        # ── Tasks done nas últimas 24h (com contagens p/ agregação) ──────
        done_rows = db.execute(
            select(
                CompanionScanTask.region,
                CompanionScanTask.completed_at,
                CompanionScanTask.found_count,
                CompanionScanTask.missing_count,
                CompanionScanTask.error_count,
            )
            .where(
                CompanionScanTask.status == "done",
                CompanionScanTask.completed_at >= cutoff_24h,
            )
        ).all()

        done_24h = len(done_rows)
        found_24h = sum(r[2] for r in done_rows)
        missing_24h = sum(r[3] for r in done_rows)
        errors_24h = sum(r[4] for r in done_rows)

        # Sparklines 24h: buckets por hora (oldest→newest)
        # ponytail: agregação em Python — puxa só a coluna de timestamp,
        # indexada por recorded_at. Em volume muito alto, trocar por GROUP
        # BY SQL com strftime('%s', ts)/3600.
        done_buckets = [0] * HIST_HOURS
        found_buckets = [0] * HIST_HOURS
        for r in done_rows:
            h = _hour_bucket(r[1], now)
            if h >= 0:
                idx = HIST_HOURS - 1 - h
                done_buckets[idx] += 1
                found_buckets[idx] += r[2]

        price_ts = db.scalars(
            select(ItemPrice.recorded_at).where(ItemPrice.recorded_at >= cutoff_24h)
        ).all()
        price_buckets = _buckets_from_timestamps(price_ts, now)

        hist_ts = db.scalars(
            select(ItemPriceHistory.recorded_at)
            .where(ItemPriceHistory.recorded_at >= cutoff_24h)
        ).all()
        hist_buckets = _buckets_from_timestamps(hist_ts, now)

        prices_24h = len(price_ts)
        hist_24h = len(hist_ts)

        # ── Fila pending ──────────────────────────────────────────────────
        pending = db.scalar(
            select(func.count()).select_from(CompanionScanTask)
            .where(CompanionScanTask.status == "pending")
        ) or 0

        # ── Último heartbeat (task done mais recente) ─────────────────────
        last_done = db.scalar(
            select(func.max(CompanionScanTask.completed_at))
            .where(CompanionScanTask.status == "done")
        )

        # ── Telemetria por região (proxy de companion) ────────────────────
        # Agrupa tasks done 24h por região + computa uptime + throughput.
        by_region_stats: dict[str, dict] = {}
        for r in done_rows:
            reg = r[0]
            st = by_region_stats.setdefault(reg, {
                "found": 0, "missing": 0, "errors": 0, "done": 0,
                "first_done": None, "last_done": None,
            })
            st["found"] += r[2]
            st["missing"] += r[3]
            st["errors"] += r[4]
            st["done"] += 1
            ts = _aware(r[1])
            if st["last_done"] is None or ts > st["last_done"]:
                st["last_done"] = ts
            if st["first_done"] is None or ts < st["first_done"]:
                st["first_done"] = ts
        # Complementa com tasks claimed ativas (companion trabalhando agora)
        for t in active:
            st = by_region_stats.setdefault(t.region, {
                "found": 0, "missing": 0, "errors": 0, "done": 0,
                "first_done": None, "last_done": None,
            })
            st["active"] = st.get("active", 0) + 1
            st.setdefault("claimed_at", t.claimed_at)

        # ── Top contributors (all-time) ───────────────────────────────────
        top = db.execute(
            select(Battle.found_by, func.count())
            .where(Battle.found_by.isnot(None))
            .group_by(Battle.found_by)
            .order_by(func.count().desc())
            .limit(10)
        ).all()

        # ── Rate limiter adaptativo + delay da API (via HTTP — memória) ───
        gate = _api_get("/meta/albion-gate")
        delay = _api_get("/meta/battle-delay")
        if gate:
            _rate_hist.append(gate["rate"])
            del _rate_hist[:-_RATE_HIST_MAX]

        # ── Descoberta REAL de batalhas (todas as fontes) ─────────────────
        # O "found" acima é SÓ do companion scan. MAX(id) (O(1) no índice PK)
        # cresce com TODA batalha nova — tracker ao vivo, sweeper e scan. Delta
        # desde que o dashboard abriu = taxa real de descoberta.
        max_battle_id = db.scalar(select(func.max(Battle.id))) or 0
        if "id" not in _bat0 and max_battle_id:
            _bat0["id"], _bat0["t"] = max_battle_id, now
        bat_delta = max_battle_id - _bat0.get("id", max_battle_id)
        bat_secs = (now - _bat0["t"]).total_seconds() if "t" in _bat0 else 0.0
        bat_rate = (bat_delta / bat_secs * 60) if bat_secs > 30 else None

        # ── Alertas ───────────────────────────────────────────────────────
        alerts: list[tuple[str, str]] = []  # (severidade, msg)

        # Claims prestes a expirar (companion pode ter travado)
        for t in active:
            if t.claim_expires_at:
                secs_left = int((_aware(t.claim_expires_at) - now).total_seconds())
                if secs_left < ALERT_CLAIM_EXPIRING_SECS:
                    alerts.append(("warn", f"Claim {t.region} {t.battle_id_start}-{t.battle_id_end} expira em {secs_left}s (travado?)"))

        # Fila pending crescente
        if pending > ALERT_PENDING_FLOOR:
            alerts.append(("warn", f"Fila pending alta: {pending} tarefas"))

        # Taxa de erro 24h
        total_probes_24h = found_24h + missing_24h + errors_24h
        if total_probes_24h > 0:
            err_rate = (errors_24h / total_probes_24h) * 100
            if err_rate > ALERT_ERR_RATE_PCT:
                alerts.append(("err", f"Taxa de erro 24h alta: {err_rate:.1f}% ({errors_24h}/{total_probes_24h})"))

        # Sem heartbeat
        if last_done is not None:
            mins_since = int((now - _aware(last_done)).total_seconds() // 60)
            if mins_since > ALERT_NO_HEARTBEAT_MINS:
                alerts.append(("err", f"Sem task done há {mins_since}min (companions parados?)"))
        else:
            alerts.append(("err", "Nenhuma task done no banco ainda"))

        return {
            "now": now,
            "active": active,
            "done_24h": done_24h,
            "found_24h": int(found_24h),
            "missing_24h": int(missing_24h),
            "errors_24h": int(errors_24h),
            "pending": int(pending),
            "top": top,
            "prices_24h": int(prices_24h),
            "hist_24h": int(hist_24h),
            "by_region_stats": by_region_stats,
            "last_done": last_done,
            "done_buckets": done_buckets,
            "found_buckets": found_buckets,
            "price_buckets": price_buckets,
            "hist_buckets": hist_buckets,
            "alerts": alerts,
            "gate": gate,
            "delay": delay,
            "rate_hist": list(_rate_hist),
            "bat_delta": bat_delta,
            "bat_rate": bat_rate,
        }
    finally:
        db.close()


def _render_alerts(d: dict) -> Panel:
    if not d["alerts"]:
        body = Text("✓ tudo saudável", style="bold green")
        border = "green"
    else:
        lines: list[Text] = []
        for sev, msg in d["alerts"]:
            style = "bold red" if sev == "err" else "bold yellow"
            icon = "✗" if sev == "err" else "▲"
            lines.append(Text(f"{icon} {msg}", style=style))
        body = Group(*lines)
        border = "red" if any(s == "err" for s, _ in d["alerts"]) else "yellow"
    return Panel(body, title="Alertas / health", border_style=border, padding=(0, 1))


def _render_sparklines(d: dict) -> Panel:
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="left", style="cyan", no_wrap=True)
    tbl.add_column(justify="left")
    tbl.add_column(justify="right", style="green", no_wrap=True)
    rows = [
        ("Batalhas found/h", d["found_buckets"], d["found_24h"]),
        ("Tasks done/h", d["done_buckets"], d["done_24h"]),
        ("Preços ingeridos/h", d["price_buckets"], d["prices_24h"]),
        ("Market history/h", d["hist_buckets"], d["hist_24h"]),
    ]
    for label, buckets, total in rows:
        tbl.add_row(label, _sparkline(buckets), str(total))
    return Panel(
        tbl,
        title="Throughput 24h — por hora (esq=antigo → dir=agora)",
        border_style="grey37", padding=(0, 1),
    )


def _render_per_region(d: dict) -> Panel:
    # ponytail: sem companion_id no schema → região é o melhor proxy que temos.
    # Migrar pra companion_id real exige mudança no cliente Tauri + migration.
    delay = d.get("delay") or {}
    tbl = Table(header_style="bold", expand=True, box=box.SIMPLE_HEAD, pad_edge=False)
    tbl.add_column("Região", style="cyan", no_wrap=True)
    tbl.add_column("Delay API", justify="right")
    tbl.add_column("Found", justify="right", style="green")
    tbl.add_column("Err", justify="right")
    tbl.add_column("found/min", justify="right", style="yellow")
    tbl.add_column("Heartbeat", justify="right")
    tbl.add_column("Ativos", justify="right", style="cyan")
    for reg in sorted(set(d["by_region_stats"]) | set(delay)):
        st = d["by_region_stats"].get(reg, {})
        dd = delay.get(reg)
        if dd:
            ds = dd["delay_secs"]
            # ~5min normal (verde); horas em dia de tráfego alto (vermelho).
            dcolor = "green" if ds < 900 else "yellow" if ds < 7200 else "bold red"
            delay_cell = Text(_dur(ds), style=dcolor)
        else:
            delay_cell = Text("—", style="dim")
        first, last = st.get("first_done"), st.get("last_done")
        if first and last:
            mins = max(1, int((last - first).total_seconds()) // 60)
            rate_str = f"{st.get('found', 0) / mins:.1f}"
        else:
            rate_str = "—"
        errs = st.get("errors", 0)
        tbl.add_row(
            reg, delay_cell, str(st.get("found", 0)),
            Text(str(errs), style="bold red" if errs else "dim"),
            rate_str, _age(last), str(st.get("active", 0)),
        )
    if not (d["by_region_stats"] or delay):
        tbl.add_row("—", *["—"] * 6)
    return Panel(tbl, title="Regiões — delay da API + throughput 24h", border_style="grey37", padding=(0, 1))


def _render_gate(d: dict) -> Panel:
    """Rate limiter adaptativo da API do Albion: taxa corrente, barra entre piso
    e teto, tendência (sessão do dashboard) e fila. Sem dados = backend fora."""
    g = d.get("gate")
    if not g:
        return Panel(Text("sem dados — backend fora?", style="dim italic"),
                     title="Albion API", border_style="grey37", padding=(1, 2))
    rate, ceil_, floor = g["rate"], g["ceiling"], g["floor"]
    frac = max(0.0, min(1.0, (rate - floor) / max(ceil_ - floor, 1e-9)))
    if rate >= ceil_ - 1e-6:
        color, label = "green", "no teto"      # taxa cheia, Albion aguentando
    elif frac <= 0.25:
        color, label = "red", "sob pressão"    # perto do piso, Albion revidando forte
    else:
        color, label = "yellow", "recuando"    # ajustando (recuou, recuperando)
    W = 18
    filled = int(round(frac * W))
    bar = Text.assemble(("█" * filled, color), ("░" * (W - filled), "grey37"))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", no_wrap=True, style="dim")
    grid.add_column()
    grid.add_row("taxa", Text.assemble((f"{rate:.2f} ", f"bold {color}"), ("req/s", "dim")))
    grid.add_row("", bar)
    grid.add_row("estado", Text(label, style=f"bold {color}"))
    grid.add_row("faixa", Text(f"{floor:.1f} – {ceil_:.1f} req/s", style="dim"))
    grid.add_row("tendência", _sparkline(d.get("rate_hist") or []))
    grid.add_row("fila", Text(str(g["queue"]), style="cyan"))
    return Panel(grid, title="Albion API — rate adaptativo", border_style=color, padding=(0, 1))


def _cols(left, right, lr=(1, 1)):
    """Duas colunas lado a lado (largura por ratio) — pra não empilhar tudo
    verticalmente num scroll infinito."""
    g = Table.grid(expand=True, padding=(0, 1))
    g.add_column(ratio=lr[0])
    g.add_column(ratio=lr[1])
    g.add_row(left, right)
    return g


def _render(d: dict) -> Group:
    pending = d["pending"]
    disc = ("medindo…" if d.get("bat_rate") is None
            else f"+{d['bat_delta']} ({d['bat_rate']:.1f}/min)")
    header = Text.assemble(
        ("ziggs ops", "bold cyan"),
        (f"   {d['now'].strftime('%Y-%m-%d %H:%M:%S')} UTC", "dim"),
        (f"   ·   refresh {REFRESH_SECS}s", "dim"),
        ("   ·   batalhas novas ", "dim"),
        (disc, "bold green" if (d.get("bat_rate") or 0) > 0 else "dim"),
        ("   ·   scan pending ", "dim"),
        (str(pending), "bold yellow" if pending > ALERT_PENDING_FLOOR else "cyan"),
        (f"   ·   último done {_age(d['last_done'])}", "dim"),
    )

    # Companions ativos (tasks claimed não expiradas)
    active_tbl = Table(header_style="bold", expand=True, box=box.SIMPLE_HEAD, pad_edge=False)
    active_tbl.add_column("Região", style="cyan", no_wrap=True)
    active_tbl.add_column("Range", justify="right", style="dim")
    active_tbl.add_column("Claimed", justify="right", style="green")
    active_tbl.add_column("Expira", justify="right", style="yellow")
    for t in d["active"]:
        exp = t.claim_expires_at
        secs_left = int((_aware(exp) - d["now"]).total_seconds()) if exp else 0
        exp_str = f"{secs_left // 60}m{secs_left % 60}s" if secs_left >= 0 else "expirando"
        active_tbl.add_row(t.region, f"{t.battle_id_start}-{t.battle_id_end}", _age(t.claimed_at), exp_str)
    if not d["active"]:
        active_tbl.add_row("—", "—", "—", "—")
    active_panel = Panel(active_tbl, title=f"Companions ativos — {len(d['active'])}",
                         border_style="grey37", padding=(0, 1))

    # Top contributors (all-time)
    top_tbl = Table(header_style="bold", expand=True, box=box.SIMPLE_HEAD, pad_edge=False)
    top_tbl.add_column("#", justify="right", style="dim")
    top_tbl.add_column("Nick", style="cyan")
    top_tbl.add_column("Batalhas", justify="right", style="green")
    for i, (nick, cnt) in enumerate(d["top"], 1):
        top_tbl.add_row(str(i), nick or "?", str(cnt))
    if not d["top"]:
        top_tbl.add_row("—", "—", "—")
    top_panel = Panel(top_tbl, title="Top contributors (all-time)", border_style="grey37", padding=(0, 1))

    return Group(
        header,
        _cols(_render_alerts(d), _render_gate(d), lr=(2, 1)),
        _render_sparklines(d),
        _render_per_region(d),
        _cols(active_panel, top_panel),
    )


def main() -> None:
    # ponytail: screen=True ocupa a tela toda e limpa a cada refresh (dashboard
    # fixo, sem flicker). Rich restaura o terminal em Ctrl-C ou exceção.
    try:
        with Live(_render(_collect()), screen=True, refresh_per_second=1) as live:
            while True:
                time.sleep(REFRESH_SECS)
                live.update(_render(_collect()))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Live restaura o terminal antes de propagar — mas em screen=True
        # garantir o restore manual em casos patológicos.
        print(f"dashboard erro: {e:#}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()