import { useRef, useState } from "react";
import { useLang, useT, REGION_LABELS } from "../i18n";
import { timeAgo } from "../lib/format";
import { navigate } from "../router";

const BATTLES_API = import.meta.env.DEV ? "http://localhost:8000" : "";

// ── Linha de batalha recente (compartilhada com BattlesCard em Dashboard.tsx) ──
export interface RecentFaction { guild_id: string; guild_name: string; alliance_name: string | null; kills: number; player_count: number }
export interface RecentBattle {
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

export function RecentBattleRow({ b, isNew, onGlowEnd }: { b: RecentBattle; isNew?: boolean; onGlowEnd?: () => void }) {
  const t = useT();
  const { lang } = useLang();
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
          {REGION_LABELS[lang][b.region] ?? b.region}
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
            {b.cluster ?? t("unknownZone")}
          </div>
        )}
      </span>

      <span className="w-12 shrink-0 text-right text-xs font-semibold text-amber-400/80 tabular-nums">
        {battleFameShort(b.total_fame)}
      </span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-500 tabular-nums">{b.kill_count} {t("killsSuffix")}</span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-600 tabular-nums">
        {timeAgo(b.start_time, { min: t("agoMinutes"), hour: t("agoHours"), day: t("agoDays") })}
      </span>
    </a>
  );
}

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

// Constante nas páginas Início/Batalhas/Highscores (pedido explícito) —
// cada uma renderiza este mesmo componente no topo, em vez de hoistar pro
// App.tsx: mantém o estado de busca simples (reseta ao trocar de página,
// que já desmonta a página inteira de qualquer forma) sem precisar levantar
// nada pro componente pai.
//
// `onQueryChange`/`extraFilters` existem só pra página de Batalhas: o texto
// digitado aqui também filtra a lista de batalhas (BattleTracker chama
// onQueryChange pra atualizar o próprio filtro "search"), e os filtros que
// antes viviam numa barra separada (Multi, período, jogadores, kills) agora
// entram como `extraFilters`, dentro da MESMA caixa — pedido explícito pra
// não ter duas barras.
export default function GlobalSearch({ onQueryChange, extraFilters }: {
  onQueryChange?: (q: string) => void; extraFilters?: React.ReactNode;
} = {}) {
  const t = useT();
  const [query, setQuery]   = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  // key muda a cada resultado novo → React remonta o container → animação retrigger
  const [animKey, setAnimKey] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    onQueryChange?.(q);
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
      {/* input (+ extraFilters, na página de Batalhas — mesma caixa, um filtro só) */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-700 bg-zinc-900/60 px-3 py-2 transition-colors focus-within:border-amber-500/50 focus-within:ring-1 focus-within:ring-amber-500/20">
        <div className="relative min-w-[160px] flex-1">
          <i className="ti ti-search absolute left-2 top-1/2 -translate-y-1/2 text-sm text-zinc-500" />
          <input type="text" value={query} onChange={handleChange}
            placeholder={t("searchPlaceholderGlobal")}
            className="w-full bg-transparent py-1.5 pl-7 pr-7 text-sm text-zinc-100 placeholder-zinc-600 outline-none" />
          {loading && <i className="ti ti-loader-2 absolute right-1.5 top-1/2 -translate-y-1/2 animate-spin text-sm text-zinc-500" />}
          {!loading && active && (
            <button onClick={() => { setQuery(""); setResults(null); onQueryChange?.(""); }}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400">
              <i className="ti ti-x text-xs" />
            </button>
          )}
        </div>
        {extraFilters && (
          <>
            <span className="h-5 w-px shrink-0 bg-zinc-800" />
            {extraFilters}
          </>
        )}
      </div>

      {/* resultados */}
      {active && !loading && results && (
        <div key={animKey} className="search-expand mt-3 space-y-5">
          {total === 0 ? (
            <p className="py-8 text-center text-sm text-zinc-600">
              {t("noResultsFor")} "{query}".
            </p>
          ) : (
            <>
              {results.players.length > 0 && (
                <div>
                  <SectionHeader label={t("players")} count={results.players.length} />
                  <div className="space-y-2">
                    {results.players.map(p => (
                      <SearchCard key={p.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/${SEARCH_REGION_PREFIX[p.region] ?? "am"}/${encodeURIComponent(p.name)}`)}
                        left={<SearchInitial char={p.name[0]} />}
                        title={p.name}
                        sub={[p.alliance_name ? `[${p.alliance_name}]` : "", p.guild_name ?? ""].filter(Boolean).join(" ") || undefined}
                        meta={`${p.battles} ${t("battlesCountSuffix")}`} />
                    ))}
                  </div>
                </div>
              )}

              {results.guilds.length > 0 && (
                <div>
                  <SectionHeader label={t("guildsLabel")} count={results.guilds.length} />
                  <div className="space-y-2">
                    {results.guilds.map(g => (
                      <SearchCard key={g.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/guild/${g.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-shield text-sm text-zinc-400" /></div>}
                        title={g.name}
                        sub={g.alliance_name ? `[${g.alliance_name}]` : undefined}
                        meta={`${g.battles} ${t("battlesCountSuffix")}`} />
                    ))}
                  </div>
                </div>
              )}

              {results.alliances.length > 0 && (
                <div>
                  <SectionHeader label={t("alliancesLabel")} count={results.alliances.length} />
                  <div className="space-y-2">
                    {results.alliances.map(a => (
                      <SearchCard key={a.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/alliance/${a.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-users text-sm text-zinc-400" /></div>}
                        title={a.name}
                        sub={`${a.guild_count} ${a.guild_count !== 1 ? t("guildWordPlural") : t("guildWordSingular")}`}
                        meta={`${a.battles} ${t("battlesCountSuffix")}`} />
                    ))}
                  </div>
                </div>
              )}

              {results.battles.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    {results.players.length > 0
                      ? <>{t("recentBattlesOf")} <span className="text-zinc-300">{results.players.map(p => p.name).join(", ")}</span></>
                      : <>{t("battles")} <span className="text-zinc-600">· {results.battles.length}</span></>
                    }
                  </p>
                  <div className="space-y-2">
                    {results.battles.map(b => (
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
