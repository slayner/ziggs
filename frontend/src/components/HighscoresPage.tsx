import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useLang, useT, type GameServer, type Lang } from "../i18n";
import ES_ITEMS from "../i18n/es-items.json";
import { ALBION_ITEMS, itemRenderUrl, ICON_SIZE_SM, type AlbionItem } from "../data/albion-items";
import { silverShort } from "../lib/format";
import { searchMatch } from "../lib/search";
import { navigate } from "../router";
import GlobalSearch from "./GlobalSearch";

const SERVER_TO_REGION: Record<GameServer, string> = { west: "americas", east: "asia", europe: "europe" };
const API = import.meta.env.DEV ? "http://localhost:8000" : "";
const PAGE_SIZE = 10;
const SCOPE_KINDS = new Set(["pvp_fame", "most_battles", "crafting"]);
const GUILD_DEFAULT_KINDS = new Set(["pvp_fame", "most_battles"]);
const ALLTIME_ONLY_KINDS = new Set([
  "gather_total", "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber",
  "fishing", "crafting",
]);
type RankingWindow = "alltime" | "week" | "month" | "season" | `season:${number}`;

const wBase = (id: string) => id.replace(/^T\d+_/, "").replace(/@\d+$/, "");

// Mapa base->item (primeira variante encontrada, só pra extrair nome
// localizado) — construído uma vez, não por render.
const WEAPON_ITEM_BY_BASE = new Map<string, AlbionItem>();
for (const item of ALBION_ITEMS) {
  if (item.slot === "weapon" && !WEAPON_ITEM_BY_BASE.has(wBase(item.id))) {
    WEAPON_ITEM_BY_BASE.set(wBase(item.id), item);
  }
}

// gen() embute prefixo de tier ("4.0 Adaga"); aqui a entrada é por base (ícone T7),
// então tira o prefixo. Nomes de artefato (art()) não têm prefixo — regex não casa, passa direto.
const stripTier = (s: string) => s.replace(/^\d+\.\d+\s+/, "");
function weaponName(base: string, lang: Lang): string {
  const item = WEAPON_ITEM_BY_BASE.get(base);
  if (!item) return base;
  if (lang === "en") return stripTier(item.nameEn ?? item.name);
  if (lang === "es") return stripTier((ES_ITEMS as Record<string, string>)[base] ?? item.name);
  return stripTier(item.name);
}

function weaponIcon(base: string): string {
  // Ícones de arma aqui aparecem a 18-32px (destaques, dropdown, linhas) — 64px
  // é nítido em 2× e ~9.5× mais leve que o full-res.
  return itemRenderUrl(`T7_${base}`, 4, ICON_SIZE_SM);
}

interface GuildRef { albion_guild_id: string; name: string; alliance_name: string | null }
interface PlayerRef { albion_id: string; name: string; guild_name: string | null; alliance_name: string | null; region: string | null }

interface Highlights {
  week_start: string;
  underdog: { guild: GuildRef; kills: number } | null;
  weapon_scorer: { player: PlayerRef; weapon_base: string; points: number } | null;
  efficiency: { guild: GuildRef; fame_per_player: number } | null;
  most_battles: { guild: GuildRef; battles: number } | null;
}

interface WeaponDef { weapon_base: string; invisible_function: string | null }

type RankingKind = "pvp_fame" | "underdog" | "weapon_scorer" | "efficiency" | "most_battles" | string;

interface RankingRow {
  albion_guild_id?: string; albion_id?: string;
  name: string; alliance_name?: string | null; guild_name?: string | null; region?: string | null;
  weapon_base?: string; value: number; rank: number;
}

const REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };

// ── destaques (4 cards) ──────────────────────────────────────────────────

function HighlightCard({ icon, label, name, value, onClick, weaponBase }: {
  icon: string; label: string; name: string;
  value: string; onClick?: () => void; weaponBase?: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 min-w-0">
        {weaponBase
          ? <img src={weaponIcon(weaponBase)} alt={weaponBase} width={32} height={32} className="shrink-0" />
          : <span className="text-lg">{icon}</span>}
        <div className="min-w-0">
          <div className="truncate text-[10px] text-zinc-500 uppercase tracking-wide">{label}</div>
          {onClick
            ? <button onClick={onClick} className="text-sm text-zinc-200 font-medium truncate hover:text-amber-400 transition-colors">{name}</button>
            : <div className="text-sm text-zinc-200 font-medium truncate">{name}</div>}
        </div>
      </div>
      <div className="shrink-0 text-xl font-bold text-amber-400 tabular-nums">{value}</div>
    </div>
  );
}

