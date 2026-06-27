import { useEffect, useMemo, useRef, useState } from "react";
import { useServer, SERVER_LABELS, type GameServer } from "../i18n";
import { silver, dateUTC } from "../lib/format";
import { navigate } from "../router";

// ── Busca global ─────────────────────────────────────────────────────────
interface SearchPlayer   { albion_id: string; name: string; guild_name: string | null; alliance_name: string | null; battles: number; region: string }
interface SearchGuild    { albion_id: string; name: string; alliance_name: string | null; battles: number }
interface SearchAlliance { albion_id: string; name: string; guild_count: number; battles: number }
// SearchBattle é compatível com RecentBattle — mesmos campos, reutiliza RecentBattleRow
type SearchBattle = RecentBattle;
interface SearchResults  { players: SearchPlayer[]; guilds: SearchGuild[]; alliances: SearchAlliance[]; battles: SearchBattle[] }

const SEARCH_REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
      {label} <span className="text-zinc-600">· {count}</span>
    </p>
  );
}

function SearchInitial({ char, className }: { char: string; className?: string }) {
  return (
    <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-sm font-bold text-zinc-400 ${className ?? ""}`}>
      {char.toUpperCase()}
    </div>
  );
}

function SearchCard({ onClick, left, title, sub, meta, delay }: {
  onClick: () => void; left: React.ReactNode;
  title: string; sub?: string; meta: string; delay: number;
}) {
  return (
    <button onClick={onClick} style={{ animationDelay: `${delay}ms` }}
      className="search-row w-full rounded-xl border border-zinc-800 bg-zinc-900/50 px-4 py-3 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-900">
      <div className="flex items-center gap-3">
        {left}
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-zinc-100">{title}</div>
          {sub && <div className="truncate text-xs text-zinc-500">{sub}</div>}
        </div>
        <span className="shrink-0 whitespace-nowrap text-xs text-zinc-500 tabular-nums">{meta}</span>
      </div>
    </button>
  );
}

function GlobalSearch() {
  const [query, setQuery]   = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  // key muda a cada resultado novo → React remonta o container → animação retrigger
  const [animKey, setAnimKey] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < 2) { setResults(null); setLoading(false); return; }
    setLoading(true);
    timer.current = setTimeout(() => {
      fetch(`${BATTLES_API}/public/search?q=${encodeURIComponent(q.trim())}`)
        .then(r => r.json())
        .then((d: SearchResults) => { setResults(d); setLoading(false); setAnimKey(k => k + 1); })
        .catch(() => setLoading(false));
    }, 300);
  }

  const active  = query.trim().length >= 2;
  const total   = results ? results.players.length + results.guilds.length + results.alliances.length + results.battles.length : 0;

  // índice global para delay de stagger entre todas as linhas
  let rowIdx = 0;
  function nextDelay() { return rowIdx++ * 35; }

  return (
    <div className="mb-6">
      {/* input */}
      <div className="relative">
        <i className="ti ti-search absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500" />
        <input type="text" value={query} onChange={handleChange}
          placeholder="Buscar jogador, guilda, aliança ou batalha…"
          className="w-full rounded-xl border border-zinc-700 bg-zinc-900/60 py-3 pl-10 pr-10 text-sm text-zinc-100 placeholder-zinc-600 transition-colors focus:border-amber-500/50 focus:outline-none focus:ring-1 focus:ring-amber-500/20" />
        {loading && <i className="ti ti-loader-2 absolute right-3.5 top-1/2 -translate-y-1/2 animate-spin text-zinc-500" />}
        {!loading && active && (
          <button onClick={() => { setQuery(""); setResults(null); }}
            className="absolute right-3.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400">
            <i className="ti ti-x text-xs" />
          </button>
        )}
      </div>

      {/* resultados */}
      {active && !loading && results && (
        <div key={animKey} className="search-expand mt-3 space-y-5">
          {total === 0 ? (
            <p className="py-8 text-center text-sm text-zinc-600">
              Nenhum resultado para "{query}".
            </p>
          ) : (
            <>
              {results.players.length > 0 && (
                <div>
                  <SectionHeader label="Jogadores" count={results.players.length} />
                  <div className="space-y-2">
                    {results.players.map(p => (
                      <SearchCard key={p.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/${SEARCH_REGION_PREFIX[p.region] ?? "am"}/${encodeURIComponent(p.name)}`)}
                        left={<SearchInitial char={p.name[0]} />}
                        title={p.name}
                        sub={[p.alliance_name ? `[${p.alliance_name}]` : "", p.guild_name ?? ""].filter(Boolean).join(" ") || undefined}
                        meta={`${p.battles} lutas`} />
                    ))}
                  </div>
                </div>
              )}

              {results.guilds.length > 0 && (
                <div>
                  <SectionHeader label="Guildas" count={results.guilds.length} />
                  <div className="space-y-2">
                    {results.guilds.map(g => (
                      <SearchCard key={g.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/guild/${g.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-shield text-sm text-zinc-400" /></div>}
                        title={g.name}
                        sub={g.alliance_name ? `[${g.alliance_name}]` : undefined}
                        meta={`${g.battles} lutas`} />
                    ))}
                  </div>
                </div>
              )}

              {results.alliances.length > 0 && (
                <div>
                  <SectionHeader label="Alianças" count={results.alliances.length} />
                  <div className="space-y-2">
                    {results.alliances.map(a => (
                      <SearchCard key={a.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/alliance/${a.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-users text-sm text-zinc-400" /></div>}
                        title={a.name}
                        sub={`${a.guild_count} guilda${a.guild_count !== 1 ? "s" : ""}`}
                        meta={`${a.battles} lutas`} />
                    ))}
                  </div>
                </div>
              )}

              {results.battles.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    {results.players.length > 0
                      ? <>Batalhas recentes de <span className="text-zinc-300">{results.players.map(p => p.name).join(", ")}</span></>
                      : <>Batalhas <span className="text-zinc-600">· {results.battles.length}</span></>
                    }
                  </p>
                  <div className="space-y-2">
                    {results.battles.map((b, i) => (
                      <div key={b.public_id} className="search-row" style={{ animationDelay: `${nextDelay()}ms` }}>
                        <RecentBattleRow b={b} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

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
  const notes = usePatchNotes();
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <button onClick={onSeeAll} className="group mb-1 flex w-full items-center justify-between text-left">
        <h2 className="text-sm font-bold text-zinc-100 group-hover:text-amber-300">Patch Notes</h2>
        <span className="text-xs text-zinc-500 group-hover:text-amber-300">Ver todos →</span>
      </button>
      {notes === null && <div className="py-8 text-center text-sm text-zinc-500">Carregando…</div>}
      {notes?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">Changelog indisponível.</div>}
      <div className="divide-y divide-zinc-800/60">
        {notes?.slice(0, 10).map(n => <PatchNoteRow key={n.url} n={n} />)}
      </div>
    </div>
  );
}

function PatchNotesPage({ onBack }: { onBack: () => void }) {
  const notes = usePatchNotes();
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <button onClick={onBack} className="mb-4 text-sm text-zinc-400 hover:text-zinc-200">← Voltar</button>
      <h1 className="mb-4 text-lg font-bold text-zinc-100">Patch Notes — Albion Online</h1>
      {notes === null && <div className="py-16 text-center text-zinc-500">Carregando…</div>}
      <div className="divide-y divide-zinc-800/60 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4">
        {notes?.map(n => <PatchNoteRow key={n.url} n={n} />)}
      </div>
    </div>
  );
}

// ── Jogadores ativos (substitui patch notes na posição original) ───────
interface ActivePlayersStat { current: number; previous: number; delta_pct: number | null }
interface ActivePlayersData {
  americas: ActivePlayersStat; europe: ActivePlayersStat; asia: ActivePlayersStat; global: ActivePlayersStat;
}
const ACTIVE_PLAYERS_ROWS: { key: keyof ActivePlayersData; label: string }[] = [
  { key: "global", label: "Global" },
  { key: "americas", label: "Americas" },
  { key: "europe", label: "Europe" },
  { key: "asia", label: "Asia" },
];

function DeltaBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-xs text-zinc-600">—</span>;
  const up = pct >= 0;
  return (
    <span className={`text-xs font-semibold tabular-nums ${up ? "text-emerald-400" : "text-red-400"}`}>
      {up ? "▲" : "▼"} {Math.abs(pct)}%
    </span>
  );
}

function ActivePlayersCard() {
  const [data, setData] = useState<ActivePlayersData | null>(null);
  const prevRef = useRef<ActivePlayersData | null>(null);
  const [glowing, setGlowing] = useState<Set<string>>(new Set());

  useEffect(() => {
    function load() {
      fetch(`${BATTLES_API}/battles/active-players`)
        .then(r => r.json())
        .then((d: ActivePlayersData) => {
          if (prevRef.current) {
            const changed = ACTIVE_PLAYERS_ROWS
              .filter(r => d[r.key].current !== prevRef.current![r.key].current)
              .map(r => r.key);
            if (changed.length) setGlowing(prev => new Set([...prev, ...changed]));
          }
          prevRef.current = d;
          setData(d);
        })
        .catch(() => setData(null));
    }
    load();
    const timer = setInterval(load, 300_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="relative rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <i
        className="ti ti-info-circle absolute right-3 top-3 text-zinc-600"
        title="Dados dos últimos 7 dias, comparado à semana anterior."
      />
      <h2 className="mb-3 text-sm font-bold text-zinc-100">Personagens em Atividade</h2>
      {data === null ? (
        <div className="py-8 text-center text-sm text-zinc-500">Carregando…</div>
      ) : (
        <div className="divide-y divide-zinc-800/60">
          {ACTIVE_PLAYERS_ROWS.map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between gap-3 py-2.5">
              <span className="text-sm text-zinc-300">{label}</span>
              <div className="flex items-center gap-3">
                <span
                  className={`text-sm font-bold tabular-nums text-zinc-100${glowing.has(key) ? " dash-text-glow" : ""}`}
                  onAnimationEnd={() => setGlowing(prev => { const n = new Set(prev); n.delete(key); return n; })}
                >
                  {data[key].current.toLocaleString("pt-BR")}
                </span>
                <DeltaBadge pct={data[key].delta_pct} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Destaques do servidor ────────────────────────────────────────────────
interface HighlightPlayer {
  albion_player_id: string; name: string; guild_name: string | null; alliance_name: string | null;
  region: string; appearances: number;
}
const HIGHLIGHT_REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };

function HighlightRow({ p, rank, isGlowing, onGlowEnd }: {
  p: HighlightPlayer; rank: number; isGlowing?: boolean; onGlowEnd?: () => void;
}) {
  const prefix = HIGHLIGHT_REGION_PREFIX[p.region];
  return (
    <button
      onClick={() => prefix && navigate(`/${prefix}/${encodeURIComponent(p.name)}`)}
      className="flex w-full items-center gap-3 rounded px-1 py-2 text-left hover:bg-zinc-800/30"
    >
      <span className="w-5 shrink-0 text-center text-xs font-bold text-zinc-500">{rank}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-zinc-200">{p.name}</div>
        <div className="truncate text-[11px] text-zinc-600">
          {p.guild_name ?? "Sem guilda"}{p.alliance_name && <span className="ml-1">[{p.alliance_name}]</span>}
        </div>
      </div>
      <span
        className={`shrink-0 text-xs font-semibold text-amber-400/80 tabular-nums${isGlowing ? " dash-text-glow" : ""}`}
        onAnimationEnd={onGlowEnd}
      >
        {p.appearances} lutas
      </span>
    </button>
  );
}

function ServerHighlightsCard() {
  const { servers } = useServer();
  const [players, setPlayers] = useState<HighlightPlayer[] | null>(null);
  const prevRef = useRef<Map<string, number>>(new Map());
  const [glowing, setGlowing] = useState<Set<string>>(new Set());

  useEffect(() => {
    function load() {
      const regions = servers.map(s => SERVER_TO_REGION[s]).join(",");
      fetch(`${BATTLES_API}/battles/highlights?regions=${regions}`)
        .then(r => r.json())
        .then((d: { players: HighlightPlayer[] }) => {
          if (prevRef.current.size) {
            const changed = d.players
              .filter(p => prevRef.current.get(p.albion_player_id) !== p.appearances)
              .map(p => p.albion_player_id);
            if (changed.length) setGlowing(prev => new Set([...prev, ...changed]));
          }
          prevRef.current = new Map(d.players.map(p => [p.albion_player_id, p.appearances]));
          setPlayers(d.players);
        })
        .catch(() => setPlayers([]));
    }
    load();
    const timer = setInterval(load, 300_000);
    return () => clearInterval(timer);
  }, [servers]);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-bold text-zinc-100">Destaques do Servidor</h2>
        <span title="Considera apenas lutas dos últimos 7 dias (5+ jogadores, com cura)" className="flex h-4 w-4 cursor-default items-center justify-center rounded-full border border-zinc-600 text-[10px] text-zinc-400">i</span>
      </div>
      {players === null && <div className="py-8 text-center text-sm text-zinc-500">Carregando…</div>}
      {players?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">Sem dados suficientes ainda.</div>}
      <div className="divide-y divide-zinc-800/60">
        {players?.map((p, i) => (
        <HighlightRow
          key={p.albion_player_id}
          p={p}
          rank={i + 1}
          isGlowing={glowing.has(p.albion_player_id)}
          onGlowEnd={() => setGlowing(prev => { const n = new Set(prev); n.delete(p.albion_player_id); return n; })}
        />
      ))}
      </div>
    </div>
  );
}

// ── Gold price chart ────────────────────────────────────────────────────
// Endpoint dedicado do AODP pra taxa de câmbio prata↔ouro — não é um item
// comum, não passa pelos helpers de preço de item em lib/prices/adp.ts.
const GOLD_BASE: Record<GameServer, string> = {
  west: "https://west.albion-online-data.com",
  east: "https://east.albion-online-data.com",
  europe: "https://europe.albion-online-data.com",
};
const SERVERS: GameServer[] = ["europe", "west", "east"];
const SERVER_COLORS: Record<GameServer, string> = { europe: "#38bdf8", west: "#34d399", east: "#fbbf24" };

type GoldPoint = { t: number; price: number };
type GoldRange = "all" | "1y" | "6m" | "1m";
const RANGES: { key: GoldRange; label: string }[] = [
  { key: "1m", label: "1 mês" },
  { key: "6m", label: "6 meses" },
  { key: "1y", label: "1 ano" },
  { key: "all", label: "Tudo" },
];
// histórico de ouro existe desde dez/2017 (checado direto na API) — "Tudo"
// busca a partir daí.
const RANGE_START: Record<GoldRange, () => Date> = {
  "1m": () => new Date(Date.now() - 31 * 86_400_000),
  "6m": () => new Date(Date.now() - 183 * 86_400_000),
  "1y": () => new Date(Date.now() - 366 * 86_400_000),
  all: () => new Date("2017-01-01T00:00:00Z"),
};

function mmddyyyy(d: Date): string {
  return `${d.getUTCMonth() + 1}-${d.getUTCDate()}-${d.getUTCFullYear()}`;
}

async function fetchGoldHistory(server: GameServer, range: GoldRange): Promise<GoldPoint[]> {
  const date = mmddyyyy(RANGE_START[range]());
  const endDate = mmddyyyy(new Date());
  const url = `${GOLD_BASE[server]}/api/v2/stats/gold.json?date=${date}&end_date=${endDate}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`gold ${res.status}`);
  const rows: { price: number; timestamp: string }[] = await res.json();
  return rows
    .map(r => ({ t: Date.parse(r.timestamp.endsWith("Z") ? r.timestamp : `${r.timestamp}Z`), price: r.price }))
    .filter(p => Number.isFinite(p.t) && p.price > 0)
    .sort((a, b) => a.t - b.t);
}

// O período "Tudo" tem ~70k pontos horários — reduz pra uma quantidade
// renderizável sem perder a forma da curva, fazendo a média de cada bucket.
const TARGET_POINTS = 150;
function downsample(points: GoldPoint[]): GoldPoint[] {
  if (points.length <= TARGET_POINTS) return points;
  const bucketSize = points.length / TARGET_POINTS;
  const out: GoldPoint[] = [];
  for (let i = 0; i < TARGET_POINTS; i++) {
    const start = Math.floor(i * bucketSize);
    const end = Math.max(start + 1, Math.floor((i + 1) * bucketSize));
    const bucket = points.slice(start, end);
    if (!bucket.length) continue;
    out.push({
      t: bucket[Math.floor(bucket.length / 2)].t,
      price: Math.round(bucket.reduce((s, p) => s + p.price, 0) / bucket.length),
    });
  }
  return out;
}

// H mais baixo (era 160) — pra ficar do tamanho do quadrante de Personagens
// em Atividade ao lado, em vez de deixar um buraco vazio nele (pedido explícito).
const VW = 320, H = 115, PAD_L = 22, PAD_R = 56, PAD_TOP = 10, PAD_BOT = 22;
const PLOT_W = VW - PAD_L - PAD_R, PLOT_H = H - PAD_TOP - PAD_BOT, BASE_Y = PAD_TOP + PLOT_H;

function GoldChartSkeleton() {
  return (
    <svg viewBox={`0 0 ${VW} ${H}`} width="100%" style={{ display: "block", overflow: "visible" }}>
      {Array.from({ length: 6 }, (_, i) => (
        <line key={i} x1={PAD_L + (i / 5) * PLOT_W} x2={PAD_L + (i / 5) * PLOT_W} y1={PAD_TOP} y2={BASE_Y}
          stroke="var(--border)" strokeWidth="0.3" opacity="0.5" />
      ))}
      <text x={VW / 2} y={H / 2} textAnchor="middle" fontSize="10" fill="var(--muted)">Carregando…</text>
    </svg>
  );
}

function GoldPriceChart({ data, range, primary }: {
  data: Record<GameServer, GoldPoint[]>; range: GoldRange; primary: GameServer;
}) {
  const cutoff = RANGE_START[range]().getTime();
  const series = useMemo(() => SERVERS.map(s => ({
    server: s,
    points: downsample(data[s].filter(p => p.t >= cutoff)),
  })), [data, cutoff]);

  const allPoints = series.flatMap(s => s.points);
  if (allPoints.length < 2) {
    return <div className="flex h-40 items-center justify-center text-sm text-zinc-500">Sem dados pra esse período.</div>;
  }

  const minT = Math.min(...allPoints.map(p => p.t));
  const maxT = Math.max(...allPoints.map(p => p.t));
  const tSpan = Math.max(maxT - minT, 1);
  const prices = allPoints.map(p => p.price);
  const pLo = Math.min(...prices), pHi = Math.max(...prices);
  const pPad = Math.max((pHi - pLo) * 0.12, 1);
  const yLo = pLo - pPad, yHi = pHi + pPad, yRange = Math.max(yHi - yLo, 1);

  const cx = (t: number) => PAD_L + ((t - minT) / tSpan) * PLOT_W;
  const cy = (price: number) => PAD_TOP + (1 - (price - yLo) / yRange) * PLOT_H;
  const path = (pts: GoldPoint[]) => pts.map((p, i) => `${i === 0 ? "M" : "L"}${cx(p.t).toFixed(1)},${cy(p.price).toFixed(1)}`).join(" ");

  // servidor escolhido nas configurações desenhado por último (fica em cima
  // quando as linhas se cruzam) — os outros 2 servidores "disputam" junto,
  // todos sempre visíveis (pedido explícito), só com opacidade bem mais baixa
  // pra dar ênfase ao escolhido.
  const ordered = [...series].sort((a, b) => (a.server === primary ? 1 : 0) - (b.server === primary ? 1 : 0));

  const GOLD_GRID_STEP = 5000;
  const goldGridLines: number[] = [];
  for (let p = Math.ceil(yLo / GOLD_GRID_STEP) * GOLD_GRID_STEP; p <= yHi; p += GOLD_GRID_STEP) {
    goldGridLines.push(p);
  }

  return (
    <svg viewBox={`0 0 ${VW} ${H}`} width="100%" style={{ display: "block", overflow: "visible" }}>
      {Array.from({ length: 6 }, (_, i) => (
        <line key={i} x1={PAD_L + (i / 5) * PLOT_W} x2={PAD_L + (i / 5) * PLOT_W} y1={PAD_TOP} y2={BASE_Y}
          stroke="var(--border)" strokeWidth="0.3" opacity="0.5" />
      ))}
      {goldGridLines.map(p => (
        <line key={p} x1={PAD_L} x2={PAD_L + PLOT_W} y1={cy(p)} y2={cy(p)}
          stroke="var(--border)" strokeWidth="0.3" opacity="0.5" />
      ))}
      {[0, 1, 2, 3].map(i => {
        const t = minT + (i / 3) * tSpan;
        return (
          <text key={i} x={cx(t)} y={BASE_Y + 14} textAnchor="middle" fontSize="8" fill="var(--muted)">
            {dateUTC(new Date(t).toISOString())}
          </text>
        );
      })}
      {ordered.map(({ server, points }) => {
        const color = SERVER_COLORS[server];
        const isPrimary = server === primary;
        const first = points[0], last = points[points.length - 1];
        return (
          <g key={server} opacity={isPrimary ? 1 : 0.3}>
            <path d={path(points)} fill="none" stroke={color} strokeWidth={isPrimary ? 1.8 : 1.2} strokeLinejoin="round" />
            <text x={2} y={cy(first.price) + 3} fontSize="9" fontWeight="700" fill={color}>{SERVER_LABELS[server]}</text>
            {isPrimary && (
              <text x={PAD_L + PLOT_W + 4} y={cy(last.price) + 3} fontSize="9" fontWeight="700" fill={color}>
                {silver(last.price)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function GoldPriceCard() {
  const { server: primary } = useServer();
  const [range, setRange] = useState<GoldRange>("6m");
  const [cache, setCache] = useState<Partial<Record<GoldRange, Record<GameServer, GoldPoint[]>>>>({});
  const [error, setError] = useState(false);
  const lastTRef = useRef<number>(0);
  const [chartGlowing, setChartGlowing] = useState(false);

  useEffect(() => {
    function fetchRange(isRefresh = false) {
      setError(false);
      Promise.all(SERVERS.map(s => fetchGoldHistory(s, range).catch(() => [] as GoldPoint[])))
        .then(results => {
          const byServer = Object.fromEntries(SERVERS.map((s, i) => [s, results[i]])) as Record<GameServer, GoldPoint[]>;
          if (isRefresh) {
            const pts = byServer[primary];
            const lastT = pts[pts.length - 1]?.t ?? 0;
            if (lastT > lastTRef.current) setChartGlowing(true);
            lastTRef.current = lastT;
          } else {
            const pts = byServer[primary];
            lastTRef.current = pts[pts.length - 1]?.t ?? 0;
          }
          setCache(prev => ({ ...prev, [range]: byServer }));
        })
        .catch(() => setError(true));
    }

    fetchRange();
    const timer = setInterval(() => fetchRange(true), 300_000);
    return () => clearInterval(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  const data = cache[range];

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-zinc-100">Preço do Ouro</h2>
        <div className="flex gap-1 text-xs">
          {RANGES.map(r => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              className={`rounded-lg px-2 py-1 ${r.key === range ? "bg-amber-500/10 text-amber-300 border border-amber-500" : "text-zinc-400 hover:text-zinc-200 border border-transparent"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {error && <div className="py-8 text-center text-sm text-red-400">Não foi possível carregar o preço do ouro.</div>}
      {!error && !data && <GoldChartSkeleton />}
      {!error && data && (
        <div
          className={chartGlowing ? "dash-glow rounded-lg" : ""}
          onAnimationEnd={() => setChartGlowing(false)}
        >
          <GoldPriceChart data={data} range={range} primary={primary} />
        </div>
      )}
    </div>
  );
}

// ── Batalhas recentes ───────────────────────────────────────────────────
// API local (não a do Albion) — mesma rota e mesma linha visual da página
// de batalhas (heatmap, jogadores por facção, data), só com o filtro padrão
// fixo e sem busca/data (pedido explícito: nada de filtro aqui).
const BATTLES_API = import.meta.env.DEV ? "http://localhost:8000" : "";
const SERVER_TO_REGION: Record<GameServer, string> = { west: "americas", east: "asia", europe: "europe" };
const BATTLE_REGION_LABELS: Record<string, string> = { americas: "Americas", europe: "Europe", asia: "Asia" };
const RECENT_BATTLES_LIMIT = 5;
const DEFAULT_MIN_PLAYERS = 5;
const DEFAULT_MIN_KILLS = 5;

interface RecentFaction { guild_id: string; guild_name: string; alliance_name: string | null; kills: number; player_count: number }
interface RecentBattle {
  public_id: string; region: string; start_time: string;
  total_fame: number; kill_count: number; cluster: string | null;
  factions: RecentFaction[];
}

// Heatmap de kills — mesmas cores/lógica da listagem de batalhas (ver BattleTracker.tsx).
const BATTLE_HEAT_MAX: [number, number, number] = [0x66, 0x71, 0x60];
const BATTLE_HEAT_MIN: [number, number, number] = [0x52, 0x52, 0x5c];

function battleHeatColor(kills: number, maxKills: number, minKills: number): string {
  const t = maxKills === minKills ? 0 : (maxKills - kills) / (maxKills - minKills);
  const [r, g, b] = BATTLE_HEAT_MAX.map((c, i) => Math.round(c + (BATTLE_HEAT_MIN[i] - c) * t));
  return `rgb(${r}, ${g}, ${b})`;
}

function battleFactionTag(f: RecentFaction): string {
  return f.alliance_name ? `[${f.alliance_name}]` : f.guild_name;
}

function battleFameShort(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return n > 0 ? String(n) : "—";
}

function battleTimeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}m atrás`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h atrás`;
  return `${Math.floor(h / 24)}d atrás`;
}

/** dd/mm hh:mm UTC, ou dd/mm/yy hh:mm se for de um ano anterior ao atual (UTC). */
function battleDateTimeUTC(ts: string): string {
  const d = new Date(ts);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const min = String(d.getUTCMinutes()).padStart(2, "0");
  const isOld = d.getUTCFullYear() !== new Date().getUTCFullYear();
  if (isOld) {
    const yy = String(d.getUTCFullYear()).slice(-2);
    return `${dd}/${mm}/${yy} ${hh}:${min}`;
  }
  return `${dd}/${mm} ${hh}:${min} UTC`;
}

function RecentBattleRow({ b, isNew, onGlowEnd }: { b: RecentBattle; isNew?: boolean; onGlowEnd?: () => void }) {
  const maxFactionKills = b.factions.length ? Math.max(...b.factions.map(f => f.kills)) : 0;
  const minFactionKills = b.factions.length ? Math.min(...b.factions.map(f => f.kills)) : 0;
  return (
    <a
      href={`/${b.public_id}`}
      onClick={e => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(`/${b.public_id}`);
      }}
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/40${isNew ? " dash-glow" : ""}`}
      onAnimationEnd={onGlowEnd}
    >
      <span className="w-24 shrink-0 flex flex-col leading-tight">
        <span className="text-[10px] uppercase tracking-wide text-zinc-600">
          {BATTLE_REGION_LABELS[b.region] ?? b.region}
        </span>
        <span className="text-xs text-zinc-500 tabular-nums">{battleDateTimeUTC(b.start_time)}</span>
      </span>

      <span className="flex-1 min-w-0 text-center">
        {b.factions.length > 0 ? (
          <span className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-sm font-semibold">
            {b.factions.map(f => (
              <span key={f.guild_id} className="flex flex-col items-center leading-tight">
                <span style={{ color: battleHeatColor(f.kills, maxFactionKills, minFactionKills) }}>
                  {battleFactionTag(f)}
                </span>
                <span className="text-[10px] font-normal text-zinc-600">{f.player_count}</span>
              </span>
            ))}
          </span>
        ) : (
          <div className="truncate text-sm text-zinc-200" title={b.cluster ?? ""}>
            {b.cluster ?? "Zona desconhecida"}
          </div>
        )}
      </span>

      <span className="w-12 shrink-0 text-right text-xs font-semibold text-amber-400/80 tabular-nums">
        {battleFameShort(b.total_fame)}
      </span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-500 tabular-nums">{b.kill_count} kills</span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-600 tabular-nums">{battleTimeAgo(b.start_time)}</span>
    </a>
  );
}

const STAGGER_MS = 350;

function BattlesCard({ onSeeAll }: { onSeeAll: () => void }) {
  const { servers } = useServer();
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
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <button onClick={onSeeAll} className="group mb-3 flex w-full items-center justify-between text-left">
        <h2 className="text-sm font-bold text-zinc-100 group-hover:text-amber-300">Batalhas Recentes</h2>
        <span className="text-xs text-zinc-500 group-hover:text-amber-300">Ver todas →</span>
      </button>
      {battles === null && <div className="py-8 text-center text-sm text-zinc-500">Carregando…</div>}
      {battles?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">Nenhuma batalha encontrada.</div>}
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
    </div>
  );
}

// ── Dashboard ───────────────────────────────────────────────────────────
export default function Dashboard({ onOpenBattles }: { onOpenBattles: () => void }) {
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
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ServerHighlightsCard />
        <PatchNotesCard onSeeAll={() => setShowAllPatchNotes(true)} />
      </div>
    </div>
  );
}
