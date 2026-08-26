import { useRef, useState } from "react";
import { useLang, useT, REGION_LABELS, zoneLabel, factionTag as makeTag } from "../i18n";
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
  return makeTag(f.alliance_name, f.guild_name);
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
            {zoneLabel(b.cluster, t)}
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
interface SearchGuild    { albion_id: string; name: string; alliance_name: string | null; battles: number; region: string | null }
interface SearchAlliance { albion_id: string; name: string; guild_count: number; battles: number; region: string | null }
// SearchBattle é compatível com RecentBattle — mesmos campos, reutiliza RecentBattleRow
type SearchBattle = RecentBattle;
interface SearchResults  { players: SearchPlayer[]; guilds: SearchGuild[]; alliances: SearchAlliance[]; battles: SearchBattle[] }

const SEARCH_REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };
// Sinônimos que o usuário pode digitar depois do nick pra filtrar região.
// "slayner america" / "slayner west" / "slayner am" → todos viram americas.
const REGION_ALIASES: Record<string, string> = {
  americas: "americas", america: "americas", am: "americas", west: "americas",
  europe: "europe", eu: "europe", east: "asia",
  asia: "asia", as: "asia", asian: "asia",
};

/** "slayner americas" → { q: "slayner", region: "americas" }. "slayner" →
 *  { q: "slayner", region: null }. O usuário pode digitar o servidor no final
 *  pra restringir — nomes não são únicos entre servidores da Albion. */
function parseRegionFromQuery(raw: string): { q: string; region: string | null } {
  const parts = raw.trim().split(/\s+/);
  if (parts.length >= 2) {
    const last = parts[parts.length - 1].toLowerCase();
    const region = REGION_ALIASES[last];
    if (region) {
      return { q: parts.slice(0, -1).join(" "), region };
    }
  }
  return { q: raw.trim(), region: null };
}

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