function SkeletonBox({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-zinc-800/80 ${className ?? ""}`} />;
}

// Mesmo formato/tamanho do HighlightCard real — evita o "pulo" de layout
// quando os dados chegam. Só o 2º card (weapon_scorer) tem ícone em caixa
// (img 32x32); os outros 3 são um emoji solto (span text-lg, sem caixa) —
// mesma ordem sempre (underdog, weapon_scorer, efficiency, most_battles),
// então dá pra saber qual posição usa qual formato de ícone.
function HighlightCardSkeleton({ boxIcon }: { boxIcon?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <SkeletonBox className={boxIcon ? "h-8 w-8 shrink-0" : "h-[18px] w-[18px] shrink-0 rounded-full"} />
        <div className="min-w-0 space-y-1.5">
          <SkeletonBox className="h-2 w-14" />
          <SkeletonBox className="h-3.5 w-24" />
        </div>
      </div>
      <SkeletonBox className="h-6 w-14 shrink-0" />
    </div>
  );
}

function FilterRowSkeleton() {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <SkeletonBox className="h-8 w-28" />
      <SkeletonBox className="h-8 w-24" />
      <SkeletonBox className="h-8 min-w-[160px] flex-1" />
    </div>
  );
}

// Mesmo formato/tamanho de RankingRowView — idem acima, pra lista paginada.
function RankingRowSkeleton() {
  return (
    <div className="flex w-full items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <span className="w-8 shrink-0" />
      <SkeletonBox className="h-3.5 w-48" />
      <SkeletonBox className="h-3.5 w-14 shrink-0" />
    </div>
  );
}
const RANKING_SKELETON_ROWS = 10;

function HighlightsRow({ regions }: { regions: string }) {
  const t = useT();
  const [data, setData] = useState<Highlights | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${API}/highscores/highlights?regions=${regions}`)
      .then(r => r.json())
      .then(d => { if (alive) setData(d); })
      .catch(() => { if (alive) setData(null); });
    return () => { alive = false; };
  }, [regions]);

  if (!data) {
    return (
      <div className="mb-5 grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        {Array.from({ length: 4 }, (_, i) => <HighlightCardSkeleton key={i} boxIcon={i === 1} />)}
      </div>
    );
  }

  const items: { id: string; icon: string; label: string; name: string; value: string; onClick?: () => void; weaponBase?: string }[] = [];
  if (data.underdog) {
    items.push({
      id: "underdog", icon: "🐺", label: t("highscoreUnderdogLabel"),
      name: data.underdog.guild.name,
      value: String(data.underdog.kills),
      onClick: () => navigate(`/guild/${encodeURIComponent(data.underdog!.guild.albion_guild_id)}`),
    });
  }
  if (data.weapon_scorer) {
    const region = data.weapon_scorer.player.region;
    const url = region && REGION_PREFIX[region] ? `/${REGION_PREFIX[region]}/${encodeURIComponent(data.weapon_scorer.player.name)}` : undefined;
    items.push({
      id: "weapon_scorer", icon: "🎯", label: t("highscoreWeaponScorerLabel"),
      name: data.weapon_scorer.player.name,
      value: String(data.weapon_scorer.points),
      weaponBase: data.weapon_scorer.weapon_base,
      onClick: url ? () => navigate(url) : undefined,
    });
  }
  if (data.efficiency) {
    items.push({
      id: "efficiency", icon: "⚡", label: t("highscoreEfficiencyLabel"),
      name: data.efficiency.guild.name,
      value: silverShort(data.efficiency.fame_per_player),
      onClick: () => navigate(`/guild/${encodeURIComponent(data.efficiency!.guild.albion_guild_id)}`),
    });
  }
  if (data.most_battles) {
    items.push({
      id: "most_battles", icon: "⚔️", label: t("highscoreMostBattlesLabel"),
      name: data.most_battles.guild.name,
      value: String(data.most_battles.battles),
      onClick: () => navigate(`/guild/${encodeURIComponent(data.most_battles!.guild.albion_guild_id)}`),
    });
  }
  if (!items.length) return null;

  return (
    <div className="mb-5 grid gap-2" style={{ gridTemplateColumns: `repeat(${items.length}, 1fr)` }}>
      {items.map(({ id, ...it }) => <HighlightCard key={id} {...it} />)}
    </div>
  );
}

