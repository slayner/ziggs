import { useEffect, useMemo, useRef, useState } from "react";
import { useLang, useT, REGION_LABELS, SERVER_LABELS, type GameServer } from "../i18n";
import { silver, silverShort, dateUTC } from "../lib/format";
import { navigate } from "../router";
import GlobalSearch, { RecentBattleRow, type RecentBattle } from "./GlobalSearch";
import AdBanner from "./AdBanner";
import AffiliateBanner from "./AffiliateBanner";
import { Panel, PanelHeader } from "./Panel";

// ── Patch notes (Steam News, ver app/api/routes/meta.py pro porquê) ────────
interface PatchNote { title: string; url: string; date: number }

function usePatchNotes() {
  const [notes, setNotes] = useState<PatchNote[] | null>(null);
  useEffect(() => {
    fetch("/meta/patch-notes").then(r => r.json()).then(setNotes).catch(() => setNotes([]));
  }, []);
  return notes;
}

function PatchNoteRow({ n }: { n: PatchNote }) {
  return (
    <a
      href={n.url}
      target="_blank"
      rel="noreferrer"
      className="-mx-1 flex items-center justify-between gap-3 rounded px-1 py-2.5 text-sm hover:bg-zinc-800/30"
    >
      <span className="truncate text-zinc-200">{n.title}</span>
      <span className="shrink-0 text-xs text-zinc-500 tabular-nums">{dateUTC(new Date(n.date * 1000).toISOString())}</span>
    </a>
  );
}

function PatchNotesCard({ onSeeAll }: { onSeeAll: () => void }) {
  const t = useT();
  const notes = usePatchNotes();
  return (
    <Panel className="p-4">
      <PanelHeader title={t("patchNotesTitle")} action={t("seeAllPatchNotes")} onAction={onSeeAll} />
      {notes === null && <div className="py-8 text-center text-sm text-zinc-500">{t("loading")}</div>}
      {notes?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">{t("changelogUnavailable")}</div>}
      <div className="divide-y divide-zinc-800/60">
        {notes?.slice(0, 10).map(n => <PatchNoteRow key={n.url} n={n} />)}
      </div>
    </Panel>
  );
}

function PatchNotesPage({ onBack }: { onBack: () => void }) {
  const t = useT();
  const notes = usePatchNotes();
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <button onClick={onBack} className="mb-4 text-sm text-zinc-400 hover:text-zinc-200">← {t("back")}</button>
      <h1 className="dash-panel-title mb-4">{t("patchNotesPageTitle")}</h1>
      {notes === null && <div className="py-16 text-center text-zinc-500">{t("loading")}</div>}
      <Panel className="divide-y divide-zinc-800/60 px-4">
        {notes?.map(n => <PatchNoteRow key={n.url} n={n} />)}
      </Panel>
    </div>
  );
}

// ── Jogadores ativos (substitui patch notes na posição original) ───────
type ActiveRegionKey = "global" | "americas" | "europe" | "asia";
const ACTIVE_PLAYERS_ROWS: ActiveRegionKey[] = ["global", "americas", "europe", "asia"];

const ACTIVE_REGION_COLORS: Record<ActiveRegionKey, string> = {
  global: "#a78bfa", americas: "#34d399", europe: "#38bdf8", asia: "#fbbf24",
};

type CountPoint = { t: number; count: number };
type ActivePlayersHistory = { since: number | null; series: Record<ActiveRegionKey, CountPoint[]> };

async function fetchActivePlayersHistory(range: ChartRange): Promise<ActivePlayersHistory> {
  const res = await fetch(`${BATTLES_API}/battles/active-players/history?range=${range}`);
  if (!res.ok) throw new Error(`active-players-history ${res.status}`);
  const d: { collected_since: string | null; series: Record<ActiveRegionKey, CountPoint[]> } = await res.json();
  return { since: d.collected_since ? Date.parse(d.collected_since) : null, series: d.series };
}

function downsampleCount(points: CountPoint[]): CountPoint[] {
  return downsample(points, bucket => ({ count: Math.round(bucket.reduce((s, p) => s + p.count, 0) / bucket.length) }));
}

// Coleta começou agora (ver services/player_count_snapshot.py) — não faz
// sentido oferecer "1 ano" quando só existe 1 dia de histórico. Cada botão
// só aparece quando já dá pra preencher aquele período de verdade; "Tudo"
// não tem mínimo, sempre mostra o que já foi coletado.
const RANGE_MIN_DAYS: Record<ChartRange, number> = { "1m": 31, "6m": 183, "1y": 366, all: 0 };

