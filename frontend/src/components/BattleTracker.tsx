import { useEffect, useMemo, useRef, useState } from "react";
import { navigate } from "../router";
import { useLang, useT, REGION_LABELS, zoneLabel, factionTag as makeTag, type GameServer } from "../i18n";
import { timeAgo as timeAgoFmt } from "../lib/format";
import GlobalSearch from "./GlobalSearch";

// GameServer ("west"/"east"/"europe", usado pro preço de mercado) -> Battle.region
// do backend ("americas"/"asia"/"europe", usado pro feed de batalhas).
const SERVER_TO_REGION: Record<GameServer, string> = { west: "americas", east: "asia", europe: "europe" };

const API = import.meta.env.DEV ? "http://localhost:8000" : "";

interface Faction {
  guild_id: string;
  guild_name: string;
  alliance_name: string | null;
  kills: number;
  player_count: number;
}

interface Battle {
  public_id: string;
  region: string;
  start_time: string;
  end_time: string | null;
  total_fame: number;
  kill_count: number;
  cluster: string | null;
  players_total: number;
  is_zvz: boolean;
  factions: Faction[];
}

const FILTER_INPUT_CLASS = "w-10 bg-transparent text-xs text-zinc-100 outline-none [color-scheme:dark] text-center tabular-nums";

/** Rótulo + campo lado a lado, item de uma lista horizontal de filtros (sem caixa própria). */
function FilterField({ label, className, children }: {
  label: string; className?: string; children: React.ReactNode;
}) {
  return (
    <div className={`flex items-center gap-1.5 shrink-0 ${className ?? ""}`}>
      <span className="text-[10px] uppercase tracking-wide text-zinc-500 whitespace-nowrap">{label}</span>
      {children}
    </div>
  );
}

/** dd/mm, ou dd/mm/yy se o ano escolhido for diferente do ano atual (UTC). */
function compactDate(v: string): string {
  if (!v) return "";
  const [y, m, d] = v.split("-");
  const curYear = new Date().getUTCFullYear();
  return Number(y) === curYear ? `${d}/${m}` : `${d}/${m}/${y.slice(-2)}`;
}

/** <input type="date"> nativo (mantém o seletor do navegador), mas com o texto
 * visível trocado pela versão compacta — o input real fica invisível por cima. */
function CompactDateInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <span className="relative inline-block">
      <span className="pointer-events-none block whitespace-nowrap text-xs text-zinc-100 tabular-nums">
        {compactDate(value) || "dd/mm"}
      </span>
      <input
        type="date"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      />
    </span>
  );
}

/** Tag de exibição: aliança entre colchetes, ou o nome puro da guilda quando ela não tem aliança.
 *  Nomes longos (>12 chars) viram sigla pra não quebrar o heatmap. */
function factionTag(f: Faction): string {
  return makeTag(f.alliance_name, f.guild_name);
}

const MAX_DISPLAY_FACTIONS = 4;

// Heatmap de kills: quem mais matou fica perto de HEAT_MAX, quem menos matou fica perto de HEAT_MIN.
const HEAT_MAX: [number, number, number] = [0x66, 0x71, 0x60]; // #667160
const HEAT_MIN: [number, number, number] = [0x52, 0x52, 0x5c]; // #52525C

function heatColor(kills: number, maxKills: number, minKills: number): string {
  const t = maxKills === minKills ? 0 : (maxKills - kills) / (maxKills - minKills);
  const [r, g, b] = HEAT_MAX.map((c, i) => Math.round(c + (HEAT_MIN[i] - c) * t));
  return `rgb(${r}, ${g}, ${b})`;
}

function fameShort(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return n > 0 ? String(n) : "—";
}

