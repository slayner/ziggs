import { useEffect, useMemo, useRef, useState } from "react";
import { itemRenderUrl } from "../data/albion-items";
import {
  getMarketCatalog, getMarketSnapshot, getMarketHistory, fetchRetry,
  type MarketCatalogItem, type MarketSnapshotRow, type MarketHistoryBucket,
} from "../api";
import { useT, useLang, useServer, type TKey } from "../i18n";
import { silverShort } from "../lib/format";

// Bucket unificado (nosso backend OU fallback AODP), sempre em ms epoch.
type Point = { t: number; price: number; count: number };
type Source = "ziggs" | "aodp" | "none";

const TIMESCALES = [
  { id: 0, key: "mkt24h" as const },
  { id: 1, key: "mkt7d" as const },
  { id: 2, key: "mkt4w" as const },
];
const DEFAULT_TIMESCALE = 2; // 4 semanas

// Categorias na ordem do mercado do Albion; "vanity" é o mercado de skins.
const CATEGORIES: { id: string; key: TKey; icon: string }[] = [
  { id: "all", key: "mktCatAll", icon: "ti-apps" },
  { id: "weapons", key: "mktCatWeapons", icon: "ti-sword" },
  { id: "offhand", key: "mktCatOffhand", icon: "ti-shield" },
  { id: "armor", key: "mktCatArmor", icon: "ti-shirt" },
  { id: "accessories", key: "mktCatAccessories", icon: "ti-bag" },
  { id: "consumables", key: "mktCatConsumables", icon: "ti-flask" },
  { id: "mounts", key: "mktCatMounts", icon: "ti-horse" },
  { id: "resources", key: "mktCatResources", icon: "ti-pick" },
  { id: "farm", key: "mktCatFarm", icon: "ti-plant-2" },
  { id: "furniture", key: "mktCatFurniture", icon: "ti-armchair" },
  { id: "tools", key: "mktCatTools", icon: "ti-tools" },
  { id: "vanity", key: "mktCatVanity", icon: "ti-sparkles" },
  { id: "other", key: "mktCatOther", icon: "ti-dots" },
];

const PAGE = 50;
// Piso de demanda pros rankings de variação: sem ele, um item ilíquido com
// 2 vendas domina o topo com um % sem significado.
const MOVER_MIN_DEMAND = 100;
const MOVER_COUNT = 8;

type SortKey = "name" | "price" | "margin" | "demand";

// Catálogo (global) + snapshot POR REGIÃO cacheados no módulo. Trocar de
// servidor no site refaz o snapshot da nova região; a UI nunca dispara
// consulta externa pra montar rankings/preços.
let _catalog: MarketCatalogItem[] | null = null;
const _snapCache = new Map<string, Map<string, MarketSnapshotRow>>();

// ── Busca esperta ────────────────────────────────────────────────────────────

function normalize(s: string): string {
  return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

// Levenshtein com corte por `max` — devolve max+1 assim que passa do limite.
function lev(a: string, b: string, max: number): number {
  const al = a.length, bl = b.length;
  if (Math.abs(al - bl) > max) return max + 1;
  let prev = Array.from({ length: bl + 1 }, (_, j) => j);
  for (let i = 1; i <= al; i++) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= bl; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      cur[j] = v;
      if (v < best) best = v;
    }
    if (best > max) return max + 1;
    prev = cur;
  }
  return prev[bl];
}

type SearchEntry = { it: MarketCatalogItem; text: string; words: string[] };

// Um token casa se: é substring do texto normalizado (nome + id), OU (típos)
// está a ≤1-2 de edição de alguma palavra do nome. Aceita palavra quebrada
// (substring) e errada (fuzzy).
function tokenMatches(entry: SearchEntry, token: string): boolean {
  if (entry.text.includes(token)) return true;
  if (token.length < 4) return false;
  const maxDist = token.length >= 7 ? 2 : 1;
  for (const w of entry.words) {
    if (Math.abs(w.length - token.length) > maxDist) continue;
    if (lev(w, token, maxDist) <= maxDist) return true;
  }
  return false;
}