// O gráfico SUBSTITUI os números (pedido explícito) — o valor "atual" de
// cada região já aparece no fim da linha global (série primária) e no rótulo
// de cada série; não precisa mais do fetch/lista separada de active-players.
function ActivePlayersCard() {
  const t = useT();
  const { lang } = useLang();
  const RANGES = useRanges();

  const [range, setRange] = useState<ChartRange>("all");
  const [history, setHistory] = useState<ActivePlayersHistory | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const [zoom, setZoom] = useState<{ t0: number; t1: number } | null>(null);

  function selectRange(r: ChartRange) {
    setRange(r);
    setZoom(null);
  }

  useEffect(() => {
    function load() {
      setHistoryError(false);
      fetchActivePlayersHistory(range).then(setHistory).catch(() => setHistoryError(true));
    }
    load();
    const timer = setInterval(load, 300_000);
    return () => clearInterval(timer);
  }, [range]);

  const series: ChartSeriesInput[] | null = history ? [...ACTIVE_PLAYERS_ROWS]
    .sort((a, b) => (a === "global" ? 1 : 0) - (b === "global" ? 1 : 0))
    .map(key => ({
      key, label: REGION_LABELS[lang][key], color: ACTIVE_REGION_COLORS[key], primary: key === "global",
      points: downsampleCount(history.series[key] ?? []).map(p => ({ t: p.t, v: p.count })),
    })) : null;

  const collectedDays = history?.since != null ? (Date.now() - history.since) / 86_400_000 : 0;
  const availableRanges = RANGES.filter(r => collectedDays >= RANGE_MIN_DAYS[r.key]);

  return (
    <Panel className="flex h-full min-h-[300px] flex-col p-4">
      <PanelHeader title={t("activePlayersTitle")} tooltip={t("activePlayersTooltip")}>
        <div className="flex gap-1">
          {zoom ? (
            <button onClick={() => setZoom(null)} className="dash-chip dash-chip-on">
              ↺ {t("resetZoom")}
            </button>
          ) : availableRanges.map(r => (
            <button
              key={r.key}
              onClick={() => selectRange(r.key)}
              className={`dash-chip${r.key === range ? " dash-chip-on" : ""}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </PanelHeader>
      {historyError && <div className="flex flex-1 items-center justify-center text-center text-sm text-red-400">{t("chartLoadError")}</div>}
      {!historyError && !series && <div className="min-h-0 flex-1"><ChartSkeleton /></div>}
      {!historyError && series && (
        <div className="min-h-0 flex-1">
          <MultiLineChart
            key={range} series={series} formatValue={v => v.toLocaleString("pt-BR")}
            zoom={zoom} onZoomChange={setZoom}
          />
        </div>
      )}
    </Panel>
  );
}

// ── Destaques do servidor: ranking semanal de fama PvP por guilda ─────────
// Reseta todo domingo 00:00 UTC (ver _week_start_utc no backend). Só conta
// fama de batalha elegível: guilda com mais de 15 jogadores NESSA luta, luta
// letal, com cura registrada de qualquer lado.
interface FameGuild {
  albion_guild_id: string; name: string; alliance_name: string | null; fame: number; avg_players: number;
}

function GuildFameRow({ g, rank, isGlowing, isOvertaking, onGlowEnd, onOvertakeEnd }: {
  g: FameGuild; rank: number; isGlowing?: boolean; isOvertaking?: boolean;
  onGlowEnd?: () => void; onOvertakeEnd?: () => void;
}) {
  return (
    <button
      onClick={() => navigate(`/guild/${encodeURIComponent(g.albion_guild_id)}`)}
      onAnimationEnd={e => { if (e.animationName === "dash-rank-overtake") onOvertakeEnd?.(); }}
      className={`flex w-full items-center gap-3 rounded px-1 py-2.5 text-left hover:bg-zinc-800/30${isOvertaking ? " dash-rank-overtake" : ""}`}
    >
      <span className="w-5 shrink-0 text-center text-xs font-bold text-zinc-500">{rank}</span>
      <div className="min-w-0 flex-1 truncate text-sm text-zinc-200">
        {g.alliance_name && <span className="text-zinc-500">[{g.alliance_name}] </span>}
        {g.name}
        <span className="ml-1.5 align-middle text-[10px] text-zinc-600">{g.avg_players}</span>
      </div>
      <span
        className={`shrink-0 text-xs font-semibold text-amber-400/80 tabular-nums${isGlowing ? " dash-text-glow" : ""}`}
        onAnimationEnd={e => { e.stopPropagation(); onGlowEnd?.(); }}
      >
        {silverShort(g.fame)}
      </span>
    </button>
  );
}

function ServerHighlightsCard({ onSeeAll }: { onSeeAll: () => void }) {
  const t = useT();
  const { servers } = useLang();
  const [guilds, setGuilds] = useState<FameGuild[] | null>(null);
  const prevFameRef = useRef<Map<string, number>>(new Map());
  const prevOrderRef = useRef<string[]>([]);
  const [glowing, setGlowing] = useState<Set<string>>(new Set());
  const [overtaking, setOvertaking] = useState<Set<string>>(new Set());

  useEffect(() => {
    function load() {
      const regions = servers.map(s => SERVER_TO_REGION[s]).join(",");
      fetch(`${BATTLES_API}/battles/highlights?regions=${regions}`)
        .then(r => r.json())
        .then((d: { guilds: FameGuild[] }) => {
          const newOrder = d.guilds.map(g => g.albion_guild_id);
          if (prevFameRef.current.size) {
            const changedFame = d.guilds
              .filter(g => prevFameRef.current.get(g.albion_guild_id) !== g.fame)
              .map(g => g.albion_guild_id);
            if (changedFame.length) setGlowing(prev => new Set([...prev, ...changedFame]));

            // "Passou" alguém = subiu de posição E foi a própria fama dela que
            // mudou nesse ciclo (senão é só reordenação por causa de outra
            // guilda, não a animação de "ultrapassagem" pedida).
            const changedFameSet = new Set(changedFame);
            const prevIndex = new Map(prevOrderRef.current.map((id, i) => [id, i]));
            const movedUp = newOrder.filter((id, i) => {
              const before = prevIndex.get(id);
              return before !== undefined && i < before && changedFameSet.has(id);
            });
            if (movedUp.length) setOvertaking(prev => new Set([...prev, ...movedUp]));
          }
          prevFameRef.current = new Map(d.guilds.map(g => [g.albion_guild_id, g.fame]));
          prevOrderRef.current = newOrder;
          setGuilds(d.guilds);
        })
        .catch(() => setGuilds([]));
    }
    load();
    const timer = setInterval(load, 300_000);
    return () => clearInterval(timer);
  }, [servers]);

  return (
    <Panel className="p-4">
      <PanelHeader title={t("serverHighlightsTitle")} tooltip={t("serverHighlightsTooltip")}
        action={t("seeAllHighscoresLink")} onAction={onSeeAll} />
      {guilds === null && <div className="py-8 text-center text-sm text-zinc-500">{t("loading")}</div>}
      {guilds?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">{t("notEnoughData")}</div>}
      <div className="divide-y divide-zinc-800/60">
        {guilds?.map((g, i) => (
        <GuildFameRow
          key={g.albion_guild_id}
          g={g}
          rank={i + 1}
          isGlowing={glowing.has(g.albion_guild_id)}
          isOvertaking={overtaking.has(g.albion_guild_id)}
          onGlowEnd={() => setGlowing(prev => { const n = new Set(prev); n.delete(g.albion_guild_id); return n; })}
          onOvertakeEnd={() => setOvertaking(prev => { const n = new Set(prev); n.delete(g.albion_guild_id); return n; })}
        />
      ))}
      </div>
    </Panel>
  );
}

// ── Chart primitives (compartilhado entre Gold Price e Active Players) ────
// O card preenche a altura do quadrante (ver GoldPriceCard/ActivePlayersCard),
// e essa altura varia (300px mínimo, mas cresce se o card vizinho for mais
// alto) — um viewBox FIXO com preserveAspectRatio="none" pra preencher isso
// esticava o desenho de forma não-uniforme (X e Y com escalas diferentes),
// deixando tudo "espremido" (texto, círculos, tudo distorcido). Em vez
// disso, viewBox = tamanho REAL medido do container (useElementSize) → 1
// unidade de viewBox = 1px real, escala sempre 1:1, sem distorção nenhuma.
// PAD_* já são pixels reais nesse sistema (não proporção de uma caixa
// lógica de 320×115). PAD_L/PAD_R quase simétricos de propósito — o rótulo
// de valor é ancorado à direita (textAnchor="end"), não precisa de uma
// margem reservada enorme, e uma diferença grande puxa a linha visivelmente
// pra esquerda (reportado).
const PAD_L = 32, PAD_R = 48, PAD_TOP = 16, PAD_BOT = 32;

function chartLayout(w: number, h: number) {
  const plotW = Math.max(w - PAD_L - PAD_R, 1);
  const plotH = Math.max(h - PAD_TOP - PAD_BOT, 1);
  return { plotW, plotH, baseY: PAD_TOP + plotH };
}

// Mede o container em pixels reais (ResizeObserver) — usado pra dar ao
// gráfico um viewBox que bate exatamente com o tamanho renderizado, sem
// precisar esticar/distorcer nada. Default 320×160 é só o primeiro frame,
// antes do observer disparar a primeira medição.
function useElementSize<T extends Element>(): [React.RefObject<T>, { w: number; h: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 320, h: 160 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setSize({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

type ChartRange = "all" | "1y" | "6m" | "1m";
function useRanges(): { key: ChartRange; label: string }[] {
  const t = useT();
  return [
    { key: "1m", label: t("range1m") },
    { key: "6m", label: t("range6m") },
    { key: "1y", label: t("range1y") },
    { key: "all", label: t("rangeAll") },
  ];
}

type ChartPoint = { t: number; v: number };
type ChartSeriesInput = { key: string; label: string; color: string; points: ChartPoint[]; primary: boolean };

// passo "redondo" de grade horizontal quando o chamador não fixa um (preço
// de ouro usa um fixo de 5000; contagem de jogadores varia demais pra isso).
function niceGridStep(range: number): number {
  const raw = range / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const norm = raw / mag;
  const mult = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
  return mult * mag;
}

function ChartSkeleton() {
  const t = useT();
  const [ref, { w, h }] = useElementSize<HTMLDivElement>();
  const { plotW, baseY } = chartLayout(w, h);
  return (
    <div ref={ref} className="h-full">
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="100%" style={{ display: "block", overflow: "visible" }}>
        {Array.from({ length: 6 }, (_, i) => (
          <line key={i} x1={PAD_L + (i / 5) * plotW} x2={PAD_L + (i / 5) * plotW} y1={PAD_TOP} y2={baseY}
            stroke="var(--border)" strokeWidth="0.5" opacity="0.5" />
        ))}
        <text x={w / 2} y={h / 2} textAnchor="middle" fontSize="12" fill="var(--muted)">{t("loading")}</text>
      </svg>
    </div>
  );
}

// Arraste horizontal no gráfico = zoom no trecho selecionado (limpa com o
// botão "resetar zoom"). `key={range}` no chamador remonta o componente
// (e descarta o zoom) toda vez que o range muda — mais simples que
// sincronizar zoom com range via effect.
function nearestChartPoint(points: ChartPoint[], t: number): ChartPoint | null {
  if (!points.length) return null;
  let best = points[0], bestDist = Math.abs(points[0].t - t);
  for (const p of points) {
    const d = Math.abs(p.t - t);
    if (d < bestDist) { best = p; bestDist = d; }
  }
  return best;
}

// Zoom é controlado pelo card pai (vira o botão "resetar" no lugar dos
// filtros de período — ver GoldPriceCard/ActivePlayersCard); arrasto e hover
// continuam internos, são só interação transitória do próprio gráfico.
function MultiLineChart({ series, formatValue, gridStep, zoom, onZoomChange }: {
  series: ChartSeriesInput[]; formatValue: (v: number) => string; gridStep?: number;
  zoom: { t0: number; t1: number } | null; onZoomChange: (z: { t0: number; t1: number } | null) => void;
}) {
  const t = useT();
  const [svgRef, { w, h }] = useElementSize<SVGSVGElement>();
  const { plotW, plotH, baseY } = chartLayout(w, h);
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  const allPoints = useMemo(() => series.flatMap(s => s.points), [series]);
  if (allPoints.length < 1) {
    return <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{t("noDataForPeriod")}</div>;
  }

  const fullMinT = Math.min(...allPoints.map(p => p.t));
  const fullMaxT = Math.max(...allPoints.map(p => p.t));
  const minT = zoom ? Math.max(zoom.t0, fullMinT) : fullMinT;
  const maxT = zoom ? Math.min(zoom.t1, fullMaxT) : fullMaxT;
  const tSpan = Math.max(maxT - minT, 1);

  const visible = series.map(s => ({ ...s, points: s.points.filter(p => p.t >= minT && p.t <= maxT) }));
  const values = visible.flatMap(s => s.points.map(p => p.v));
  if (!values.length) {
    return <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{t("noDataForPeriod")}</div>;
  }
  const vLo = Math.min(...values), vHi = Math.max(...values);
  const vPad = Math.max((vHi - vLo) * 0.12, 1);
  const yLo = vLo - vPad, yHi = vHi + vPad, yRange = Math.max(yHi - yLo, 1);

  const cx = (t: number) => PAD_L + ((t - minT) / tSpan) * plotW;
  const cy = (v: number) => PAD_TOP + (1 - (v - yLo) / yRange) * plotH;
  const path = (pts: ChartPoint[]) => pts.map((p, i) => `${i === 0 ? "M" : "L"}${cx(p.t).toFixed(1)},${cy(p.v).toFixed(1)}`).join(" ");

  const step = gridStep ?? niceGridStep(yRange);
  const gridLines: number[] = [];
  for (let v = Math.ceil(yLo / step) * step; v <= yHi; v += step) gridLines.push(v);

  function viewBoxX(clientX: number): number {
    const rect = svgRef.current!.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)) * w;
  }
  function xToT(x: number): number {
    const frac = Math.min(1, Math.max(0, (x - PAD_L) / plotW));
    return minT + frac * tSpan;
  }
  function handleDown(e: React.MouseEvent<SVGSVGElement>) {
    e.preventDefault(); // sem isso o arrasto seleciona o texto da página inteira (título, legendas etc.)
    const x = viewBoxX(e.clientX);
    setDrag({ x0: x, x1: x });
  }
  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const x = viewBoxX(e.clientX);
    if (drag) setDrag(d => d && { ...d, x1: x });
    else setHoverX(x);
  }
  function handleUp() {
    setHoverX(null);
    if (!drag) return;
    const xa = Math.min(drag.x0, drag.x1), xb = Math.max(drag.x0, drag.x1);
    setDrag(null);
    if (xb - xa < 6) return; // arrasto curto demais = clique, ignora
    onZoomChange({ t0: xToT(xa), t1: xToT(xb) });
  }
  function handleContextMenu(e: React.MouseEvent<SVGSVGElement>) {
    if (!zoom) return; // sem zoom ativo, deixa o menu de contexto normal do navegador
    e.preventDefault();
    onZoomChange(null);
  }

  const ordered = [...visible].sort((a, b) => (a.primary ? 1 : 0) - (b.primary ? 1 : 0));

  // Rótulos de série à esquerda são ancorados no 1º ponto de cada linha —
  // séries com valores iniciais próximos (Americas/Europa/Ásia) empilhavam os
  // textos ilegíveis uns sobre os outros. Afasta verticalmente até um vão
  // mínimo, empurrando de volta pra cima se estourar a base do gráfico.
  const labelYs = (() => {
    const MIN_GAP = 13;
    const desired = ordered.filter(s => s.points.length)
      .map(s => ({ key: s.key, y: cy(s.points[0].v) + 4 }))
      .sort((a, b) => a.y - b.y);
    for (let i = 1; i < desired.length; i++) {
      if (desired[i].y - desired[i - 1].y < MIN_GAP) desired[i].y = desired[i - 1].y + MIN_GAP;
    }
    const over = desired.length ? desired[desired.length - 1].y - (baseY + 4) : 0;
    if (over > 0) for (const d of desired) d.y -= over;
    return new Map(desired.map(d => [d.key, d.y]));
  })();

  // tooltip de hover: ponto mais próximo do mouse em CADA série (podem ter
  // timestamps levemente diferentes entre si — cada uma foi reduzida por
  // downsample independentemente), agrupados sob o horário da série primária.
  const hoverT = drag || hoverX === null ? null : xToT(hoverX);
  const hoverAnchor = hoverT === null ? null : nearestChartPoint(
    visible.find(s => s.primary)?.points ?? visible[0]?.points ?? [], hoverT,
  );
  const hoverRows = hoverT === null ? [] : visible
    .map(s => ({ s, p: nearestChartPoint(s.points, hoverT) }))
    .filter((r): r is { s: typeof visible[number]; p: ChartPoint } => r.p !== null);

  return (
    <div className="relative h-full">
      <svg
        ref={svgRef} viewBox={`0 0 ${w} ${h}`} width="100%" height="100%"
        style={{ display: "block", overflow: "visible", cursor: "crosshair", userSelect: "none" }}
        onMouseDown={handleDown} onMouseMove={handleMove} onMouseUp={handleUp} onMouseLeave={handleUp}
        onContextMenu={handleContextMenu}
      >
        {Array.from({ length: 6 }, (_, i) => (
          <line key={i} x1={PAD_L + (i / 5) * plotW} x2={PAD_L + (i / 5) * plotW} y1={PAD_TOP} y2={baseY}
            stroke="var(--border)" strokeWidth="0.5" opacity="0.5" />
        ))}
        {gridLines.map(v => (
          <line key={v} x1={PAD_L} x2={PAD_L + plotW} y1={cy(v)} y2={cy(v)}
            stroke="var(--border)" strokeWidth="0.5" opacity="0.5" />
        ))}
        {[0, 1, 2, 3].map(i => {
          const tt = minT + (i / 3) * tSpan;
          return (
            <text key={i} x={cx(tt)} y={baseY + 16} textAnchor="middle" fontSize="10" fill="var(--muted)">
              {dateUTC(new Date(tt).toISOString())}
            </text>
          );
        })}
        {drag && (
          <rect x={Math.min(drag.x0, drag.x1)} y={PAD_TOP} width={Math.abs(drag.x1 - drag.x0)} height={plotH}
            fill="var(--gold, #f59e0b)" opacity={0.15} />
        )}
        {ordered.map(s => {
          // Cada série é filtrada/downsample de forma independente — uma
          // pode ficar sem NENHUM ponto no trecho visível (zoom estreito,
          // ou região com histórico mais curto) mesmo com outras series
          // cheias. Sem esse guard, s.points[-1] vira undefined e
          // cx(last.t) explode (TypeError não tratado = tela em branco).
          if (!s.points.length) return null;
          const first = s.points[0], last = s.points[s.points.length - 1];
          return (
            <g key={s.key} opacity={s.primary ? 1 : 0.3}>
              <path d={path(s.points)} fill="none" stroke={s.color} strokeWidth={s.primary ? 1.8 : 1.2} strokeLinejoin="round" />
              <circle cx={cx(last.t)} cy={cy(last.v)} r={s.primary ? 1.6 : 1.4} fill={s.color} />
              <text x={2} y={labelYs.get(s.key) ?? cy(first.v) + 4} fontSize="11" fontWeight="700" fill={s.color}>{s.label}</text>
              {s.primary && (
                <text x={w - 2} y={cy(last.v) + 4} textAnchor="end" fontSize="11" fontWeight="700" fill={s.color}>
                  {formatValue(last.v)}
                </text>
              )}
            </g>
          );
        })}
        {hoverAnchor && (
          <g pointerEvents="none">
            <line x1={cx(hoverAnchor.t)} x2={cx(hoverAnchor.t)} y1={PAD_TOP} y2={baseY}
              stroke="var(--muted)" strokeWidth="0.4" strokeDasharray="2,2" opacity="0.7" />
            {hoverRows.map(({ s, p }) => (
              <circle key={s.key} cx={cx(p.t)} cy={cy(p.v)} r={2.2} fill={s.color} stroke="#000" strokeWidth={0.5} />
            ))}
            {(() => {
              const boxW = 112, boxH = 20 + hoverRows.length * 14;
              const rawX = cx(hoverAnchor.t) + 5;
              const boxX = rawX + boxW > w ? cx(hoverAnchor.t) - 5 - boxW : rawX;
              const boxY = Math.min(Math.max(PAD_TOP, cy(hoverAnchor.v) - boxH / 2), baseY - boxH);
              const iso = new Date(hoverAnchor.t).toISOString();
              return (
                <g transform={`translate(${boxX},${boxY})`}>
                  <rect width={boxW} height={boxH} fill="#18181b" stroke="var(--border)" strokeWidth="0.5" opacity="0.95" />
                  <text x={7} y={14} fontSize="10" fontWeight="700" fill="var(--text, #e4e4e7)">
                    {dateUTC(iso)} {iso.slice(11, 16)} UTC
                  </text>
                  {hoverRows.map(({ s, p }, i) => (
                    <g key={s.key} transform={`translate(7,${26 + i * 14})`}>
                      <circle cx={2.5} cy={-3.5} r={2.5} fill={s.color} />
                      <text x={9} y={0} fontSize="10" fill="var(--muted)">{s.label}: </text>
                      <text x={9 + s.label.length * 5.6 + 8} y={0} fontSize="10" fontWeight="700" fill="var(--text, #e4e4e7)">
                        {formatValue(p.v)}
                      </text>
                    </g>
                  ))}
                </g>
              );
            })()}
          </g>
        )}
      </svg>
    </div>
  );
}

// ── Gold price chart ────────────────────────────────────────────────────
// Histórico próprio (services/gold_price.py, backend) — antes buscava direto
// da AODP no browser por servidor; agora um fetch só no nosso banco (backfill
// completo desde 2017 + poll periódico, não depende de quanto tempo a AODP
// externa continuar no ar).
const SERVERS: GameServer[] = ["europe", "west", "east"];
const SERVER_COLORS: Record<GameServer, string> = { europe: "#38bdf8", west: "#34d399", east: "#fbbf24" };

type GoldPoint = { t: number; price: number };
type GoldHistory = Record<string, GoldPoint[]>; // chave = region (americas|europe|asia), ver SERVER_TO_REGION

async function fetchGoldHistory(range: ChartRange): Promise<GoldHistory> {
  const res = await fetch(`${BATTLES_API}/battles/gold/history?range=${range}`);
  if (!res.ok) throw new Error(`gold-history ${res.status}`);
  const d: { series: GoldHistory } = await res.json();
  return d.series;
}

// O período "Tudo" pode chegar a centenas de pontos (o backend já faz bucket
// a ≤400/região) — reduz mais um pouco pra não perder a forma da curva,
// fazendo a média de cada bucket. No estado normal isso é um no-op de
// segurança: a redução pesada já aconteceu no servidor.
const TARGET_POINTS = 150;
function downsample<T extends { t: number }>(points: T[], avg: (bucket: T[]) => Omit<T, "t">): T[] {
  if (points.length <= TARGET_POINTS) return points;
  const bucketSize = points.length / TARGET_POINTS;
  const out: T[] = [];
  for (let i = 0; i < TARGET_POINTS; i++) {
    const start = Math.floor(i * bucketSize);
    const end = Math.max(start + 1, Math.floor((i + 1) * bucketSize));
    const bucket = points.slice(start, end);
    if (!bucket.length) continue;
    out.push({ t: bucket[Math.floor(bucket.length / 2)].t, ...avg(bucket) } as T);
  }
  return out;
}

function downsampleGold(points: GoldPoint[]): GoldPoint[] {
  return downsample(points, bucket => ({ price: Math.round(bucket.reduce((s, p) => s + p.price, 0) / bucket.length) }));
}

function GoldPriceCard() {
  const t = useT();
  const RANGES = useRanges();
  const { server: primary } = useLang();
  const [range, setRange] = useState<ChartRange>("6m");
  const [data, setData] = useState<GoldHistory | null>(null);
  const [error, setError] = useState(false);
  const lastTRef = useRef<number>(0);
  const [chartGlowing, setChartGlowing] = useState(false);
  const [zoom, setZoom] = useState<{ t0: number; t1: number } | null>(null);

  function selectRange(r: ChartRange) {
    setRange(r);
    setZoom(null);
  }

  useEffect(() => {
    function fetchRange(isRefresh = false) {
      setError(false);
      fetchGoldHistory(range)
        .then(series => {
          const pts = series[SERVER_TO_REGION[primary]] ?? [];
          const lastT = pts[pts.length - 1]?.t ?? 0;
          if (isRefresh && lastT > lastTRef.current) setChartGlowing(true);
          lastTRef.current = lastT;
          setData(series);
        })
        .catch(() => setError(true));
    }

    fetchRange();
    const timer = setInterval(() => fetchRange(true), 300_000);
    return () => clearInterval(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  const series: ChartSeriesInput[] | null = data ? [...SERVERS]
    .sort((a, b) => (a === primary ? 1 : 0) - (b === primary ? 1 : 0))
    .map(s => ({
      key: s, label: SERVER_LABELS[s], color: SERVER_COLORS[s], primary: s === primary,
      points: downsampleGold(data[SERVER_TO_REGION[s]] ?? []).map(p => ({ t: p.t, v: p.price })),
    })) : null;

  return (
    <Panel className="flex h-full min-h-[300px] flex-col p-4">
      <PanelHeader title={t("goldPriceTitle")}>
        <div className="flex gap-1">
          {zoom ? (
            <button onClick={() => setZoom(null)} className="dash-chip dash-chip-on">
              ↺ {t("resetZoom")}
            </button>
          ) : RANGES.map(r => (
            <button
              key={r.key}
              onClick={() => selectRange(r.key)}
              className={`dash-chip${r.key === range ? " dash-chip-on" : ""}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </PanelHeader>
      {error && <div className="flex flex-1 items-center justify-center text-center text-sm text-red-400">{t("goldPriceError")}</div>}
      {!error && !series && <div className="min-h-0 flex-1"><ChartSkeleton /></div>}
      {!error && series && (
        <div
          className={`min-h-0 flex-1${chartGlowing ? " dash-glow rounded-lg" : ""}`}
          onAnimationEnd={() => setChartGlowing(false)}
        >
          <MultiLineChart key={range} series={series} formatValue={silver} gridStep={5000} zoom={zoom} onZoomChange={setZoom} />
        </div>
      )}
    </Panel>
  );
}

// ── Batalhas recentes ───────────────────────────────────────────────────
// API local (não a do Albion) — mesma rota e mesma linha visual da página
// de batalhas (heatmap, jogadores por facção, data), só com o filtro padrão
// fixo e sem busca/data (pedido explícito: nada de filtro aqui).
const BATTLES_API = import.meta.env.DEV ? "http://localhost:8000" : "";
const SERVER_TO_REGION: Record<GameServer, string> = { west: "americas", east: "asia", europe: "europe" };
const RECENT_BATTLES_LIMIT = 5;
const DEFAULT_MIN_PLAYERS = 5;
const DEFAULT_MIN_KILLS = 5;

const STAGGER_MS = 350;

function BattlesCard({ onSeeAll }: { onSeeAll: () => void }) {
  const t = useT();
  const { servers } = useLang();
  const [battles, setBattles] = useState<RecentBattle[] | null>(null);
  const knownIdsRef = useRef<Set<string>>(new Set());
  const insertQueueRef = useRef<RecentBattle[]>([]);
  const insertTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());

  function drainInsertQueue() {
    if (!insertQueueRef.current.length) {
      clearInterval(insertTimerRef.current!);
      insertTimerRef.current = null;
      return;
    }
    const next = insertQueueRef.current.shift()!;
    knownIdsRef.current.add(next.public_id);
    setBattles(prev =>
      prev ? [next, ...prev.filter(b => b.public_id !== next.public_id)].slice(0, RECENT_BATTLES_LIMIT) : [next]
    );
    setNewIds(prev => new Set([...prev, next.public_id]));
  }

  useEffect(() => {
    // cleanup stagger timer on unmount / server change
    return () => { clearInterval(insertTimerRef.current!); };
  }, [servers]);

  useEffect(() => {
    function load() {
      const params = new URLSearchParams({
        limit: String(RECENT_BATTLES_LIMIT),
        offset: "0",
        min_players: String(DEFAULT_MIN_PLAYERS),
        min_kills: String(DEFAULT_MIN_KILLS),
        regions: servers.map(s => SERVER_TO_REGION[s]).join(","),
      });
      fetch(`${BATTLES_API}/battles?${params}`)
        .then(r => r.json())
        .then((data: { battles: RecentBattle[] }) => {
          const list = data.battles;
          if (!knownIdsRef.current.size) {
            // carga inicial — bulk direto, sem animação
            knownIdsRef.current = new Set(list.map(b => b.public_id));
            setBattles(list);
            return;
          }
          const newOnes = list.filter(b => !knownIdsRef.current.has(b.public_id));
          list.forEach(b => knownIdsRef.current.add(b.public_id));
          if (!newOnes.length) {
            // nada novo — atualiza dados existentes silenciosamente
            setBattles(list);
            return;
          }
          // enfileira do mais antigo pro mais novo (API retorna mais novo primeiro)
          // assim o mais novo termina no topo depois de todos os drains
          insertQueueRef.current.push(...[...newOnes].reverse());
          if (!insertTimerRef.current) {
            drainInsertQueue();
            if (insertQueueRef.current.length) {
              insertTimerRef.current = setInterval(drainInsertQueue, STAGGER_MS);
            }
          }
        })
        .catch(() => setBattles([]));
    }
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [servers]);

  return (
    <Panel className="p-4">
      <PanelHeader title={t("recentBattlesTitle")} action={t("seeAllBattlesLink")} onAction={onSeeAll} />
      {battles === null && <div className="py-8 text-center text-sm text-zinc-500">{t("loading")}</div>}
      {battles?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">{t("noBattlesFound")}</div>}
      <div className="space-y-2">
        {battles?.map(b => (
          <RecentBattleRow
            key={b.public_id}
            b={b}
            isNew={newIds.has(b.public_id)}
            onGlowEnd={() => setNewIds(prev => { const n = new Set(prev); n.delete(b.public_id); return n; })}
          />
        ))}
      </div>
    </Panel>
  );
}

// ── Dashboard ───────────────────────────────────────────────────────────
// Indicador do app desktop — leva pra página /download (download + features).
function CompanionStrip() {
  const t = useT();
  return (
    <Panel className="flex items-center gap-4 p-4">
      <i className="ti ti-device-desktop" style={{ fontSize: 28, color: "var(--gold)" }} />
      <div className="min-w-0 flex-1">
        <div className="font-semibold">{t("companionPromoTitle")}</div>
        <div className="text-sm text-zinc-400">{t("companionPromoText")}</div>
      </div>
      <button className="btn" onClick={() => navigate("/download")}>
        <i className="ti ti-download" /> {t("companionPromoBtn")}
      </button>
    </Panel>
  );
}

export default function Dashboard({ onOpenBattles, onOpenHighscores }: {
  onOpenBattles: () => void; onOpenHighscores: () => void;
}) {
  const [showAllPatchNotes, setShowAllPatchNotes] = useState(false);

  if (showAllPatchNotes) return <PatchNotesPage onBack={() => setShowAllPatchNotes(false)} />;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <GlobalSearch />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ActivePlayersCard />
        <GoldPriceCard />
      </div>
      <div className="mt-4">
        <BattlesCard onSeeAll={onOpenBattles} />
      </div>
      <div className="mt-4">
        <CompanionStrip />
      </div>
      <div className="mt-4 flex flex-col items-center gap-2">
        <AdBanner slot="dashboard" variant="leaderboard" mobileVariant="mobileBanner" />
        <AffiliateBanner variant="leaderboard" />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ServerHighlightsCard onSeeAll={onOpenHighscores} />
        <PatchNotesCard onSeeAll={() => setShowAllPatchNotes(true)} />
      </div>
    </div>
  );
}