/** dd/mm hh:mm UTC, ou dd/mm/yy hh:mm se for de um ano anterior ao atual (UTC). */
function dateTimeUTC(ts: string): string {
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

const AUTO_REFRESH_MS = 60_000;
const PAGE_SIZE = 10;
const DEFAULT_MIN_PLAYERS = "5";
const DEFAULT_MIN_KILLS = "5";

interface Filters {
  search: string;
  // string (não number): input pode ficar vazio sem virar "0" forçado — vazio = sem filtro.
  minPlayers: string;
  minKills: string;
  dateFrom: string;
  dateTo: string;
  servers: GameServer[];
  pageSize: number;
}

const STAGGER_MS = 350;

export default function BattleTracker({
  initialPage,
  initialSearch,
  initialMinPlayers,
  initialMinKills,
  initialDateFrom,
  initialDateTo,
}: {
  initialPage?: number;
  initialSearch?: string;
  initialMinPlayers?: string;
  initialMinKills?: string;
  initialDateFrom?: string;
  initialDateTo?: string;
} = {}) {
  const t = useT();
  const { lang, servers } = useLang();
  const [battles, setBattles] = useState<Battle[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // filtros — sempre combinados (AND) na consulta, que só olha pra nossa base.
  const [search, setSearch] = useState(initialSearch ?? "");
  const [minPlayers, setMinPlayers] = useState(initialMinPlayers ?? DEFAULT_MIN_PLAYERS);
  const [minKills, setMinKills] = useState(initialMinKills ?? DEFAULT_MIN_KILLS);
  const [dateFrom, setDateFrom] = useState(initialDateFrom ?? "");
  const [dateTo, setDateTo] = useState(initialDateTo ?? "");
  const [page, setPage] = useState(initialPage ?? 0);
  const pageSize = PAGE_SIZE;
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtersRef = useRef<Filters & { page: number }>({
    search: "", minPlayers: DEFAULT_MIN_PLAYERS, minKills: DEFAULT_MIN_KILLS, dateFrom: "", dateTo: "", servers, pageSize, page: 0,
  });
  filtersRef.current = { search, minPlayers, minKills, dateFrom, dateTo, servers, pageSize, page };

  // live feed — só ativo na página 0 sem filtro de data
  const knownIdsRef = useRef<Set<string>>(new Set());
  const insertQueueRef = useRef<Battle[]>([]);
  const insertTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [newBattleIds, setNewBattleIds] = useState<Set<string>>(new Set());

  function drainInsertQueue() {
    if (!insertQueueRef.current.length) {
      clearInterval(insertTimerRef.current!);
      insertTimerRef.current = null;
      return;
    }
    const next = insertQueueRef.current.shift()!;
    knownIdsRef.current.add(next.public_id);
    setBattles(prev =>
      prev ? [next, ...prev.filter(b => b.public_id !== next.public_id)].slice(0, pageSize) : [next]
    );
    setNewBattleIds(prev => new Set([...prev, next.public_id]));
  }

  async function load(f: Filters, p: number, silent = false) {
    if (!silent) {
      setLoading(true);
      // filter/page change: abort pending live insertions e reseta o feed
      clearInterval(insertTimerRef.current!);
      insertTimerRef.current = null;
      insertQueueRef.current = [];
      knownIdsRef.current = new Set();
      setNewBattleIds(new Set());
    }
    setError(null);
    try {
      const params = new URLSearchParams({
        limit: String(f.pageSize),
        offset: String(p * f.pageSize),
        min_players: f.minPlayers || "0",
        min_kills: f.minKills || "0",
        // servidor(es) escolhidos nas configurações — 1 selecionado filtra só
        // esse, 2+ mostra a união de todos (pedido explícito).
        regions: f.servers.map(s => SERVER_TO_REGION[s]).join(","),
      });
      if (f.search) params.set("search", f.search);
      if (f.dateFrom) params.set("date_from", f.dateFrom);
      if (f.dateTo) params.set("date_to", f.dateTo);
      const res = await fetch(`${API}/battles?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setBattles(data.battles);
      setTotal(data.total);
      // inicializa o set de IDs conhecidos para o feed live funcionar
      if (p === 0) knownIdsRef.current = new Set((data.battles as Battle[]).map(b => b.public_id));
    } catch (e) {
      if (!silent) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => {
    setPage(0);
    load(filtersRef.current, 0);

    const id = setInterval(async () => {
      const f = filtersRef.current;
      const isLive = f.page === 0 && !f.dateFrom && !f.dateTo;

      if (!isLive) {
        // pagina > 0 ou com filtro de data: bulk silent (comportamento original)
        load(f, f.page, true);
        return;
      }

      // live peek: busca o topo do feed com os filtros atuais
      try {
        const params = new URLSearchParams({
          limit: String(f.pageSize),
          offset: "0",
          min_players: f.minPlayers || "0",
          min_kills: f.minKills || "0",
          regions: f.servers.map(s => SERVER_TO_REGION[s]).join(","),
        });
        if (f.search) params.set("search", f.search);
        const res = await fetch(`${API}/battles?${params}`);
        if (!res.ok) return;
        const data = await res.json();
        const incoming: Battle[] = data.battles;

        const newOnes = incoming.filter(b => !knownIdsRef.current.has(b.public_id));
        incoming.forEach(b => knownIdsRef.current.add(b.public_id));

        if (!newOnes.length) {
          // nada novo — atualiza dados existentes silenciosamente
          setBattles(incoming);
          setTotal(data.total);
          return;
        }

        setTotal(data.total);
        // enfileira do mais antigo pro mais novo para o mais novo terminar no topo
        insertQueueRef.current.push(...[...newOnes].reverse());
        if (!insertTimerRef.current) {
          drainInsertQueue();
          if (insertQueueRef.current.length) {
            insertTimerRef.current = setInterval(drainInsertQueue, STAGGER_MS);
          }
        }
      } catch (_) { /* silent */ }
    }, AUTO_REFRESH_MS);

    return () => { clearInterval(id); clearInterval(insertTimerRef.current!); };
  }, [servers]);

  // Espelha filtros na URL (replaceState — não empilha histórico a cada troca).
  // Só entram os que diferem do default, pra link ficar curto e shareable.
  useEffect(() => {
    const sp = new URLSearchParams();
    sp.set("view", "battles");
    if (page > 0) sp.set("page", String(page));
    if (search) sp.set("search", search);
    if (minPlayers !== DEFAULT_MIN_PLAYERS) sp.set("min_players", minPlayers);
    if (minKills !== DEFAULT_MIN_KILLS) sp.set("min_kills", minKills);
    if (dateFrom) sp.set("date_from", dateFrom);
    if (dateTo) sp.set("date_to", dateTo);
    history.replaceState(history.state, "", `/?${sp.toString()}`);
  }, [page, search, minPlayers, minKills, dateFrom, dateTo]);

  // qualquer filtro muda -> debounce curto, volta pra página 1 e recarrega.
  function debouncedReload() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(0);
      load(filtersRef.current, 0);
    }, 400);
  }

  function onSearch(v: string) { setSearch(v); debouncedReload(); }
  function onMinPlayers(v: string) {
    if (v !== "" && !/^\d+$/.test(v)) return;
    setMinPlayers(v);
    debouncedReload();
  }
  function onMinKills(v: string) {
    if (v !== "" && !/^\d+$/.test(v)) return;
    setMinKills(v);
    debouncedReload();
  }
  function onDateFrom(v: string) { setDateFrom(v); debouncedReload(); }
  function onDateTo(v: string) { setDateTo(v); debouncedReload(); }

  function onPage(p: number) {
    setPage(p);
    load(filtersRef.current, p);
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // mesma janela de 5 páginas numeradas da paginação de jogadores (1-indexed
  // pra exibição; "page" aqui é 0-indexed, o offset usado na busca).
  const pageNums = useMemo(() => {
    const current = page + 1;
    const start = Math.max(1, Math.min(current - 2, totalPages - 4));
    const to = Math.min(totalPages, start + 4);
    return Array.from({ length: to - start + 1 }, (_, i) => start + i);
  }, [page, totalPages]);

  // Modo "Multi": seleção fica num state separado da lista, então sobrevive
  // a troca de página/busca/auto-refresh — só é limpa quando o modo é desligado.
  const [multiMode, setMultiMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [merging, setMerging] = useState(false);

  function toggleSelect(publicId: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(publicId)) next.delete(publicId);
      else next.add(publicId);
      return next;
    });
  }

  async function onMultiClick() {
    if (!multiMode) {
      setMultiMode(true);
      return;
    }
    if (selected.size >= 2) {
      setMerging(true);
      setError(null);
      try {
        const res = await fetch(`${API}/battles/merge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ public_ids: Array.from(selected) }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        navigate(`/${data.public_id}`);
        return;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setMerging(false);
      }
    }
    setMultiMode(false);
    setSelected(new Set());
  }

  function onBattleClick(publicId: string) {
    if (multiMode) toggleSelect(publicId);
    else navigate(`/${publicId}`);
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <GlobalSearch
        onQueryChange={onSearch}
        battlesOnly
        extraFilters={
          <>
            <button
              onClick={onMultiClick}
              disabled={merging}
              className={`shrink-0 dash-chip disabled:opacity-40 ${multiMode ? "dash-chip-on" : ""}`}
            >
              {merging ? t("multiCombining") : multiMode ? `${t("multiLabel")} (${selected.size})` : t("multiLabel")}
            </button>
            <FilterField label={t("periodLabel")}>
              <CompactDateInput value={dateFrom} onChange={onDateFrom} />
              <span className="shrink-0 text-zinc-600">–</span>
              <CompactDateInput value={dateTo} onChange={onDateTo} />
            </FilterField>
            <FilterField label={t("playersFilterLabel")}>
              <input
                type="text"
                inputMode="numeric"
                value={minPlayers}
                onChange={e => onMinPlayers(e.target.value)}
                className={FILTER_INPUT_CLASS}
              />
            </FilterField>
            <FilterField label={t("killsFilterLabel")}>
              <input
                type="text"
                inputMode="numeric"
                value={minKills}
                onChange={e => onMinKills(e.target.value)}
                className={FILTER_INPUT_CLASS}
              />
            </FilterField>
          </>
        }
      />

      {error && (
        <p className="mb-4 rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400">{error}</p>
      )}

      {!loading && battles?.length === 0 && (
        <div className="py-16 text-center">
          <p className="text-zinc-400">{t("noBattlesFound")}</p>
          <p className="mt-1 text-sm text-zinc-600">{t("noBattlesFoundFilters")}</p>
        </div>
      )}

      {/* Lista */}
      <div className="space-y-2">
        {battles?.map(b => {
          const maxFactionKills = b.factions.length ? Math.max(...b.factions.map(f => f.kills)) : 0;
          const minFactionKills = b.factions.length ? Math.min(...b.factions.map(f => f.kills)) : 0;
          const isSelected = selected.has(b.public_id);
          const isNew = newBattleIds.has(b.public_id);
          return (
            <a
              key={b.public_id}
              href={`/${b.public_id}`}
              onClick={e => {
                // botão do meio (auxclick, nem chega aqui), ctrl/cmd/shift+click e
                // botão direito (sem onClick) seguem o comportamento nativo do link
                // (nova aba/janela, menu de contexto) — só o clique simples normal
                // usa o roteador da SPA, pra não recarregar a página inteira.
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                e.preventDefault();
                onBattleClick(b.public_id);
              }}
              onAnimationEnd={isNew ? () => setNewBattleIds(prev => { const n = new Set(prev); n.delete(b.public_id); return n; }) : undefined}
              className={`w-full rounded-xl border px-4 py-3 text-left flex flex-wrap items-center gap-x-3 gap-y-1 transition-colors cursor-pointer${isNew ? " dash-glow" : ""} ${
                isSelected
                  ? "border-amber-500 bg-amber-500/10"
                  : "border-zinc-800 bg-zinc-900/60 hover:border-zinc-600 hover:bg-zinc-800/40"
              }`}
            >
              {multiMode && (
                <span
                  className={`h-4 w-4 shrink-0 rounded border ${
                    isSelected ? "border-amber-400 bg-amber-400" : "border-zinc-600"
                  }`}
                />
              )}

              <span className="w-24 shrink-0 flex flex-col leading-tight">
                <span className="text-[10px] uppercase tracking-wide text-zinc-600">
                  {REGION_LABELS[lang][b.region] ?? b.region}
                </span>
                <span className="text-xs text-zinc-500 tabular-nums">{dateTimeUTC(b.start_time)}</span>
              </span>

              <span className="flex-1 min-w-0 text-center">
                {b.factions.length > 0 ? (
                  <span className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-sm font-semibold">
                    {b.factions.slice(0, MAX_DISPLAY_FACTIONS).map(f => (
                      <span key={f.guild_id} className="flex flex-col items-center leading-tight">
                        <span style={{ color: heatColor(f.kills, maxFactionKills, minFactionKills) }}>
                          {factionTag(f)}
                        </span>
                        {/* contagem de jogadores — cor fixa e sutil, fora do
                            style do heatmap de propósito (pedido explícito). */}
                        <span className="text-[10px] font-normal text-zinc-600">{f.player_count}</span>
                      </span>
                    ))}
                  </span>
                ) : (
                  <div className="text-sm text-zinc-200 truncate" title={b.cluster ?? ""}>
                    {zoneLabel(b.cluster, t)}
                  </div>
                )}
              </span>

              {/* colunas de largura fixa — sem isso, números com mais dígitos
                  (ex.: 157 kills) empurram o texto e desalinham a lista inteira
                  linha a linha (pedido explícito). */}
              <span className="w-12 shrink-0 text-right text-xs font-semibold text-amber-400/80 tabular-nums">
                {fameShort(b.total_fame)}
              </span>
              <span className="w-16 shrink-0 text-right text-xs text-zinc-500 tabular-nums">{b.kill_count} {t("killsSuffix")}</span>
              <span className="w-16 shrink-0 text-right text-xs text-zinc-600 tabular-nums">
                {timeAgoFmt(b.start_time, { min: t("agoMinutes"), hour: t("agoHours"), day: t("agoDays") })}
              </span>
            </a>
          );
        })}
      </div>

      {/* mesma paginação (page size + páginas numeradas) da lista de jogadores
          da página de batalha (pedido explícito). Fica montada e visível
          durante o loading (só desabilitada) — sumir e reaparecer é o mesmo
          "pulo" de layout que a gente acabou de tirar do quadrante de cima. */}
      {battles && (
        <div className="mt-4 flex items-center justify-end gap-3 text-xs">
          <i
            className={`ti ti-loader-2 animate-spin text-zinc-500 ${loading ? "opacity-100" : "opacity-0"}`}
            aria-hidden="true"
          />
          <div className="flex items-center gap-1">
            <button disabled={loading || page <= 0} onClick={() => onPage(0)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">«</button>
            <button disabled={loading || page <= 0} onClick={() => onPage(page - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">‹</button>
            {pageNums.map(n => (
              <button
                key={n}
                disabled={loading}
                onClick={() => onPage(n - 1)}
                className={`px-2 py-1 rounded disabled:opacity-30 ${n === page + 1 ? "bg-amber-500/10 text-amber-300 border border-amber-500" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                {n}
              </button>
            ))}
            <button disabled={loading || page + 1 >= totalPages} onClick={() => onPage(page + 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">›</button>
            <button disabled={loading || page + 1 >= totalPages} onClick={() => onPage(totalPages - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">»</button>
          </div>
          <span className="text-zinc-500">{total} {t("battlesCountSuffix")}</span>
        </div>
      )}
    </div>
  );
}