// "7.4", "t7.4", "t7", "7" → tier 7 (+ encant 4 quando presente). O resto vira
// termo de texto pra busca fuzzy.
function parseQuery(raw: string): { tier: number; enchant: number; tokens: string[] } {
  const norm = normalize(raw).trim();
  if (!norm) return { tier: 0, enchant: -1, tokens: [] };
  let tier = 0, enchant = -1;
  const tokens: string[] = [];
  for (const tk of norm.split(/\s+/)) {
    const m = tk.match(/^t?([1-8])(?:[.,]([0-4]))?$/);
    if (m) { tier = Number(m[1]); if (m[2] !== undefined) enchant = Number(m[2]); }
    else tokens.push(tk);
  }
  return { tier, enchant, tokens };
}

// ── Histórico ────────────────────────────────────────────────────────────────

// O timestamp do bucket pode vir em .NET ticks, unix ms ou unix s — detecta
// pela magnitude e normaliza pra ms epoch. Só afeta os rótulos do eixo.
function tsToMs(raw: number): number {
  if (raw > 1e15) return Math.round(raw / 10000) - 62135596800000; // .NET ticks
  if (raw > 1e11) return raw;                                      // unix ms
  if (raw > 1e8) return raw * 1000;                               // unix s
  return raw;
}

function bucketToPoint(b: MarketHistoryBucket): Point {
  return { t: tsToMs(b.bucket_ts), price: b.avg_price, count: b.item_count };
}

// region = servidor do Albion (west|east|europe) — é o mesmo valor pro nosso
// backend e pro subdomínio do AODP. Qualidade fixa em 1 (não há mais seletor Q).
async function loadHistory(
  itemId: string, region: string, timescale: number,
): Promise<{ points: Point[]; source: Source }> {
  try {
    const mine = await getMarketHistory(itemId, region, 1, timescale);
    if (mine.buckets.length >= 2) {
      return { points: mine.buckets.map(bucketToPoint), source: "ziggs" };
    }
  } catch { /* backend indisponível → tenta AODP */ }

  try {
    const scale = timescale === 0 ? 1 : timescale === 2 ? 168 : 24;
    const url = `https://${region}.albion-online-data.com/api/v2/stats/history/${encodeURIComponent(itemId)}` +
      `?time-scale=${scale}&qualities=1`;
    const res = await fetchRetry(url);
    if (res.ok) {
      const entries: { data: { avg_price: number; item_count: number; timestamp: string }[] }[] = await res.json();
      const byT = new Map<number, { price: number[]; count: number }>();
      for (const e of entries) for (const d of e.data) {
        if (!d.avg_price) continue;
        const t = Date.parse(d.timestamp);
        const b = byT.get(t) ?? { price: [], count: 0 };
        b.price.push(d.avg_price); b.count += d.item_count || 0;
        byT.set(t, b);
      }
      const points = [...byT.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([t, b]) => ({ t, price: Math.round(b.price.reduce((x, y) => x + y, 0) / b.price.length), count: b.count }));
      if (points.length >= 2) return { points, source: "aodp" };
    }
  } catch { /* sem dado em lugar nenhum */ }

  return { points: [], source: "none" };
}

function tierOf(id: string): number | null {
  const m = id.match(/^T(\d)_/);
  return m ? Number(m[1]) : null;
}

type Mover = MarketCatalogItem & { snap: MarketSnapshotRow };
type Selection = { item: MarketCatalogItem; enchant: number };