function SearchCard({ onClick, left, title, sub, meta, delay, region }: {
  onClick: () => void; left: React.ReactNode;
  title: string; sub?: string; meta: string; delay: number; region?: string | null;
}) {
  const { lang } = useLang();
  return (
    <button onClick={onClick} style={{ animationDelay: `${delay}ms` }}
      className="search-row w-full rounded-xl border border-zinc-800 bg-zinc-900/50 px-4 py-3 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-900">
      <div className="flex items-center gap-3">
        {left}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold text-zinc-100">{title}</span>
            {region && (
              <span className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-zinc-400">
                {REGION_LABELS[lang][region] ?? region}
              </span>
            )}
          </div>
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
export default function GlobalSearch({ onQueryChange, extraFilters, battlesOnly }: {
  onQueryChange?: (q: string) => void; extraFilters?: React.ReactNode;
  battlesOnly?: boolean;
} = {}) {
  const t = useT();
  const [query, setQuery]   = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  // Busca externa (Albion API) — disparada 8s depois da local se ainda não
  // tem resultado. Mantém o spinner pra indicar "haverão mais opções".
  const [extLoading, setExtLoading] = useState(false);
  // key muda a cada resultado novo → React remonta o container → animação retrigger
  const [animKey, setAnimKey] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const extTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Digitação rápida dispara várias requests em voo — aborta a anterior (evita
  // gastar backend/rede à toa) e ainda guarda um id monotônico como cinto de
  // segurança contra a resposta antiga chegar DEPOIS da mais nova mesmo assim.
  const abortRef = useRef<AbortController | null>(null);
  const extAbortRef = useRef<AbortController | null>(null);
  const reqIdRef = useRef(0);
  const extReqIdRef = useRef(0);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    onQueryChange?.(q);
    if (timer.current) clearTimeout(timer.current);
    if (extTimer.current) clearTimeout(extTimer.current);
    abortRef.current?.abort();
    extAbortRef.current?.abort();
    setExtLoading(false);
    if (q.trim().length < 2) { setResults(null); setLoading(false); return; }
    setLoading(true);
    // Parse de região no final do query: "slayner americas" → q="slayner",
    // region="americas". O backend filtra por região (nomes não são únicos
    // entre servidores da Albion); sem região, busca em todas.
    const { q: parsedQ, region } = parseRegionFromQuery(q.trim());
    if (parsedQ.length < 2) { setResults(null); setLoading(false); return; }
    const trimmed = parsedQ;
    const regionParam = region ? `&region=${region}` : "";
    timer.current = setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      const reqId = ++reqIdRef.current;
      fetch(`${BATTLES_API}/public/search?q=${encodeURIComponent(trimmed)}${regionParam}`, { signal: controller.signal })
        .then(r => r.json())
        .then((d: SearchResults) => {
          if (reqId !== reqIdRef.current) return; // resposta antiga — ignora
          setResults(d); setAnimKey(k => k + 1);
          // Loading local desliga (a 1ª resposta já chegou); extLoading cuida
          // do spinner restante enquanto a busca externa roda.
          setLoading(false);
          // Busca externa (Albion API) só faz sentido pra players/guilds/alliances
          // — na página de Batalhas (battlesOnly), batalhas são tudo que importa.
          if (battlesOnly) { setExtLoading(false); return; }
          setExtLoading(true);
          extTimer.current = setTimeout(() => {
            const ec = new AbortController();
            extAbortRef.current = ec;
            const ereqId = ++extReqIdRef.current;
            fetch(`${BATTLES_API}/public/search/external?q=${encodeURIComponent(trimmed)}${regionParam}`, { signal: ec.signal })
              .then(r => r.json())
              .then((ed: Partial<SearchResults>) => {
                if (ereqId !== extReqIdRef.current) return;
                // Merge: só adiciona quem NÃO está já nos resultados locais
                // (Albion pode devolver os mesmos que já tínhamos).
                setResults(prev => {
                  if (!prev) return prev;
                  const knownP = new Set(prev.players.map(p => p.albion_id));
                  const knownG = new Set(prev.guilds.map(g => g.albion_id));
                  const knownA = new Set(prev.alliances.map(a => a.albion_id));
                  const newP = (ed.players ?? []).filter(p => !knownP.has(p.albion_id));
                  const newG = (ed.guilds ?? []).filter(g => !knownG.has(g.albion_id));
                  const newA = (ed.alliances ?? []).filter(a => !knownA.has(a.albion_id));
                  if (!newP.length && !newG.length && !newA.length) return prev; // nada novo
                  setAnimKey(k => k + 1);
                  return {
                    ...prev,
                    players: [...prev.players, ...newP],
                    guilds: [...prev.guilds, ...newG],
                    alliances: [...prev.alliances, ...newA],
                  };
                });
                setExtLoading(false);
              })
              .catch((err: unknown) => {
                if (ereqId !== extReqIdRef.current) return;
                if ((err as { name?: string })?.name !== "AbortError") setExtLoading(false);
              });
          }, 8000);
        })
        .catch((err: unknown) => {
          if (reqId !== reqIdRef.current) return;
          if ((err as { name?: string })?.name !== "AbortError") setLoading(false);
        });
    }, 300);
  }

  const active  = query.trim().length >= 2;
  const total   = results ? (battlesOnly ? results.battles.length : results.players.length + results.guilds.length + results.alliances.length + results.battles.length) : 0;
  const anyLoading = loading || extLoading;

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
          {anyLoading && <i className="ti ti-loader-2 absolute right-1.5 top-1/2 -translate-y-1/2 animate-spin text-sm text-zinc-500" />}
          {!anyLoading && active && (
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

      {/* resultados — mostra o que já temos enquanto a busca externa roda
          (extLoading), indicando que haverão mais opções. Loading local
          (primeiro fetch) esconde até ter a 1ª resposta. */}
      {active && !loading && results && (
        <div key={animKey} className="search-expand mt-3 space-y-5">
          {total === 0 ? (
            extLoading ? (
              <p className="py-8 text-center text-sm text-zinc-500">
                {t("searchLookingFor")} "{query}"...
              </p>
            ) : (
              <p className="py-8 text-center text-sm text-zinc-600">
                {t("noResultsFor")} "{query}".
              </p>
            )
          ) : (
            <>
              {!battlesOnly && results.players.length > 0 && (
                <div>
                  <SectionHeader label={t("players")} count={results.players.length} />
                  <div className="space-y-2">
                    {results.players.map(p => (
                      <SearchCard key={p.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/${SEARCH_REGION_PREFIX[p.region] ?? "am"}/${encodeURIComponent(p.name)}`)}
                        left={<SearchInitial char={p.name[0]} />}
                        title={p.name}
                        sub={[p.alliance_name ? `[${p.alliance_name}]` : "", p.guild_name ?? ""].filter(Boolean).join(" ") || undefined}
                        meta={`${p.battles} ${t("battlesCountSuffix")}`}
                        region={p.region} />
                    ))}
                  </div>
                </div>
              )}

              {!battlesOnly && results.guilds.length > 0 && (
                <div>
                  <SectionHeader label={t("guildsLabel")} count={results.guilds.length} />
                  <div className="space-y-2">
                    {results.guilds.map(g => (
                      <SearchCard key={g.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/guild/${g.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-shield text-sm text-zinc-400" /></div>}
                        title={g.name}
                        sub={g.alliance_name ? `[${g.alliance_name}]` : undefined}
                        meta={`${g.battles} ${t("battlesCountSuffix")}`}
                        region={g.region} />
                    ))}
                  </div>
                </div>
              )}

              {!battlesOnly && results.alliances.length > 0 && (
                <div>
                  <SectionHeader label={t("alliancesLabel")} count={results.alliances.length} />
                  <div className="space-y-2">
                    {results.alliances.map(a => (
                      <SearchCard key={a.albion_id} delay={nextDelay()}
                        onClick={() => navigate(`/alliance/${a.albion_id}`)}
                        left={<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800"><i className="ti ti-users text-sm text-zinc-400" /></div>}
                        title={a.name}
                        sub={`${a.guild_count} ${a.guild_count !== 1 ? t("guildWordPlural") : t("guildWordSingular")}`}
                        meta={`${a.battles} ${t("battlesCountSuffix")}`}
                        region={a.region} />
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
          {extLoading && (
            <div className="flex items-center justify-center gap-2 py-3 text-xs text-zinc-500">
              <i className="ti ti-loader-2 animate-spin" />
              {t("searchLookingForMore")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