// ── dropdown de tipo de ranking (busca + seções: especiais + armas) ──────

function RankingTypeSelect({ kind, onChange, weapons }: {
  kind: RankingKind; onChange: (k: RankingKind) => void; weapons: WeaponDef[];
}) {
  const t = useT();
  const { lang } = useLang();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(ev: MouseEvent) {
      if (ref.current && !ref.current.contains(ev.target as Node)) {
        setOpen(false);
        setGatherSubOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  const specials: { key: RankingKind; label: string }[] = [
    { key: "pvp_fame", label: t("highscorePvpFameLabel") },
    { key: "underdog", label: t("highscoreUnderdogLabel") },
    { key: "weapon_scorer", label: t("highscoreWeaponScorerLabel") },
    { key: "efficiency", label: t("highscoreEfficiencyLabel") },
    { key: "most_battles", label: t("highscoreMostBattlesLabel") },
    { key: "silver_dropped", label: t("highscoreSilverDroppedLabel") },
    { key: "crafting", label: t("highscoreCraftingLabel") },
  ];

  // "Coleta" (total) fica no top-level com seta ▶ sempre visível. Clicar
  // seleciona o total; hover abre o submenu lateral com os por-recurso.
  // O total NÃO se repete dentro do submenu — já é o item top-level.
  const gatherTotalKey: RankingKind = "gather_total";
  const gatherTotalLabel = t("highscoreGatherTotalLabel");

  // Coleta por recurso — submenu lateral (cascata pra direita). Só os
  // recursos; crafting/fishing são escalares próprios mas fishing é coleta
  // (vara de pescar), crafting NÃO é (fica no top-level). Sem o total aqui.
  const gatherSubEntries: { key: RankingKind; label: string }[] = [
    { key: "gather_wood", label: t("highscoreGatherWoodLabel") },
    { key: "gather_hide", label: t("highscoreGatherHideLabel") },
    { key: "gather_ore", label: t("highscoreGatherOreLabel") },
    { key: "gather_rock", label: t("highscoreGatherRockLabel") },
    { key: "gather_fiber", label: t("highscoreGatherFiberLabel") },
    { key: "fishing", label: t("highscoreFishingLabel") },
  ];
  const [gatherSubOpen, setGatherSubOpen] = useState(false);
  const isGatherSubKind = kind === gatherTotalKey || gatherSubEntries.some(g => g.key === kind);

  const weaponEntries = useMemo(() => {
    const named = weapons.map(w => ({ key: `weapon:${w.weapon_base}` as RankingKind, base: w.weapon_base, label: weaponName(w.weapon_base, lang) }));
    named.sort((a, b) => a.label.localeCompare(b.label));
    return named;
  }, [weapons, lang]);

  const filteredWeapons = query.trim()
    ? weaponEntries.filter(w => searchMatch(query, w.label))
    : weaponEntries;

  const currentLabel = kind.startsWith("weapon:")
    ? weaponName(kind.slice(7), lang)
    : specials.find(s => s.key === kind)?.label
      ?? (kind === gatherTotalKey ? gatherTotalLabel : undefined)
      ?? gatherSubEntries.find(g => g.key === kind)?.label
      ?? kind;
  const currentWeaponBase = kind.startsWith("weapon:") ? kind.slice(7) : null;

  function pick(k: RankingKind) { onChange(k); setOpen(false); setQuery(""); }

  const rowCls = (active: boolean) =>
    `flex w-full items-center gap-2 truncate px-3 py-1.5 text-left hover:bg-zinc-800 ${active ? "text-amber-300" : "text-zinc-300"}`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => { setOpen(o => !o); setGatherSubOpen(false); }}
        className="flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 outline-none hover:border-zinc-500 focus:border-amber-500"
      >
        {currentWeaponBase && <img src={weaponIcon(currentWeaponBase)} alt="" width={18} height={18} />}
        {currentLabel}
        <i className="ti ti-chevron-down text-zinc-500" aria-hidden="true" />
      </button>
      {open && (
        <>
          <div className="absolute left-0 z-20 mt-1 w-64 rounded-lg border border-zinc-700 bg-zinc-900 py-1 text-xs shadow-lg">
            <div className="px-2 pb-1.5">
              <input
                autoFocus
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={t("highscoreSearchPlaceholder")}
                className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-amber-500"
              />
            </div>
            {!query.trim() && specials.map(s => (
              <button key={s.key} onClick={() => pick(s.key)} className={rowCls(kind === s.key)}>{s.label}</button>
            ))}
            {/* Coleta — item top-level (total) com submenu lateral aninhado.
                Wrapper relative + absolute filho: o onMouseLeave do wrapper
                cobre item E submenu juntos, então mover o mouse entre os dois
                não dispara leave (mesmo elemento pai). Submenu é absolute
                (fora do fluxo), não é cortado pela scrollbar do panel. */}
            {!query.trim() && (
              <div
                className="relative"
                onMouseEnter={() => setGatherSubOpen(true)}
                onMouseLeave={() => setGatherSubOpen(false)}
              >
                <button
                  onClick={() => pick(gatherTotalKey)}
                  className={rowCls(isGatherSubKind)}
                >
                  <span className="truncate">{t("highscoreGatheringSectionLabel")}</span>
                  <i
                    className={`ti ti-chevron-right ml-auto text-zinc-500 transition-transform ${gatherSubOpen ? "rotate-90" : ""}`}
                    aria-hidden="true"
                  />
                </button>
                {gatherSubOpen && (
                  <div className="absolute left-full top-0 z-30 ml-0 w-56 rounded-lg border border-zinc-700 bg-zinc-900 py-1 text-xs shadow-lg">
                    {gatherSubEntries.map(g => (
                      <button key={g.key} onClick={() => pick(g.key)} className={rowCls(kind === g.key)}>
                        <span className="truncate">{g.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="mt-1 border-t border-zinc-800 px-3 pt-1.5 text-[10px] uppercase tracking-wide text-zinc-600">
              {t("highscoreWeaponsSectionLabel")}
            </div>
            {/* Só a seção de armas scrolla — pode ter centenas de entradas.
                specials + coleta são poucas, não scrollam. Antes o panel
                inteiro era overflow-y-auto, e a scrollbar encostava no
                submenu lateral criando um gap que disparava onMouseLeave. */}
            <div className="max-h-72 overflow-y-auto">
              {filteredWeapons.map(w => (
                <button key={w.key} onClick={() => pick(w.key)} className={rowCls(kind === w.key)}>
                  <img src={weaponIcon(w.base)} alt="" width={20} height={20} className="shrink-0" />
                  <span className="truncate">{w.label}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── lista paginada ────────────────────────────────────────────────────────

// memo: a lista de 50 linhas re-renderizava a cada tecla na busca / toggle de
// loading mesmo com as linhas iguais. Props estáveis (row vem do array de
// estado, kind/rank primitivos), então memo pula quando nada mudou.
const RankingRowView = memo(function RankingRowView({ rank, row, highlight, fallbackRegion }: { rank: number; row: RankingRow; highlight?: boolean; fallbackRegion?: string }) {
  // Deriva do DADO, não do kind: pvp_fame/most_battles com scope=player devolvem
  // linhas de jogador (têm albion_id), mas isPlayerKind(kind) é false — o que
  // fazia o frontend tratar como guilda e prependar a tag de aliança no nome.
  const player = !!row.albion_id;
  const region = row.region || fallbackRegion;
  // Aliança fica de fora do link (pedido explícito) — só nome/guilda levam
  // pro perfil, cada linha inteira é UM link só: guilda pro perfil da
  // guilda, jogador pro perfil dele.
  const href = player
    ? (region && REGION_PREFIX[region] ? `/${REGION_PREFIX[region]}/${encodeURIComponent(row.name)}` : null)
    : (row.albion_guild_id ? `/guild/${encodeURIComponent(row.albion_guild_id)}` : null);
  const allianceTag = row.alliance_name ? `[${row.alliance_name}] ` : "";
  // Guilda: aliança vem antes do nome da guilda. Jogador: nome plano, aliança
  // (se houver) vai junto do nome da guilda na subtítulo, não no nome do jogador.
  const title = player ? row.name : `${allianceTag}${row.name}`;
  const sub = player ? (row.guild_name ? `${allianceTag}${row.guild_name}` : null) : null;

  const content = (
    <>
      <span className="w-8 shrink-0 text-center text-xs font-bold text-zinc-500">{rank}</span>
      {row.weapon_base && (
        <img src={weaponIcon(row.weapon_base)} alt="" width={28} height={28} className="shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-zinc-200">{title}</div>
        {sub && <div className="truncate text-[11px] text-zinc-600">{sub}</div>}
      </div>
      <span className="shrink-0 text-sm font-semibold text-amber-400/80 tabular-nums">{silverShort(row.value)}</span>
    </>
  );

  if (href) {
    return (
      <button onClick={() => navigate(href)} className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/40 ${highlight ? "border-amber-500 bg-amber-500/10 ring-1 ring-amber-500/40" : "border-zinc-800 bg-zinc-900/60"}`}>
        {content}
      </button>
    );
  }
  return (
    <div className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 ${highlight ? "border-amber-500 bg-amber-500/10 ring-1 ring-amber-500/40" : "border-zinc-800 bg-zinc-900/60"}`}>
      {content}
    </div>
  );
});

export default function HighscoresPage({ initialWindow = "alltime", initialKind, initialRegions, highlightPlayer, initialRank }: {
  initialWindow?: RankingWindow;
  initialKind?: RankingKind;
  initialRegions?: string;
  highlightPlayer?: string;
  initialRank?: number;
}) {
  const t = useT();
  const { servers } = useLang();
  const [initial] = useState(() => ({ initialWindow, initialKind, initialRegions, highlightPlayer, initialRank }));
  const regions = useMemo(() => initial.initialRegions ?? servers.map(s => SERVER_TO_REGION[s]).join(","), [servers, initial]);

  const [weapons, setWeapons] = useState<WeaponDef[]>([]);
  useEffect(() => {
    // guarda: resposta de erro (404/500) vem como objeto, não lista — sem isso, weapons.map() derruba a árvore toda
    fetch(`${API}/highscores/weapons`).then(r => r.json()).then(d => setWeapons(Array.isArray(d) ? d : [])).catch(() => setWeapons([]));
  }, []);

  const [kind, setKind] = useState<RankingKind>(initial.initialKind ?? "pvp_fame");
  const [scopeView, setScopeView] = useState<"guild" | "player">(
    GUILD_DEFAULT_KINDS.has(initial.initialKind ?? "pvp_fame") ? "guild" : "player",
  );
  const [window_, setWindow] = useState<RankingWindow>(
    ALLTIME_ONLY_KINDS.has(initial.initialKind ?? "pvp_fame") ? "alltime" : initial.initialWindow,
  );
  const [currentSeason, setCurrentSeason] = useState<number | null>(null);
  const [historicalSeasons, setHistoricalSeasons] = useState<number[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState<number>(initial.initialRank != null ? Math.floor((initial.initialRank - 1) / PAGE_SIZE) : 0);
  const [rows, setRows] = useState<RankingRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    fetch(`${API}/highscores/seasons?regions=${regions}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => {
        if (!alive) return;
        const current = [...new Set(Object.values(d.current_seasons ?? {}).filter(Number.isInteger))] as number[];
        setCurrentSeason(current.length === 1 ? current[0] : null);
        setHistoricalSeasons(Array.isArray(d.historical_seasons) ? d.historical_seasons : []);
      })
      .catch(() => { if (alive) { setCurrentSeason(null); setHistoricalSeasons([]); } });
    return () => { alive = false; };
  }, [regions]);

  const filtersRef = useRef({ kind, window_, search, regions, scopeView });
  useEffect(() => {
    const previous = filtersRef.current;
    // Kind mudou → reset scopeView pro default do novo kind
    if (previous.kind !== kind) {
      setScopeView(GUILD_DEFAULT_KINDS.has(kind) ? "guild" : "player");
    }
    filtersRef.current = { kind, window_, search, regions, scopeView };
    if (previous.kind !== kind || previous.window_ !== window_ || previous.search !== search || previous.regions !== regions || previous.scopeView !== scopeView) setPage(0);
  }, [kind, window_, search, regions, scopeView]);

  const apiScope = !SCOPE_KINDS.has(kind) ? "default"
    : GUILD_DEFAULT_KINDS.has(kind) ? (scopeView === "player" ? "player" : "default")
    : (scopeView === "guild" ? "guild" : "default");

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({
      kind, regions, window: window_, limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE),
    });
    if (search) params.set("search", search);
    if (apiScope !== "default") params.set("scope", apiScope);
    let alive = true;
    fetch(`${API}/highscores/rankings?${params}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => { if (alive) { setRows(Array.isArray(d.rows) ? d.rows : []); setTotal(Number(d.total) || 0); } })
      .catch(() => { if (alive) { setRows([]); setTotal(0); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [kind, window_, search, page, regions, apiScope]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageNums = useMemo(() => {
    const current = page + 1;
    const start = Math.max(1, Math.min(current - 2, totalPages - 4));
    const to = Math.min(totalPages, start + 4);
    return Array.from({ length: to - start + 1 }, (_, i) => start + i);
  }, [page, totalPages]);

  const fallbackRegion = regions.split(",")[0] || undefined;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <GlobalSearch />

      <HighlightsRow regions={regions} />

      {rows === null ? <FilterRowSkeleton /> : (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <RankingTypeSelect
            kind={kind}
            onChange={next => {
              setKind(next);
              if (ALLTIME_ONLY_KINDS.has(next)) setWindow("alltime");
            }}
            weapons={weapons}
          />
          {SCOPE_KINDS.has(kind) && (
            <div className="flex overflow-hidden rounded-lg border border-zinc-700">
              <button onClick={() => setScopeView("guild")} className={`px-3 py-1.5 text-xs ${scopeView === "guild" ? "bg-amber-500/10 text-amber-300" : "text-zinc-400 hover:text-zinc-200"}`}>
                <i className="ti ti-building-community" style={{ fontSize: 12 }} /> {t("highscoreScopeGuild")}
              </button>
              <button onClick={() => setScopeView("player")} className={`px-3 py-1.5 text-xs ${scopeView === "player" ? "bg-amber-500/10 text-amber-300" : "text-zinc-400 hover:text-zinc-200"}`}>
                <i className="ti ti-user" style={{ fontSize: 12 }} /> {t("highscoreScopePlayer")}
              </button>
            </div>
          )}
          <select
            value={window_}
            onChange={e => setWindow(e.target.value as RankingWindow)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200 outline-none hover:border-zinc-500 focus:border-amber-500"
          >
            {!ALLTIME_ONLY_KINDS.has(kind) && <option value="week">{t("highscoreDurationWeek")}</option>}
            {!ALLTIME_ONLY_KINDS.has(kind) && <option value="month">{t("highscoreDurationMonth")}</option>}
            {!ALLTIME_ONLY_KINDS.has(kind) && (
              <option value="season">
                {t("highscoreDurationSeason")}{currentSeason ? ` (${t("highscoreSeasonLabel")} ${currentSeason})` : ""}
              </option>
            )}
            <option value="alltime">{t("highscoreDurationAllTime")}</option>
            {!ALLTIME_ONLY_KINDS.has(kind) && historicalSeasons.map(season => (
              <option key={season} value={`season:${season}`}>{t("highscoreSeasonLabel")} {season}</option>
            ))}
          </select>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t("highscoreSearchPlaceholder")}
            className="min-w-[160px] flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-amber-500"
          />
        </div>
      )}

      {!loading && rows?.length === 0 && <div className="py-8 text-center text-sm text-zinc-500">{t("highscoreNoData")}</div>}

      <div className="flex flex-col gap-1.5">
        {loading
          ? Array.from({ length: RANKING_SKELETON_ROWS }, (_, i) => <RankingRowSkeleton key={i} />)
          : rows?.map((row, i) => (
              <RankingRowView key={row.albion_guild_id ?? row.albion_id ?? i} rank={row.rank} row={row} highlight={!!initial.highlightPlayer && row.albion_id === initial.highlightPlayer} fallbackRegion={fallbackRegion} />
            ))}
      </div>

      {!loading && rows && rows.length > 0 && (
        <div className="mt-4 flex items-center justify-end gap-3 text-xs">
          <div className="flex items-center gap-1">
            <button disabled={page <= 0} onClick={() => setPage(0)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">«</button>
            <button disabled={page <= 0} onClick={() => setPage(p => p - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">‹</button>
            {pageNums.map(n => (
              <button
                key={n}
                onClick={() => setPage(n - 1)}
                className={`px-2 py-1 rounded ${n === page + 1 ? "bg-amber-500/10 text-amber-300 border border-amber-500" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                {n}
              </button>
            ))}
            <button disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">›</button>
            <button disabled={page + 1 >= totalPages} onClick={() => setPage(totalPages - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">»</button>
          </div>
          <span className="text-zinc-500">{total}</span>
        </div>
      )}
    </div>
  );
}