export default function MarketPage() {
  const t = useT();
  const { lang } = useLang();
  const { server } = useServer(); // "west" | "east" | "europe" = região do mercado
  const [catalog, setCatalog] = useState<MarketCatalogItem[] | null>(_catalog);
  const [snap, setSnap] = useState<Map<string, MarketSnapshotRow> | null>(_snapCache.get(server) ?? null);
  const [loadErr, setLoadErr] = useState(false);
  const [cat, setCat] = useState("all");
  const [query, setQuery] = useState("");
  const [tierSel, setTierSel] = useState(0); // dropdown; 0 = todos
  const [sort, setSort] = useState<SortKey>("name");
  const [sortDesc, setSortDesc] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [selected, setSelected] = useState<Selection | null>(null);

  // Catálogo (global, 1 fetch por sessão).
  useEffect(() => {
    if (_catalog) return;
    getMarketCatalog()
      .then(c => { _catalog = c; setCatalog(c); })
      .catch(() => setLoadErr(true));
  }, []);

  // Snapshot da região ativa — refaz ao trocar de servidor no site.
  useEffect(() => {
    const cached = _snapCache.get(server);
    if (cached) { setSnap(cached); return; }
    setSnap(null); // limpa enquanto carrega a nova região
    let alive = true;
    getMarketSnapshot(server)
      .then(rows => {
        const m = new Map(rows.map(r => [r.id, r]));
        _snapCache.set(server, m);
        if (alive) setSnap(m);
      })
      .catch(() => { if (alive) setSnap(new Map()); });
    return () => { alive = false; };
  }, [server]);

  // Fecha o painel com Esc.
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSelected(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  const localName = (it: MarketCatalogItem) => (lang === "en" ? it.en : it.pt) || it.en;

  // Índice de busca pré-computado (nome normalizado + palavras) por item.
  const searchIndex = useMemo<SearchEntry[] | null>(() => {
    if (!catalog) return null;
    return catalog.map(it => {
      const text = normalize(`${localName(it)} ${it.id}`);
      return { it, text, words: text.split(/[^a-z0-9]+/).filter(Boolean) };
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, lang]);

  // ── Rankings de oportunidade (a cara da aba) ──────────────────────────────
  const movers = useMemo(() => {
    if (!catalog || !snap || snap.size === 0) return null;
    const byId = new Map(catalog.map(c => [c.id, c]));
    const joined: Mover[] = [];
    for (const s of snap.values()) {
      const c = byId.get(s.id);
      if (c && s.price > 0) joined.push({ ...c, snap: s });
    }
    const liquid = joined.filter(m => m.snap.demand >= MOVER_MIN_DEMAND);
    const gainers = liquid.filter(m => m.snap.change_pct > 0)
      .sort((a, b) => b.snap.change_pct - a.snap.change_pct).slice(0, MOVER_COUNT);
    const losers = liquid.filter(m => m.snap.change_pct < 0)
      .sort((a, b) => a.snap.change_pct - b.snap.change_pct).slice(0, MOVER_COUNT);
    const hot = [...joined].sort((a, b) => b.snap.demand - a.snap.demand).slice(0, MOVER_COUNT);
    return { gainers, losers, hot, total: joined.length };
  }, [catalog, snap]);

  const setSortKey = (k: SortKey) => {
    if (sort === k) { setSortDesc(d => !d); return; }
    setSort(k);
    setSortDesc(k !== "name"); // colunas numéricas começam desc (maior primeiro)
  };

  const parsed = useMemo(() => parseQuery(query), [query]);
  const effTier = parsed.tier || tierSel;

  const filtered = useMemo(() => {
    if (!searchIndex) return [];
    // Frescor: o backend só devolve no snapshot itens com preço visto nos
    // últimos 3 dias da região; item fora do snapshot some da lista. Sem
    // snapshot carregado ainda (map vazio) não esconde nada.
    const freshOnly = !!snap && snap.size > 0;
    const list = searchIndex.filter(e => {
      if (freshOnly && !snap!.has(e.it.id)) return false;
      if (cat !== "all" && e.it.c !== cat) return false;
      if (effTier !== 0 && tierOf(e.it.id) !== effTier) return false;
      for (const tk of parsed.tokens) if (!tokenMatches(e, tk)) return false;
      return true;
    }).map(e => e.it);

    const num = (it: MarketCatalogItem): number => {
      const s = snap?.get(it.id);
      if (!s) return -1;
      return sort === "price" ? s.price : sort === "margin" ? s.change_pct : s.demand;
    };
    const sorted = [...list];
    if (sort === "name") {
      sorted.sort((a, b) => localName(a).localeCompare(localName(b)));
    } else {
      sorted.sort((a, b) => num(b) - num(a));
    }
    if (sortDesc !== (sort !== "name")) sorted.reverse();
    return sorted;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchIndex, snap, cat, effTier, parsed.tokens.join(" "), sort, sortDesc, lang]);

  useEffect(() => { setShown(PAGE); }, [cat, query, tierSel]);

  const open = (item: MarketCatalogItem) => setSelected({ item, enchant: parsed.enchant >= 0 ? parsed.enchant : 0 });

  return (
    <div className="market-page">
      <div className="market-head">
        <h1>{t("marketTitle")}</h1>
        <p className="market-sub">{t("marketSub")}</p>
      </div>

      {/* ── Oportunidades ─────────────────────────────────────────────────── */}
      {movers ? (
        <div className="market-movers">
          <MoverCard
            title={t("mktMoversGainers")} icon="ti-trending-up" tone="up"
            rows={movers.gainers} localName={localName} onPick={open}
            metric={m => <span className="mover-pct up">+{m.snap.change_pct.toFixed(1)}%</span>}
          />
          <MoverCard
            title={t("mktMoversLosers")} icon="ti-trending-down" tone="down"
            rows={movers.losers} localName={localName} onPick={open}
            metric={m => <span className="mover-pct down">{m.snap.change_pct.toFixed(1)}%</span>}
          />
          <MoverCard
            title={t("mktMoversHot")} icon="ti-flame" tone="hot"
            rows={movers.hot} localName={localName} onPick={open}
            metric={m => <span className="mover-demand">{silverShort(m.snap.demand)}</span>}
          />
        </div>
      ) : (
        <div className="market-empty">{loadErr ? t("marketNoData") : t("loading")}</div>
      )}

      {movers && (
        <div className="market-pulse">{t("mktPulse").replace("{n}", movers.total.toLocaleString())}</div>
      )}

      {/* ── Explorador: busca + categorias + tabela ───────────────────────── */}
      {catalog && (
        <>
          <h2 className="market-section-title">{t("mktCatalogTitle")}</h2>

          <div className="market-searchbar">
            <i className="ti ti-search" aria-hidden="true" />
            <input
              type="text" value={query}
              placeholder={t("marketSearchPlaceholder")}
              onChange={e => setQuery(e.target.value)}
            />
            {query && <button className="market-search-clear" onClick={() => setQuery("")} aria-label="clear">✕</button>}
            <select value={tierSel} onChange={e => setTierSel(Number(e.target.value))} className="market-tier-select">
              <option value={0}>{t("marketTier")}</option>
              {[1, 2, 3, 4, 5, 6, 7, 8].map(tt => <option key={tt} value={tt}>T{tt}</option>)}
            </select>
          </div>

          <div className="market-cats">
            {CATEGORIES.map(c => (
              <button key={c.id} className={`market-cat ${cat === c.id ? "active" : ""}`} onClick={() => setCat(c.id)}>
                <i className={`ti ${c.icon}`} aria-hidden="true" />
                <span>{t(c.key)}</span>
              </button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <div className="market-empty">{t("marketNoResults")}</div>
          ) : (
            <>
              <div className="market-list">
                <div className="market-list-header">
                  <span className="mlh-spacer" />
                  <SortBtn label={t("mktColName")} k="name" sort={sort} desc={sortDesc} onClick={setSortKey} align="left" />
                  <SortBtn label={t("mktColPrice")} k="price" sort={sort} desc={sortDesc} onClick={setSortKey} />
                  <SortBtn label={t("mktColMargin")} k="margin" sort={sort} desc={sortDesc} onClick={setSortKey} />
                  <SortBtn label={t("mktColDemand")} k="demand" sort={sort} desc={sortDesc} onClick={setSortKey} />
                </div>
                {filtered.slice(0, shown).map(it => {
                  const s = snap?.get(it.id);
                  return (
                    <button key={it.id} className="market-row-head" onClick={() => open(it)}>
                      <img src={itemRenderUrl(it.id)} alt="" width={40} height={40} loading="lazy" />
                      <span className="market-row-name">{localName(it)}</span>
                      <span className="market-col price">{s && s.price > 0 ? silverShort(s.price) : "—"}</span>
                      <span className={`market-col margin ${s && s.change_pct > 0 ? "up" : s && s.change_pct < 0 ? "down" : ""}`}>
                        {s && s.price > 0 ? `${s.change_pct > 0 ? "+" : ""}${s.change_pct.toFixed(1)}%` : "—"}
                      </span>
                      <span className="market-col demand">{s && s.demand > 0 ? silverShort(s.demand) : "—"}</span>
                    </button>
                  );
                })}
              </div>
              {filtered.length > shown && (
                <button className="market-more" onClick={() => setShown(s => s + PAGE * 2)}>
                  {t("marketShowMore")} ({(filtered.length - shown).toLocaleString()})
                </button>
              )}
            </>
          )}
        </>
      )}

      {/* ── Modal de detalhe ──────────────────────────────────────────────── */}
      {selected && (
        <div className="market-modal-backdrop" onClick={() => setSelected(null)}>
          <div className="market-modal" onClick={e => e.stopPropagation()}>
            <ItemDetail
              item={selected.item} initialEnchant={selected.enchant}
              region={server} localName={localName} onClose={() => setSelected(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function MoverCard({ title, icon, tone, rows, localName, onPick, metric }: {
  title: string; icon: string; tone: "up" | "down" | "hot";
  rows: Mover[]; localName: (it: MarketCatalogItem) => string;
  onPick: (it: MarketCatalogItem) => void;
  metric: (m: Mover) => React.ReactNode;
}) {
  const t = useT();
  return (
    <div className={`mover-card ${tone}`}>
      <div className="mover-card-title">
        <i className={`ti ${icon}`} aria-hidden="true" /> {title}
      </div>
      {rows.length === 0 ? (
        <div className="mover-empty">{t("mktMoversEmpty")}</div>
      ) : rows.map((m, i) => (
        <button key={m.id} className="mover-row" onClick={() => onPick(m)}>
          <span className="mover-rank">{i + 1}</span>
          <img src={itemRenderUrl(m.id)} alt="" width={30} height={30} loading="lazy" />
          <span className="mover-name">{localName(m)}</span>
          <span className="mover-price">{silverShort(m.snap.price)}</span>
          {metric(m)}
        </button>
      ))}
    </div>
  );
}

function SortBtn({ label, k, sort, desc, onClick, align }: {
  label: string; k: SortKey; sort: SortKey; desc: boolean;
  onClick: (k: SortKey) => void; align?: "left";
}) {
  const active = sort === k;
  return (
    <button className={`mlh-btn ${active ? "active" : ""} ${align === "left" ? "left" : ""}`} onClick={() => onClick(k)}>
      {label}{active ? (desc ? " ↓" : " ↑") : ""}
    </button>
  );
}

// Detalhe de um item: encantamento + escala de tempo + faixa de stats + gráfico.
function ItemDetail({ item, initialEnchant, region, localName, onClose }: {
  item: MarketCatalogItem;
  initialEnchant: number;
  region: string;
  localName: (it: MarketCatalogItem) => string;
  onClose: () => void;
}) {
  const t = useT();
  const [enchant, setEnchant] = useState(initialEnchant);
  const [timescale, setTimescale] = useState(DEFAULT_TIMESCALE);
  const [points, setPoints] = useState<Point[] | null>(null);
  const [source, setSource] = useState<Source>("none");
  const [loading, setLoading] = useState(false);

  // Encant só existe pra itens com tier (equipamento/recursos T4+).
  const canEnchant = /^T[4-8]_/.test(item.id);
  const itemId = canEnchant && enchant > 0 ? `${item.id}@${enchant}` : item.id;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    loadHistory(itemId, region, timescale)
      .then(r => { if (alive) { setPoints(r.points); setSource(r.source); } })
      .catch(() => { if (alive) { setPoints([]); setSource("none"); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [itemId, region, timescale]);

  // Stats agregados do período carregado (não do snapshot — do gráfico atual).
  const stats = useMemo(() => {
    if (!points || points.length < 1) return null;
    const prices = points.map(p => p.price);
    const cur = prices[prices.length - 1];
    const first = prices[0];
    const change = first > 0 ? ((cur - first) / first) * 100 : 0;
    return {
      cur, min: Math.min(...prices), max: Math.max(...prices),
      vol: points.reduce((s, p) => s + p.count, 0), change,
    };
  }, [points]);

  return (
    <>
      <div className="market-modal-head">
        <img src={itemRenderUrl(itemId)} alt="" width={56} height={56} />
        <div className="market-modal-title">
          <div className="market-modal-name">{localName(item)}</div>
          <div className="market-modal-controls">
            {canEnchant && (
              <div className="seg">
                {[0, 1, 2, 3, 4].map(e => (
                  <button key={e} className={`seg-btn ${enchant === e ? "active" : ""}`} onClick={() => setEnchant(e)}>.{e}</button>
                ))}
              </div>
            )}
            <div className="seg">
              {TIMESCALES.map(ts => (
                <button key={ts.id} className={`seg-btn ${timescale === ts.id ? "active" : ""}`} onClick={() => setTimescale(ts.id)}>
                  {t(ts.key)}
                </button>
              ))}
            </div>
            {source !== "none" && !loading && (
              <span className={`market-source ${source}`}>{source === "ziggs" ? t("marketSrcZiggs") : t("marketSrcAodp")}</span>
            )}
          </div>
        </div>
        <button className="market-modal-close" onClick={onClose} aria-label="close">✕</button>
      </div>

      {stats && !loading && (
        <div className="market-stat-strip">
          <ModalStat label={t("mktStatCurrent")} value={silverShort(stats.cur)} strong />
          <ModalStat label={t("mktStatChange")} value={`${stats.change > 0 ? "+" : ""}${stats.change.toFixed(1)}%`}
            tone={stats.change > 0 ? "up" : stats.change < 0 ? "down" : undefined} />
          <ModalStat label={t("mktStatLow")} value={silverShort(stats.min)} />
          <ModalStat label={t("mktStatHigh")} value={silverShort(stats.max)} />
          <ModalStat label={t("mktStatVol")} value={silverShort(stats.vol)} tone="vol" />
        </div>
      )}

      <MarketChart points={points} loading={loading} emptyLabel={t("marketNoData")} />
    </>
  );
}

function ModalStat({ label, value, tone, strong }: {
  label: string; value: string; tone?: "up" | "down" | "vol"; strong?: boolean;
}) {
  return (
    <div className="market-stat">
      <div className="market-stat-label">{label}</div>
      <div className={`market-stat-value ${tone ?? ""} ${strong ? "strong" : ""}`}>{value}</div>
    </div>
  );
}

// Gráfico de linha de preço absoluto + barras de volume (série única).
function MarketChart({ points, loading, emptyLabel }: {
  points: Point[] | null; loading: boolean; emptyLabel: string;
}) {
  const t = useT();
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const VW = 860, H = 340, padL = 68, padR = 22, padTop = 18, padBot = 34;
  const plotW = VW - padL - padR, plotH = H - padTop - padBot;

  if (loading) return <div className="market-chart-box"><div className="market-chart-loading">{t("loading")}</div></div>;
  if (!points || points.length < 2) return <div className="market-chart-box"><div className="market-chart-empty">{emptyLabel}</div></div>;

  const prices = points.map(p => p.price);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const range = hi - lo || hi || 1;
  const yLo = lo - range * 0.1, yHi = hi + range * 0.1, yRange = yHi - yLo || 1;
  const t0 = points[0].t, t1 = points[points.length - 1].t, tRange = t1 - t0 || 1;

  const cx = (tms: number) => padL + ((tms - t0) / tRange) * plotW;
  const cy = (v: number) => padTop + (1 - (v - yLo) / yRange) * plotH;

  const maxCount = Math.max(...points.map(p => p.count), 1);
  const barAreaH = plotH * 0.32;
  const baseY = padTop + plotH;
  const barW = Math.max(2, Math.min(18, (plotW / points.length) * 0.55));
  const barH = (c: number) => (c / maxCount) * barAreaH;

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${cx(p.t).toFixed(1)},${cy(p.price).toFixed(1)}`).join(" ");
  const area = `${path} L${cx(t1).toFixed(1)},${(padTop + plotH).toFixed(1)} L${cx(t0).toFixed(1)},${(padTop + plotH).toFixed(1)} Z`;

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => yLo + f * yRange);
  const fmtDate = (ms: number) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });

  const onMove = (e: React.MouseEvent) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = ((e.clientX - rect.left) / rect.width) * VW;
    let best = 0, bd = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(cx(points[i].t) - x);
      if (d < bd) { bd = d; best = i; }
    }
    setHover(best);
  };

  const hv = hover !== null ? points[hover] : null;

  return (
    <div className="market-chart-box">
      <svg ref={svgRef} viewBox={`0 0 ${VW} ${H}`} width="100%" style={{ display: "block" }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={padL + plotW} y1={cy(v)} y2={cy(v)} stroke="var(--border)" strokeWidth="0.5" opacity="0.6" />
            <text x={padL - 8} y={cy(v) + 3} textAnchor="end" fontSize="11" fill="var(--muted)">{silverShort(Math.round(v))}</text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
          const ms = t0 + f * tRange;
          return <text key={i} x={padL + f * plotW} y={padTop + plotH + 20} textAnchor="middle" fontSize="11" fill="var(--muted)">{fmtDate(ms)}</text>;
        })}
        {points.map((p, i) => p.count > 0 ? (
          <rect key={`vb${i}`}
            x={cx(p.t) - barW / 2} y={baseY - barH(p.count)}
            width={barW} height={barH(p.count)}
            fill="var(--info)" opacity={hover === i ? 0.75 : 0.3} />
        ) : null)}
        <text x={padL + plotW} y={baseY - barAreaH - 4} textAnchor="end" fontSize="10" fill="var(--info)" opacity="0.8">
          {maxCount.toLocaleString()}
        </text>
        <path d={area} fill="var(--gold-soft)" opacity="0.5" />
        <path d={path} fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {hv && (
          <g>
            <line x1={cx(hv.t)} x2={cx(hv.t)} y1={padTop} y2={padTop + plotH} stroke="var(--border-strong)" strokeWidth="1" />
            <circle cx={cx(hv.t)} cy={cy(hv.price)} r="4" fill="var(--gold)" stroke="var(--bg)" strokeWidth="1.5" />
          </g>
        )}
      </svg>
      <div className="market-legend">
        <span className="market-legend-item"><span className="market-legend-swatch price" /> {t("marketPrice")}</span>
        <span className="market-legend-item"><span className="market-legend-swatch vol" /> {t("marketSold")}</span>
      </div>
      {hv && (
        <div className="market-tip">
          <strong>{silverShort(hv.price)}</strong>
          <span>{new Date(hv.t).toLocaleDateString()} · {t("marketSold")}: {hv.count.toLocaleString()}</span>
        </div>
      )}
    </div>
  );
}
