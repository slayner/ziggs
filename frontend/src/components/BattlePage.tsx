import { useEffect, useMemo, useRef, useState } from "react";
import { itemRenderUrl, ITEM_BY_ID, NO_WEAPON_ICON_ID, is2H } from "../data/albion-items";
import { imgRetry } from "../api";
import { navigate, navigateReplace } from "../router";
import { EquipGrid, PriceHistoryChart, type DraftEquip } from "./CompBuilder";
import { useT, type TKey, factionTag as makeTag } from "../i18n";
import { searchIncludes } from "../lib/search";
import { Panel, PanelHeader } from "./Panel";

const API = import.meta.env.DEV ? "http://localhost:8000" : "";

type Build = Record<string, string>;

interface Participant {
  albion_player_id: string;
  name: string;
  guild_id: string | null;
  guild_name: string | null;
  alliance_id: string | null;
  alliance_name: string | null;
  side: "A" | "B" | "rats";
  kills: number;
  deaths: number;
  kill_fame: number;
  death_fame: number;
  // null = build nunca carregou (ou a API não reportou IP) — não entra na média.
  ip: number | null;
  damage_dealt: number;
  damage_taken: number;
  healing_done: number;
  equipment: Build[] | null;
  role: string | null;
}

interface Side {
  label: string;
  player_count: number;
  score: number;
  factions: { guild_id: string | null; guild_name: string | null; alliance_name: string | null }[];
  participants: Participant[];
}

interface Highlight { name: string; value: number; guild_name: string | null; alliance_name: string | null; albion_player_id?: string; region?: string }

interface InventoryItem { item_id: string; count: number }

interface KillEvent {
  t: string; side: string; faction: string; victim_faction: string | null;
  killer_guild: string | null; killer_alliance: string | null;
  victim_guild: string | null; victim_alliance: string | null;
  killer: string; victim: string | null;
  fame: number;
  weapon: string | null; victim_weapon: string | null;
  killer_equipment: Build | null; victim_equipment: Build | null;
  killer_inventory: InventoryItem[] | null; victim_inventory: InventoryItem[] | null;
}

interface BattleDetail {
  public_id: string;
  battle_count: number;
  region: string;
  start_time: string;
  end_time: string | null;
  total_fame: number;
  kill_count: number;
  cluster: string | null;
  players_total: number;
  sides: Side[];
  rats: { participants: Participant[]; player_count: number };
  highlights: {
    top_kills?: Highlight | null;
    top_fame?: Highlight | null;
    top_healing?: Highlight | null;
    top_death_fame?: Highlight | null;
  };
  kill_timeline: KillEvent[];
  unresolved_ids?: string[];
  found_by?: string[];
}

function useRegionLabels(): Record<string, string> {
  const t = useT();
  return { europe: "Europe", americas: "Americas", asia: "Asia", multi: t("regionMultiple") };
}
const EQUIP_SLOTS = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "mount", "bag", "food", "potion"];
// ponytail: pedido explícito — montaria/capa/comida/pot/bolsa não diferenciam a "build" de uma role.
const COMPOSITION_SLOTS = ["weapon", "offhand", "helmet", "armor", "boots"];

function useRoleLabels(): Record<string, string> {
  const t = useT();
  return {
    tank: "Tank", healer: "Healer", support: t("roleSupport"), dps: "DPS", pierce: "Pierce",
    battlemount: "BM", utility: t("roleUtility"), unknown: t("roleUnknownBuild"),
  };
}
// pro search aceitar o termo em inglês mesmo quando o label do site é PT (igual às comps).
const ROLE_LABELS_EN: Record<string, string> = {
  tank: "Tank", healer: "Healer", support: "Support", dps: "DPS", pierce: "Pierce",
  battlemount: "Battle mount", utility: "Utility", unknown: "Unknown build",
};
// mesmas cores das funções padrão da comp builder (DEFAULT_FN_TYPES em
// CompBuilder.tsx) — lá não existe pierce, então fica com o roxo que era do
// suporte antes aqui.
// paleta compartilhada entre o horizonte de eventos e a lista de alianças —
// mesma cor pra mesma guilda/aliança nos dois lugares, até o limite de 8;
// o resto cai no cinza "outros".
// número de lanes coloridas no gráfico de linha do tempo (KillTimelineChart)
// — separado do tamanho de FACTION_COLORS porque lá 30 linhas seria ilegível;
// guildas/alianças além disso caem em "outras" nesse gráfico específico.
const MAX_FACTION_ROWS = 8;
// ~30 cores num tom opaco/dessaturado (mesma família das 8 originais),
// espalhadas pelo círculo de cores pra reduzir colisão visual quando a
// batalha tem muitas guildas/alianças simultâneas (pedido explícito).
const FACTION_COLORS = [
  "#f87171", // vermelho
  "#fb923c", // laranja
  "#fbbf24", // âmbar
  "#eab308", // amarelo
  "#a3e635", // lima
  "#4ade80", // verde
  "#34d399", // verde-esmeralda
  "#2dd4bf", // verde-azulado
  "#22d3ee", // ciano
  "#38bdf8", // azul-céu
  "#60a5fa", // azul
  "#818cf8", // azul-índigo
  "#a78bfa", // violeta
  "#c084fc", // roxo
  "#e879f9", // roxo-rosado
  "#f472b6", // rosa
  "#fb7185", // rosa-avermelhado
  "#94a3b8", // cinza-azulado
  "#a8a29e", // cinza-quente
  "#71717a", // cinza-escuro
  "#e2e8f0", // quase branco
  "#1e3a8a", // azul-marinho escuro
  "#312e81", // azul-índigo escuro
  "#581c87", // roxo escuro
  "#7f1d1d", // vinho escuro
  "#155e75", // ciano escuro
  "#14532d", // verde escuro
  "#b45309", // âmbar escuro
  "#be185d", // rosa escuro
  "#4c1d95", // violeta escuro
];
const OTHERS_COLOR = "#52525b";

const ROLE_COLORS: Record<string, string> = {
  tank: "#1399FF", healer: "#43B80E", support: "#FFE04D", dps: "#FF2025", pierce: "#a855f7",
  battlemount: "#f59e0b", utility: "#6b7280", unknown: "#52525b",
};

function fameShort(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return n > 0 ? String(n) : "—";
}

// "Prata" na lista de guildas é prata PERDIDA (morrendo), não ganha — o sinal
// de menos deixa isso óbvio sem precisar de uma legenda.
function silverLostLabel(n: number): string {
  return n > 0 ? `-${fameShort(n)}` : "—";
}

function formatUtc(iso: string): string {
  const d = new Date(iso);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const min = String(d.getUTCMinutes()).padStart(2, "0");
  return `${dd}/${mm}/${d.getUTCFullYear()}, ${hh}:${min} UTC`;
}

function durationLabel(start: string, end: string | null, t: (key: TKey) => string): string {
  if (!end) return t("inProgressLabel");
  const min = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60_000);
  return min < 60 ? `${min}min` : `${(min / 60).toFixed(1)}h`;
}

function factionLabel(p: Participant): string {
  return factionLabelParts(p.guild_name, p.alliance_name, p.name);
}

function factionLabelParts(guild: string | null, alliance: string | null, fallbackName: string): string {
  const g = guild || fallbackName;
  return alliance ? `[${alliance}] ${g}` : g;
}

function computeGuildGroups(participants: Participant[]) {
  const byAlliance = new Map<string, Set<string>>();
  const noAlliance = new Set<string>();
  for (const p of participants) {
    if (!p.guild_name) continue;
    if (p.alliance_name) {
      if (!byAlliance.has(p.alliance_name)) byAlliance.set(p.alliance_name, new Set());
      byAlliance.get(p.alliance_name)!.add(p.guild_name);
    } else {
      noAlliance.add(p.guild_name);
    }
  }
  return {
    alliances: Array.from(byAlliance.entries()).sort((a, b) => a[0].localeCompare(b[0])),
    noAlliance: Array.from(noAlliance).sort(),
  };
}

// value prefixado: "a:<aliança>" filtra a aliança inteira, "g:<guilda>" só
// aquela guilda — pedido explícito pra poder filtrar pela tag da aliança.
function matchesGuildFilter(p: Participant, filter: string): boolean {
  if (!filter) return true;
  if (filter.startsWith("a:")) return p.alliance_name === filter.slice(2);
  if (filter.startsWith("g:")) return p.guild_name === filter.slice(2);
  return true;
}

// dropdown próprio em vez de <select>+<optgroup> — o label de um <optgroup>
// nativo NUNCA é clicável (restrição do HTML, não tem como contornar com
// CSS), então a aliança ficava com cara de selecionável mas nunca selecionava.
function GuildFilterSelect({ participants, value, onChange, align = "right" }: {
  participants: Participant[]; value: string; onChange: (v: string) => void;
  // De que lado do botão o menu abre. "right" (default) ancora a borda direita
  // do menu na borda direita do botão — certo quando o botão fica perto da
  // direita do container. Quando o botão fica perto da ESQUERDA (ex.: primeiro
  // controle de uma toolbar), usar align="left" — senão o menu abre pra fora
  // da tela/container em vez de pro lado que tem espaço.
  align?: "left" | "right";
}) {
  const t = useT();
  const groups = useMemo(() => computeGuildGroups(participants), [participants]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(ev: MouseEvent) {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  const currentLabel = !value ? t("allGuildsOption")
    : value.startsWith("a:") ? `[${value.slice(2)}]`
    : value.slice(2);

  function pick(v: string) { onChange(v); setOpen(false); }

  const rowCls = (active: boolean) =>
    `block w-full truncate px-3 py-1.5 text-left hover:bg-zinc-800 ${active ? "text-amber-300" : "text-zinc-300"}`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200 outline-none hover:border-zinc-500 focus:border-amber-500"
      >
        {currentLabel}
      </button>
      {open && (
        <div className={`absolute z-20 mt-1 max-h-72 w-56 overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 py-1 text-xs shadow-lg ${align === "left" ? "left-0" : "right-0"}`}>
          <button onClick={() => pick("")} className={rowCls(!value)}>{t("allGuildsOption")}</button>
          {groups.alliances.map(([alliance, guilds]) => (
            <div key={alliance} className="mt-1 border-t border-zinc-800 pt-1">
              <button onClick={() => pick(`a:${alliance}`)} className={`${rowCls(value === `a:${alliance}`)} font-bold`}>
                [{alliance}]
              </button>
              {Array.from(guilds).sort().map(g => (
                <button key={g} onClick={() => pick(`g:${g}`)} className={`${rowCls(value === `g:${g}`)} pl-5`}>
                  {g}
                </button>
              ))}
            </div>
          ))}
          {groups.noAlliance.length > 0 && (
            <div className="mt-1 border-t border-zinc-800 pt-1">
              {groups.noAlliance.map(g => (
                <button key={g} onClick={() => pick(`g:${g}`)} className={rowCls(value === `g:${g}`)}>
                  {g}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function searchHaystack(p: Participant, roleLabels: Record<string, string>): string {
  const itemNames = (p.equipment ?? []).flatMap(build =>
    EQUIP_SLOTS.map(slot => build[slot]).filter((id): id is string => !!id)
      .flatMap(id => {
        const item = ITEM_BY_ID.get(id);
        return [item?.name ?? id, item?.nameEn];
      })
  );
  const role = p.role ? [p.role, roleLabels[p.role], ROLE_LABELS_EN[p.role]] : [];
  return [p.name, p.guild_name, p.alliance_name, ...role, ...itemNames].filter(Boolean).join(" ").toLowerCase();
}

function BuildIcons({ equipment, slots = EQUIP_SLOTS, size = "h-7 w-7" }: { equipment: Build; slots?: string[]; size?: string }) {
  return (
    <div className="flex gap-1">
      {slots.map(slot => {
        const id = equipment[slot];
        if (!id) return null;
        const item = ITEM_BY_ID.get(id);
        const quality = Number(equipment[`${slot}_quality`]) || 0;
        return (
          <img
            key={slot}
            src={itemRenderUrl(item ?? id, quality)}
            alt={slot}
            title={item?.name ?? id}
            className={`${size} object-cover`}
            loading="lazy"
            onError={imgRetry()}
          />
        );
      })}
    </div>
  );
}

function EquipRow({ equipment }: { equipment: Build[] | null }) {
  const t = useT();
  if (!equipment || equipment.length === 0) return <span className="text-zinc-600 text-xs">{t("noBuildLabel")}</span>;
  const unique = dedupeBuilds(equipment);
  return (
    <div className="space-y-1">
      {unique.map((build, i) => <BuildIcons key={i} equipment={build} />)}
    </div>
  );
}

function Highlight({ icon, label, h, region }: { icon: string; label: string; h?: Highlight | null; region?: string }) {
  if (!h) return null;
  const prefix = REGION_PREFIX[h.region ?? region ?? ""] ?? null;
  const playerUrl = prefix ? `/${prefix}/${encodeURIComponent(h.name)}` : null;
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-lg">{icon}</span>
        <div className="min-w-0">
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide">{label}</div>
          {playerUrl
            ? <button onClick={() => navigate(playerUrl)} className="text-sm text-zinc-200 font-medium truncate hover:text-amber-400 transition-colors">{h.name}</button>
            : <div className="text-sm text-zinc-200 font-medium truncate">{h.name}</div>
          }
          <div className="text-[11px] text-zinc-600 truncate">{factionLabelParts(h.guild_name, h.alliance_name, h.name)}</div>
        </div>
      </div>
      <div className="shrink-0 text-xl font-bold text-amber-400 tabular-nums">{fameShort(h.value)}</div>
    </div>
  );
}

// am/as/eu, mesmos prefixos de router.ts — link de perfil é region-aware
// (nomes não são únicos entre Americas/Europe/Asia).
const REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };

function ParticipantRow({ p, color, region }: { p: Participant; color: string; region: string }) {
  const t = useT();
  const prefix = REGION_PREFIX[region];
  return (
    <div
      className="px-4 py-2.5 flex items-center gap-3 border-l-2"
      style={{ borderLeftColor: color }}
    >
      <div className="w-40 shrink-0 min-w-0">
        {prefix ? (
          <button
            onClick={() => navigate(`/${prefix}/${encodeURIComponent(p.name)}`)}
            className="max-w-full truncate text-sm text-zinc-200 hover:text-amber-400 hover:underline"
          >
            {p.name}
          </button>
        ) : (
          <div className="text-sm text-zinc-200 truncate">{p.name}</div>
        )}
        <div className="text-[11px] text-zinc-600 truncate">{factionLabel(p)}</div>
      </div>
      <div className="flex-1 min-w-0 flex justify-center">
        <EquipRow equipment={p.equipment} />
      </div>
      <div className="text-right text-xs shrink-0 tabular-nums w-[240px] grid grid-cols-5 gap-2 text-zinc-400">
        <span title="IP">{p.ip ? Math.round(p.ip) : "—"}</span>
        <span title={t("healingWord")}>{p.healing_done ? fameShort(p.healing_done) : "—"}</span>
        <span title="Kills" className="text-emerald-400">{p.kills}</span>
        <span title={t("deathsColLabel")}>{p.deaths}</span>
        <span title={t("fameWord")} className="text-amber-400/80">{fameShort(p.kill_fame)}</span>
      </div>
    </div>
  );
}

type SortKey = "ip" | "healing_done" | "kills" | "deaths" | "kill_fame";
function useSortCols(): { key: SortKey; label: string }[] {
  const t = useT();
  return [
    { key: "ip", label: "IP" },
    { key: "healing_done", label: t("healingWord") },
    { key: "kills", label: "Kills" },
    { key: "deaths", label: t("deathsColLabel") },
    { key: "kill_fame", label: t("fameWord") },
  ];
}

function ParticipantTable({ participants, region }: { participants: Participant[]; region: string }) {
  const t = useT();
  const roleLabels = useRoleLabels();
  const SORT_COLS = useSortCols();
  // mesma cor por guilda/aliança da lista de guildas/alianças, em vez do
  // azul/vermelho genérico de lado A/B (pedido explícito).
  const groups = useMemo(() => buildAllianceGroups(participants, EMPTY_SILVER), [participants]);
  const colorMap = useMemo(() => factionColorMap(groups), [groups]);
  const guildKeyMap = useMemo(() => guildNameToGroupKey(groups), [groups]);
  const [guildFilter, setGuildFilter] = useState("");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("kill_fame");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const pageSize = 10;
  const [page, setPage] = useState(1);

  const haystacks = useMemo(
    () => new Map(participants.map(p => [p.albion_player_id, searchHaystack(p, roleLabels)])),
    [participants, roleLabels],
  );

  const filtered = useMemo(
    () => participants.filter(p =>
      matchesGuildFilter(p, guildFilter) &&
      (!search || searchIncludes(search, haystacks.get(p.albion_player_id) ?? ""))
    ),
    [participants, guildFilter, search, haystacks],
  );

  const sorted = useMemo(() => {
    const dir = sortDir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => dir * ((a[sortKey] ?? 0) - (b[sortKey] ?? 0)));
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const pageClamped = Math.min(page, totalPages);
  const pageItems = sorted.slice((pageClamped - 1) * pageSize, pageClamped * pageSize);

  const pageNums = useMemo(() => {
    const start = Math.max(1, Math.min(pageClamped - 2, totalPages - 4));
    const from = Math.max(1, start);
    const to = Math.min(totalPages, from + 4);
    return Array.from({ length: to - from + 1 }, (_, i) => from + i);
  }, [pageClamped, totalPages]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir(d => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
    setPage(1);
  }

  return (
    // sem overflow-hidden aqui: clipa os cantos fixos do Panel (ficam a -1px)
    <Panel>
      <div className="border-b border-zinc-800 px-4 py-2.5 flex flex-wrap items-center gap-2">
        <GuildFilterSelect participants={participants} value={guildFilter} onChange={v => { setGuildFilter(v); setPage(1); }} align="left" />
        <input
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1); }}
          placeholder={t("searchParticipantsPlaceholder")}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-amber-500 placeholder:text-zinc-600 w-64"
        />
        <div className="flex-1" />
        <div className="w-[240px] grid grid-cols-5 gap-2 shrink-0">
          {SORT_COLS.map(c => (
            <button
              key={c.key}
              onClick={() => toggleSort(c.key)}
              className={`whitespace-nowrap text-right text-[11px] font-medium ${
                sortKey === c.key ? "text-amber-300" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {c.label}{sortKey === c.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
            </button>
          ))}
        </div>
      </div>

      <div className="divide-y divide-zinc-800/60">
        {pageItems.map(p => (
          <ParticipantRow key={p.albion_player_id} p={p} region={region} color={colorMap.get(guildKeyMap.get(p.guild_name ?? p.name) ?? "") ?? OTHERS_COLOR} />
        ))}
        {pageItems.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-zinc-500">{t("noPlayersWithFilters")}</div>
        )}
      </div>

      <div className="border-t border-zinc-800 px-4 py-2.5 flex items-center justify-end gap-3 text-xs">
        <div className="flex items-center gap-1">
          <button disabled={pageClamped === 1} onClick={() => setPage(1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">«</button>
          <button disabled={pageClamped === 1} onClick={() => setPage(p => p - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">‹</button>
          {pageNums.map(n => (
            <button
              key={n}
              onClick={() => setPage(n)}
              className={`px-2 py-1 rounded ${n === pageClamped ? "bg-amber-500/10 text-amber-300 border border-amber-500" : "text-zinc-400 hover:text-zinc-200"}`}
            >
              {n}
            </button>
          ))}
          <button disabled={pageClamped === totalPages} onClick={() => setPage(p => p + 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">›</button>
          <button disabled={pageClamped === totalPages} onClick={() => setPage(totalPages)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">»</button>
        </div>
        <span className="text-zinc-500">{sorted.length} {t("playersWord")}</span>
      </div>
    </Panel>
  );
}

interface GuildStat {
  guild_id: string | null; guild_name: string; alliance_id: string | null; alliance_name: string | null;
  kills: number; deaths: number; kill_fame: number; player_count: number;
  ip_sum: number; ip_count: number; silver: number;
}
interface AllianceGroup {
  key: string; label: string; expandable: boolean;
  entity_id: string | null; is_alliance: boolean;
  kills: number; deaths: number; kill_fame: number; player_count: number;
  ip_sum: number; ip_count: number; silver: number;
  guilds: GuildStat[];
}

// sem tentar inferir handholding pelo grafo de kills — só agrupa pelas tags
// reais do jogo. Aliança com 1 só guilda presente no KB, ou guilda sem
// aliança, aparecem como linha solo (pedido explícito).
function buildAllianceGroups(participants: Participant[], silverByVictim: Map<string, number>): AllianceGroup[] {
  const guildMap = new Map<string, GuildStat>();
  for (const p of participants) {
    const key = p.guild_name ?? `solo:${p.albion_player_id}`;
    let g = guildMap.get(key);
    if (!g) {
      g = { guild_id: p.guild_id, guild_name: p.guild_name ?? p.name, alliance_id: p.alliance_id, alliance_name: p.alliance_name, kills: 0, deaths: 0, kill_fame: 0, player_count: 0, ip_sum: 0, ip_count: 0, silver: 0 };
      guildMap.set(key, g);
    }
    g.kills += p.kills;
    g.deaths += p.deaths;
    g.kill_fame += p.kill_fame;
    g.player_count += 1;
    // build não carregou (ou API não reportou IP) -> não entra na média (pedido explícito).
    if (p.ip) { g.ip_sum += p.ip; g.ip_count += 1; }
    g.silver += silverByVictim.get(p.name) ?? 0;
  }

  const byAlliance = new Map<string, GuildStat[]>();
  for (const g of guildMap.values()) {
    const key = g.alliance_name ? `a:${g.alliance_name}` : `g:${g.guild_name}`;
    if (!byAlliance.has(key)) byAlliance.set(key, []);
    byAlliance.get(key)!.push(g);
  }

  const groups: AllianceGroup[] = [];
  for (const members of byAlliance.values()) {
    const isRealAlliance = !!members[0].alliance_name && members.length > 1;
    const sorted = members.sort((a, b) => b.kills - a.kills);
    groups.push({
      key: isRealAlliance ? `a:${members[0].alliance_name}` : `g:${sorted[0].guild_name}`,
      label: isRealAlliance ? members[0].alliance_name! : sorted[0].guild_name,
      expandable: sorted.length > 1,
      entity_id: isRealAlliance ? members[0].alliance_id : sorted[0].guild_id,
      is_alliance: isRealAlliance,
      kills: sorted.reduce((s, g) => s + g.kills, 0),
      deaths: sorted.reduce((s, g) => s + g.deaths, 0),
      kill_fame: sorted.reduce((s, g) => s + g.kill_fame, 0),
      player_count: sorted.reduce((s, g) => s + g.player_count, 0),
      ip_sum: sorted.reduce((s, g) => s + g.ip_sum, 0),
      ip_count: sorted.reduce((s, g) => s + g.ip_count, 0),
      silver: sorted.reduce((s, g) => s + g.silver, 0),
      guilds: sorted,
    });
  }
  return groups.sort((a, b) => b.kills - a.kills);
}

function avgIp(g: { ip_sum: number; ip_count: number }): number {
  return g.ip_count ? Math.round(g.ip_sum / g.ip_count) : 0;
}

// pedido explícito: presença na luta não é só kills — quem morreu bastante
// também "fez a batalha acontecer", então o critério de impacto é kills+mortes.
const LOW_IMPACT_SHARE = 0.03;
function engagement(g: { kills: number; deaths: number }): number {
  return g.kills + g.deaths;
}

// Separa (sem juntar) os grupos com presença abaixo de 3% do total da
// batalha — provavelmente ratos/ganks que cruzaram a luta sem fazer diferença
// real, escondidos por padrão atrás de "Mostrar mais". O corte é por GRUPO já
// agregado por aliança — uma guilda fraca dentro de uma aliança forte continua
// dentro do grupo da aliança, já que a presença dela soma pro total da
// aliança (pedido explícito).
function splitByImpact(groups: AllianceGroup[]): { main: AllianceGroup[]; low: AllianceGroup[] } {
  const total = groups.reduce((s, g) => s + engagement(g), 0);
  if (!total) return { main: groups, low: [] };
  const main: AllianceGroup[] = [];
  const low: AllianceGroup[] = [];
  for (const g of groups) (engagement(g) / total >= LOW_IMPACT_SHARE ? main : low).push(g);
  return { main, low };
}

// Mesma cor por grupo (guilda/aliança) usada na lista de guildas/alianças —
// reaproveitada na lista de jogadores pra que a cor da bracket de cada
// participante seja a mesma da linha dele lá (pedido explícito), em vez do
// azul/vermelho genérico de lado A/B.
function factionColorMap(groups: AllianceGroup[]): Map<string, string> {
  const { main: mainGroups, low: lowGroups } = splitByImpact(groups);
  const lowKeys = new Set(lowGroups.map(g => g.key));
  const byKills = [...mainGroups].sort((a, b) => b.kills - a.kills);
  const colorIndex = new Map(byKills.map((g, i) => [g.key, i]));
  const colors = new Map<string, string>();
  for (const g of groups) {
    const idx = colorIndex.get(g.key);
    colors.set(g.key, lowKeys.has(g.key) || idx === undefined || idx >= FACTION_COLORS.length
      ? OTHERS_COLOR
      : FACTION_COLORS[idx % FACTION_COLORS.length]);
  }
  return colors;
}

// Não reimplementa a regra de bucket de buildAllianceGroups (aliança com só 1
// guilda presente no KB colapsa pra linha solo) — bug anterior: um jogador de
// guilda solta dentro de uma aliança de 1-guilda-só ia pra "a:ALIANÇA", mas o
// grupo real ficava em "g:GUILDA", cor nunca batia. Em vez disso busca direto
// nos `guilds` de cada grupo, a mesma fonte que gerou os grupos.
function guildNameToGroupKey(groups: AllianceGroup[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const g of groups) for (const gs of g.guilds) map.set(gs.guild_name, g.key);
  return map;
}

const EMPTY_SILVER = new Map<string, number>();

function AllianceRow({ group, color }: { group: AllianceGroup; color: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div
        className={`px-4 py-2.5 flex items-center gap-3 ${group.expandable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
        onClick={() => group.expandable && setOpen(o => !o)}
      >
        <div className="w-4 shrink-0 text-center text-xs text-zinc-500">{group.expandable ? (open ? "▾" : "▸") : ""}</div>
        <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: color }} />
        <div className="flex-1 min-w-0 truncate">
          {group.entity_id ? (
            <button
              className="text-sm text-zinc-200 hover:text-amber-400 transition-colors truncate text-left"
              onClick={e => { e.stopPropagation(); navigate(`/${group.is_alliance ? "alliance" : "guild"}/${group.entity_id}`); }}
            >
              {group.expandable ? `[${group.label}]` : group.label}
            </button>
          ) : (
            <span className="text-sm text-zinc-200 truncate">{group.expandable ? `[${group.label}]` : group.label}</span>
          )}
        </div>
        <div className="text-right text-xs shrink-0 tabular-nums w-[360px] grid grid-cols-6 gap-2 text-zinc-400">
          <span>{group.player_count}</span>
          <span className="text-zinc-600">{silverLostLabel(group.silver)}</span>
          <span className="text-emerald-400">{group.kills}</span>
          <span>{group.deaths}</span>
          <span className="text-amber-400/80">{fameShort(group.kill_fame)}</span>
          <span>{avgIp(group) || "—"}</span>
        </div>
      </div>
      {open && group.guilds.map((g, i) => (
        <div key={i} className="pl-8 pr-4 py-1.5 flex items-center gap-3 border-t border-zinc-800/40 bg-zinc-950/30">
          <div className="flex-1 min-w-0 truncate">
            {g.guild_id ? (
              <button className="text-xs text-zinc-400 hover:text-amber-400 transition-colors truncate text-left"
                onClick={() => navigate(`/guild/${g.guild_id}`)}>
                {g.guild_name}
              </button>
            ) : (
              <span className="text-xs text-zinc-400 truncate">{g.guild_name}</span>
            )}
          </div>
          <div className="text-right text-xs shrink-0 tabular-nums w-[360px] grid grid-cols-6 gap-2 text-zinc-500">
            <span>{g.player_count}</span>
            <span className="text-zinc-600">{silverLostLabel(g.silver)}</span>
            <span className="text-emerald-400/80">{g.kills}</span>
            <span>{g.deaths}</span>
            <span className="text-amber-400/70">{fameShort(g.kill_fame)}</span>
            <span>{avgIp(g) || "—"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

type AllianceSortKey = "player_count" | "silver" | "kills" | "deaths" | "kill_fame" | "ip";
function useAllianceSortCols(): { key: AllianceSortKey; label: string }[] {
  const t = useT();
  return [
    { key: "player_count", label: t("playersColLabel") },
    { key: "silver", label: t("silverColLabel") },
    { key: "kills", label: "Kills" },
    { key: "deaths", label: t("deathsColLabel") },
    { key: "kill_fame", label: t("fameWord") },
    { key: "ip", label: "IP" },
  ];
}

function allianceSortValue(g: AllianceGroup, key: AllianceSortKey): number {
  return key === "ip" ? avgIp(g) : g[key];
}

function AllianceList({ participants, silverByVictim, title }: {
  participants: Participant[]; silverByVictim: Map<string, number>; title: string;
}) {
  const t = useT();
  const ALLIANCE_SORT_COLS = useAllianceSortCols();
  const groups = useMemo(() => buildAllianceGroups(participants, silverByVictim), [participants, silverByVictim]);
  const { main: mainGroups, low: lowGroups } = useMemo(() => splitByImpact(groups), [groups]);
  // cor sempre pelo ranking de kills entre os grupos de alto impacto (igual ao
  // horizonte de eventos), não pela ordem de exibição atual — senão mudar o
  // sort trocaria as cores das linhas. Grupos de baixo impacto usam OTHERS_COLOR.
  const colorMap = useMemo(() => factionColorMap(groups), [groups]);
  const [showLowImpact, setShowLowImpact] = useState(false);
  const [sortKey, setSortKey] = useState<AllianceSortKey>("kills");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const visibleGroups = useMemo(
    () => (showLowImpact ? [...mainGroups, ...lowGroups] : mainGroups),
    [mainGroups, lowGroups, showLowImpact],
  );

  const sorted = useMemo(() => {
    const dir = sortDir === "desc" ? -1 : 1;
    return [...visibleGroups].sort((a, b) => dir * (allianceSortValue(a, sortKey) - allianceSortValue(b, sortKey)));
  }, [visibleGroups, sortKey, sortDir]);

  function toggleSort(key: AllianceSortKey) {
    if (key === sortKey) setSortDir(d => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  return (
    <Panel>
      <div className="border-b border-zinc-800 px-4 py-2.5 flex items-center gap-3">
        <div className="flex-1 text-sm font-semibold text-zinc-200">{title}</div>
        <div className="w-[360px] shrink-0 grid grid-cols-6 gap-2">
          {ALLIANCE_SORT_COLS.map(c => (
            <button
              key={c.key}
              onClick={() => toggleSort(c.key)}
              className={`whitespace-nowrap text-right text-[11px] font-medium ${
                sortKey === c.key ? "text-amber-300" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {c.label}{sortKey === c.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
            </button>
          ))}
        </div>
      </div>
      <div className="divide-y divide-zinc-800/60">
        {sorted.map(g => (
          <AllianceRow key={g.key} group={g} color={colorMap.get(g.key) ?? OTHERS_COLOR} />
        ))}
        {sorted.length === 0 && <div className="px-4 py-8 text-center text-sm text-zinc-500">{t("noDataLabel")}</div>}
      </div>
      {lowGroups.length > 0 && (
        <button
          onClick={() => setShowLowImpact(v => !v)}
          className="w-full border-t border-zinc-800 px-4 py-2 text-center text-xs text-zinc-500 hover:text-zinc-300"
        >
          {showLowImpact ? t("hideLowImpactBtn") : `${t("showMorePrefix")} ${lowGroups.length} ${t("guildsAlliancesSuffix")}`}
        </button>
      )}
    </Panel>
  );
}

function polarPoint(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const [x1, y1] = polarPoint(cx, cy, r, endDeg);
  const [x2, y2] = polarPoint(cx, cy, r, startDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 0 ${x2} ${y2} Z`;
}

// Mistura a cor da fatia com o fundo do card (zinc-900) — fatia não
// selecionada fica numa versão opaca e apagada da PRÓPRIA cor, em vez de
// ficar "vazada" com opacity (pedido explícito).
function mutedColor(hex: string, amount = 0.7): string {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  const bg = [24, 24, 27]; // zinc-900
  const mix = (c: number, i: number) => Math.round(c + (bg[i] - c) * amount);
  return `rgb(${mix(r, 0)}, ${mix(g, 1)}, ${mix(b, 2)})`;
}

// Distância que a fatia selecionada "sai" do centro da pizza, na direção do
// próprio ângulo médio — efeito clássico de fatia puxada, animado via CSS.
const PIE_PULL_OUT = 2.5;

function PieChart({ counts, selected, onSelect, size = 80 }: {
  counts: Record<string, number>; selected: string | null; onSelect: (role: string) => void; size?: number;
}) {
  const entries = Object.entries(counts).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  if (!total) return null;
  let acc = 0;
  return (
    <svg viewBox="0 0 100 100" style={{ width: size, height: size, overflow: "visible" }} className="shrink-0">
      {entries.map(([k, v]) => {
        const start = (acc / total) * 360;
        acc += v;
        const end = (acc / total) * 360;
        const mid = (start + end) / 2;
        const [lx, ly] = polarPoint(50, 50, 32, mid);
        const dim = selected && selected !== k;
        const isSelected = selected === k;
        const color = ROLE_COLORS[k] ?? ROLE_COLORS.unknown;
        const [ox, oy] = isSelected ? polarPoint(0, 0, PIE_PULL_OUT, mid) : [0, 0];
        return (
          <g
            key={k}
            transform={`translate(${ox} ${oy})`}
            style={{ transition: "transform 200ms ease" }}
          >
            <path
              d={arcPath(50, 50, 48, start, end)}
              fill={dim ? mutedColor(color) : color}
              className="cursor-pointer"
              onClick={() => onSelect(k)}
            />
            <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle" fontSize={9} fontWeight={700}
              fill="#fff" opacity={dim ? 0.6 : 1} pointerEvents="none">
              {v}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function buildBase(id: string | null | undefined): string {
  if (!id) return "";
  const noEnchant = id.split("@")[0];
  const m = noEnchant.match(/^T\d+_(.+)$/);
  return m ? m[1] : noEnchant;
}

function buildSignature(build: Build, slots: string[] = EQUIP_SLOTS): string {
  return slots.map(slot => buildBase(build[slot])).join("|");
}

// Linhas reais de battlemount do Albion (mesmo critério do backend, ver
// battles.py BATTLEMOUNT_NAMES) — só essas contam como "build" diferente.
const BATTLEMOUNT_NAMES = [
  "BATTLE_RHINO", "ANCIENT_ENT", "BATTLE_EAGLE", "BEHEMOTH", "COLOSSUS_BEETLE",
  "GOLIATH_HORSEEATER", "JUGGERNAUT", "PHALANX_BEETLE", "ROVING_BASTION",
  "TOWER_CHARIOT", "SIEGE_BALLISTA", "FLAME_BASILISK", "VENOM_BASILISK",
  "COMMAND_MAMMOTH",
];
function isBattlemount(mountId: string | null | undefined): boolean {
  const base = buildBase(mountId).toUpperCase();
  return !!base && BATTLEMOUNT_NAMES.some(n => base.includes(n));
}

const DEDUP_SLOTS = ["weapon", "offhand", "helmet", "armor", "boots"];

// pedido explícito: voltar pra batalha com a mesma build mas montaria de
// transporte/capa/comida/pot/bolsa diferente ainda é "a mesma build" — só
// montaria DE COMBATE entra na comparação.
function dedupeBuilds(equipment: Build[]): Build[] {
  const seen = new Map<string, Build>();
  for (const build of equipment) {
    const sig = buildSignature(build, DEDUP_SLOTS) + "|" + (isBattlemount(build.mount) ? buildBase(build.mount) : "");
    if (!seen.has(sig)) seen.set(sig, build);
  }
  return Array.from(seen.values());
}

// montaria de combate é a própria "build" da role — sem ela mostrada não dá
// pra saber qual montaria o jogador estava usando.
function slotsForRole(role: string): string[] {
  return role === "battlemount" ? [...COMPOSITION_SLOTS, "mount"] : COMPOSITION_SLOTS;
}

// pedido explícito: filtro "Todas" junto dos de função — agrega todo mundo
// com função detectada (ignora "unknown", já que não tem build pra mostrar).
const ALL_ROLES = "all";

function unifiedBuilds(participants: Participant[], role: string): { build: Build; count: number }[] {
  const map = new Map<string, { build: Build; count: number }>();
  for (const p of participants) {
    const r = p.role ?? "unknown";
    if (r === "unknown") continue;
    if (role !== ALL_ROLES && r !== role) continue;
    // slots por papel do PARTICIPANTE (r), não da aba selecionada — assim a
    // aba "Todas" mostra a montaria nas builds de BM sem aplicar o slot de
    // montaria nas builds de quem só estava montado pra se deslocar.
    const slots = slotsForRole(r);
    for (const build of p.equipment ?? []) {
      const sig = buildSignature(build, slots);
      const cur = map.get(sig);
      if (cur) cur.count++;
      else map.set(sig, { build, count: 1 });
    }
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}

function RoleBuildList({ role, participants }: { role: string; participants: Participant[] }) {
  const t = useT();
  const builds = useMemo(() => unifiedBuilds(participants, role), [participants, role]);
  return (
    <div className="h-[264px] space-y-2 overflow-y-auto pr-5">
      {builds.map((b, i) => (
        <div key={i} className="flex items-center justify-end gap-2">
          <span className="text-xs text-zinc-500 tabular-nums">{b.count}×</span>
          <BuildIcons equipment={b.build} slots={slotsForRole(isBattlemount(b.build.mount) ? "battlemount" : role)} size="h-[60px] w-[60px]" />
        </div>
      ))}
      {builds.length === 0 && <div className="text-xs text-zinc-600">{t("noBuildsRegistered")}</div>}
    </div>
  );
}

function CompositionSection({ participants }: { participants: Participant[] }) {
  const t = useT();
  const roleLabels = useRoleLabels();
  const [guildFilter, setGuildFilter] = useState("");
  const filtered = useMemo(
    () => participants.filter(p => matchesGuildFilter(p, guildFilter)),
    [participants, guildFilter],
  );

  const roleCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const p of filtered) {
      const role = p.role ?? "unknown";
      if (role === "unknown") continue; // pedido explícito: função desconhecida não entra na pizza
      c[role] = (c[role] ?? 0) + 1;
    }
    return c;
  }, [filtered]);

  const roles = useMemo(
    () => Object.entries(roleCounts).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k]) => k),
    [roleCounts],
  );

  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const activeRole = selectedRole ?? roles[0] ?? null;
  const totalKnown = useMemo(() => Object.values(roleCounts).reduce((a, b) => a + b, 0), [roleCounts]);

  return (
    <Panel className="p-4">
      <PanelHeader title={t("compositionTitle")}>
        <div className="flex flex-nowrap gap-1 overflow-x-auto">
          <button
            onClick={() => setSelectedRole(ALL_ROLES)}
            className={`shrink-0 whitespace-nowrap dash-chip ${activeRole === ALL_ROLES ? "dash-chip-on" : ""}`}
          >
            {t("allRolesBtn")} ({totalKnown})
          </button>
          {roles.map(r => (
            <button
              key={r}
              onClick={() => setSelectedRole(r)}
              className={`shrink-0 whitespace-nowrap dash-chip ${activeRole === r ? "dash-chip-on" : ""}`}
            >
              {roleLabels[r] ?? r} ({roleCounts[r]})
            </button>
          ))}
        </div>
        <GuildFilterSelect participants={participants} value={guildFilter} onChange={setGuildFilter} />
      </PanelHeader>
      <div className="flex flex-col sm:flex-row gap-6 items-start">
        <div className="flex justify-start shrink-0">
          <PieChart counts={roleCounts} selected={activeRole === ALL_ROLES ? null : activeRole} onSelect={setSelectedRole} size={260} />
        </div>
        <div className="flex-1 min-w-0">
          {activeRole && <RoleBuildList role={activeRole} participants={filtered} />}
        </div>
      </div>
    </Panel>
  );
}

function weaponName(id: string | null, t: (key: TKey) => string): string {
  if (!id) return t("roleUnknownBuild");
  return ITEM_BY_ID.get(id)?.name ?? id;
}

const TL_PAD_L = 10, TL_PLOT_W = 300, TL_TOP_Y = 10, TL_BASE_Y = 95;

function topFactions(events: KillEvent[], max: number): string[] {
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.faction, (counts.get(e.faction) ?? 0) + 1);
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, max).map(([f]) => f);
}

function WeaponIcon({ id, quality }: { id: string | null; quality?: number }) {
  const t = useT();
  const weaponId = id ?? NO_WEAPON_ICON_ID;
  const item = ITEM_BY_ID.get(weaponId);
  const label = id ? weaponName(id, t) : t("noWeaponEquipped");
  return (
    <img
      src={itemRenderUrl(item ?? weaponId, id ? quality ?? 0 : 0)}
      alt={label}
      title={label}
      className="h-7 w-7 object-cover shrink-0"
      loading="lazy"
      onError={imgRetry()}
    />
  );
}

// kills da mesma guilda muito próximas no tempo (dentro de 4% da duração da
// luta) viram uma bola só, com a lista de kills indo pro hover — em vez de
// uma pilha de pontos colados e ilegível num momento de troca de foco.
const CLUSTER_WINDOW_FRACTION = 0.04;

function clusterEvents(events: KillEvent[], windowMs: number): KillEvent[][] {
  const clusters: KillEvent[][] = [];
  for (const e of events) {
    const t = new Date(e.t).getTime();
    const last = clusters[clusters.length - 1];
    if (last && t - new Date(last[0].t).getTime() <= windowMs) {
      last.push(e);
    } else {
      clusters.push([e]);
    }
  }
  return clusters;
}

function timeHmsUTC(ts: string): string {
  const d = new Date(ts);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function KillTimelineChart({ events, startTime, hiddenFactions }: { events: KillEvent[]; startTime: string; hiddenFactions?: Set<string> }) {
  const t = useT();
  const [hover, setHover] = useState<{ x: number; y: number; cluster: KillEvent[]; pinned: boolean } | null>(null);
  const visibleEvents = useMemo(
    () => hiddenFactions?.size ? events.filter(e => !hiddenFactions.has(e.faction)) : events,
    [events, hiddenFactions],
  );
  const factions = useMemo(() => topFactions(visibleEvents, MAX_FACTION_ROWS), [visibleEvents]);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!hover?.pinned) return;
    function onDocMouseDown(ev: MouseEvent) {
      if (tooltipRef.current && !tooltipRef.current.contains(ev.target as Node)) setHover(null);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [hover?.pinned]);

  const start = new Date(startTime).getTime();
  const end = new Date(visibleEvents[visibleEvents.length - 1]?.t ?? startTime).getTime();
  const span = Math.max(end - start, 1);
  const plotH = TL_BASE_Y - TL_TOP_Y;
  const clusterWindowMs = span * CLUSTER_WINDOW_FRACTION;

  const hasOthers = visibleEvents.some(e => !factions.includes(e.faction));
  const rows = hasOthers ? [...factions, "__others__"] : factions;
  const rowKey = (faction: string) => (factions.includes(faction) ? faction : "__others__");
  const rowColor = (key: string) =>
    key === "__others__" ? OTHERS_COLOR : FACTION_COLORS[factions.indexOf(key) % FACTION_COLORS.length];

  // uma linha por guilda/aliança: cada uma sobe só nas kills DELA, no exato
  // instante em que aconteceram (ou, se vierem muitas juntas, no centro do
  // grupo), ficando reta no resto do tempo — inclusive depois da última
  // kill dela, até o fim do gráfico, em vez de simplesmente parar no meio.
  const seriesEvents = new Map<string, KillEvent[]>();
  for (const e of visibleEvents) {
    const k = rowKey(e.faction);
    if (!seriesEvents.has(k)) seriesEvents.set(k, []);
    seriesEvents.get(k)!.push(e);
  }
  const maxCount = Math.max(1, ...rows.map(k => seriesEvents.get(k)?.length ?? 0));
  const endX = TL_PAD_L + TL_PLOT_W;

  const series = rows.map(key => {
    const evs = seriesEvents.get(key) ?? [];
    const clusters = clusterEvents(evs, clusterWindowMs);
    let cum = 0;
    const points = clusters.map(cluster => {
      cum += cluster.length;
      const times = cluster.map(e => new Date(e.t).getTime());
      const midT = (Math.min(...times) + Math.max(...times)) / 2;
      return {
        x: TL_PAD_L + Math.min(1, Math.max(0, (midT - start) / span)) * TL_PLOT_W,
        y: TL_BASE_Y - (cum / maxCount) * plotH,
        cluster,
      };
    });
    // conecta os pontos direto (sem degrau em ângulo reto) — sobe na inclinação
    // certa matematicamente, mas com cara de gráfico de linha de verdade.
    let path = `M ${TL_PAD_L} ${TL_BASE_Y} `;
    points.forEach(p => { path += `L ${p.x} ${p.y} `; });
    const lastY = points.length ? points[points.length - 1].y : TL_BASE_Y;
    path += `L ${endX} ${lastY} `;
    return { key, points, path };
  });

  const durationMin = Math.max(1, Math.round((end - start) / 60_000));

  return (
    <div>
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="text-sm font-semibold text-zinc-200">{t("eventsHorizonTitle")}</div>
        <div className="flex flex-wrap justify-end gap-3 text-xs">
          {rows.map(f => (
            <span key={f} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full shrink-0" style={{ background: rowColor(f) }} />
              <span className="text-zinc-400">{f === "__others__" ? t("othersLabel") : f}</span>
            </span>
          ))}
        </div>
      </div>
      <svg viewBox="0 0 320 110" className="w-full" style={{ overflow: "visible" }}>
        {Array.from({ length: 11 }, (_, i) => i).map(i => {
          const x = TL_PAD_L + (i / 10) * TL_PLOT_W;
          return (
            <g key={i}>
              <line x1={x} y1={TL_TOP_Y} x2={x} y2={TL_BASE_Y} stroke="#3f3f46" strokeWidth={0.5} />
              <text x={x} y={108} fontSize={6} fill="#71717a" textAnchor="middle">
                {Math.round((i / 10) * durationMin)}m
              </text>
            </g>
          );
        })}
        <line x1={TL_PAD_L} y1={TL_BASE_Y} x2={TL_PAD_L + TL_PLOT_W} y2={TL_BASE_Y} stroke="#52525b" strokeWidth={0.5} />
        {series.map(s => (
          <path key={s.key} d={s.path} fill="none" stroke={rowColor(s.key)} strokeWidth={1.2} opacity={0.9} />
        ))}
        {series.map(s => s.points.map((p, i) => (
          <g key={`${s.key}-${i}`}>
            <circle
              cx={p.x}
              cy={p.y}
              r={2.2}
              fill={rowColor(s.key)}
              stroke="#18181b"
              strokeWidth={0.6}
              style={{ cursor: "pointer" }}
              onMouseEnter={ev => setHover(h => (h?.pinned ? h : { x: ev.clientX, y: ev.clientY, cluster: p.cluster, pinned: false }))}
              onMouseMove={ev => setHover(h => (h && !h.pinned ? { ...h, x: ev.clientX, y: ev.clientY } : h))}
              onMouseLeave={() => setHover(h => (h?.pinned ? h : null))}
              onClick={ev => setHover({ x: ev.clientX, y: ev.clientY, cluster: p.cluster, pinned: true })}
            />
            {p.cluster.length > 6 && (
              <text x={p.x} y={p.y - 4} fontSize={5} fill="#71717a" textAnchor="middle" pointerEvents="none">
                {p.cluster.length}
              </text>
            )}
          </g>
        )))}
      </svg>
      {hover && (
        <div
          ref={tooltipRef}
          className="fixed z-50 flex flex-col gap-0.5 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-[11px] text-zinc-200 shadow-lg"
          style={{ left: hover.x + 12, top: hover.y, transform: "translateY(-50%)" }}
        >
          <div className={hover.cluster.length > 30 ? "max-h-[420px] space-y-0.5 overflow-y-auto" : "space-y-0.5"}>
            {hover.cluster.map((e, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="text-zinc-500 tabular-nums">{timeHmsUTC(e.t)}</span>
                <WeaponIcon id={e.weapon} quality={Number(e.killer_equipment?.weapon_quality) || 0} />
                <span className="font-medium text-zinc-100">{e.killer}</span>
                <span className="text-zinc-500">{t("killedVerb")}</span>
                <WeaponIcon id={e.victim_weapon} quality={Number(e.victim_equipment?.weapon_quality) || 0} />
                <span className="font-medium text-zinc-100">{e.victim ?? "?"}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Lista de mortes + equip guide (reusa EquipGrid/PriceHistoryChart das comps) ──
// Só a vítima é exibida no "menu suspenso" — é o que importa pra saber o que
// dropou. equip já inclui mount/bag (EQUIP_SLOTS tem os 10 slots), mesmo que
// o EquipGrid das comps não mostre os dois — entram só na conta de prata e no
// inventário visual abaixo do grid.
function victimItems(e: KillEvent): { id: string; qty: number }[] {
  const items: { id: string; qty: number }[] = [];
  if (e.victim_equipment) for (const slot of EQUIP_SLOTS) {
    const id = e.victim_equipment[slot]; if (id) items.push({ id, qty: 1 });
  }
  if (e.victim_inventory) for (const it of e.victim_inventory) items.push({ id: it.item_id, qty: it.count });
  return items;
}

function buildValue(items: { id: string; qty: number }[], prices: Map<string, number>): number {
  return items.reduce((sum, it) => sum + (prices.get(it.id) ?? 0) * it.qty, 0);
}

// preço de loot pra batalhas — vai pro nosso backend (não direto na API
// pública), que cacheia pra sempre no banco: a ideia é nunca re-consultar um
// item já visto, "o preço da época" já é o suficiente (pedido explícito) e
// evita bater na API externa de novo a cada vez que alguém abre a batalha.
async function fetchCurrentPrices(ids: string[]): Promise<Map<string, number>> {
  const unique = Array.from(new Set(ids));
  const out = new Map<string, number>();
  if (!unique.length) return out;
  const CHUNK = 100;
  for (let i = 0; i < unique.length; i += CHUNK) {
    const slice = unique.slice(i, i + CHUNK);
    const data: Record<string, number> = await fetch(`${API}/battles/prices?item_ids=${slice.join(",")}`)
      .then(r => r.json()).catch(() => ({}));
    for (const [id, price] of Object.entries(data)) out.set(id, price);
  }
  return out;
}

const EQUIP_GRID_SLOTS = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "food", "potion"] as const;

function killEquipToDraft(equip: Build | null): DraftEquip {
  const out: DraftEquip = {};
  if (!equip) return out;
  for (const slot of EQUIP_GRID_SLOTS) {
    const id = equip[slot];
    if (id) out[slot] = { id, name: ITEM_BY_ID.get(id)?.name ?? id };
  }
  return out;
}

// badge de quantidade sobreposto no topo do ícone (mesmo espírito do
// rc-equip-qty do EquipGrid) em vez de texto ao lado — é o que deixava o
// espaçamento entre os itens do inventário enorme.
function ExtraItemIcon({ id, qty, quality, onClick }: { id: string; qty: number; quality?: number; onClick?: () => void }) {
  const item = ITEM_BY_ID.get(id);
  return (
    <div
      className="relative h-9 w-9 shrink-0"
      style={{ cursor: onClick ? "pointer" : undefined }}
      onClick={onClick}
    >
      <img src={itemRenderUrl(item ?? id, quality ?? 0)} alt={item?.name ?? id} title={item?.name ?? id}
        className="h-full w-full object-contain" loading="lazy" onError={imgRetry()} />
      {qty > 1 && (
        <span
          className="absolute bottom-0.5 right-0.5 text-[7px] font-extrabold leading-none text-white"
          style={{ textShadow: "0 0 2px #000, 0 0 3px #000" }}
        >
          ×{qty}
        </span>
      )}
    </div>
  );
}

const INVENTORY_VISIBLE = 14;

// "menu suspenso" da kill: EquipGrid + PriceHistoryChart de verdade, os mesmos
// componentes da comp builder (mesmo clique-no-item-foca-o-gráfico) — só o
// inventário (itens carregados + mount/bag, fora do grid de 8 slots) é novo.
function KillEquipExpansion({ event, prices }: { event: KillEvent; prices: Map<string, number> }) {
  const t = useT();
  const [focusedId, setFocusedId] = useState<string | undefined>(undefined);
  const equip = event.victim_equipment;
  const draftEquip = useMemo(() => killEquipToDraft(equip), [equip]);
  const gearQuality = useMemo(() => {
    const out: Record<string, number> = {};
    for (const slot of EQUIP_GRID_SLOTS) {
      const q = Number(equip?.[`${slot}_quality`]);
      if (q) out[slot] = q;
    }
    return out;
  }, [equip]);
  const equipChartItems = useMemo(
    () => EQUIP_GRID_SLOTS
      .map(slot => ({ id: draftEquip[slot]?.id ?? "", name: draftEquip[slot]?.name ?? "", slot }))
      .filter(i => i.id),
    [draftEquip],
  );
  const extras = [
    ...(equip?.mount ? [{ id: equip.mount, qty: 1, quality: Number(equip.mount_quality) || 0 }] : []),
    ...(equip?.bag ? [{ id: equip.bag, qty: 1, quality: Number(equip.bag_quality) || 0 }] : []),
    ...(event.victim_inventory ?? []).map(it => ({ id: it.item_id, qty: it.count, quality: 0 })),
  ].sort((a, b) => (prices.get(b.id) ?? 0) * b.qty - (prices.get(a.id) ?? 0) * a.qty);
  const shown = extras.slice(0, INVENTORY_VISIBLE);
  const restCount = extras.length - INVENTORY_VISIBLE;
  const restValue = restCount > 0
    ? extras.slice(INVENTORY_VISIBLE).reduce((sum, it) => sum + (prices.get(it.id) ?? 0) * it.qty, 0)
    : 0;
  // mesmo gráfico do equipamento, só com mais linhas — clicar num item do
  // inventário foca a linha dele igual clicar num item do EquipGrid.
  const chartItems = useMemo(
    () => [...equipChartItems, ...shown.map(it => ({ id: it.id, name: ITEM_BY_ID.get(it.id)?.name ?? it.id, slot: "inventory" }))],
    [equipChartItems, shown],
  );

  return (
    <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-4 py-4">
      <div className="flex flex-wrap gap-6">
        <div className="flex flex-col gap-2" style={{ width: 212 }}>
          <EquipGrid equip={draftEquip} weaponIs2H={is2H(draftEquip.weapon?.id)} potionQty={10} foodQty={1} onFocus={setFocusedId} gearQuality={gearQuality} />
          {extras.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">{t("inventoryLabel")}</div>
              <div className="grid grid-cols-5 gap-1">
                {shown.map((it, i) => (
                  <ExtraItemIcon key={i} id={it.id} qty={it.qty} quality={it.quality} onClick={() => setFocusedId(it.id)} />
                ))}
                {restCount > 0 && (
                  <div className="flex h-9 w-9 flex-col items-center justify-center rounded border border-zinc-800 bg-zinc-950 leading-none">
                    <span className="text-[9px] font-semibold text-zinc-300">+{restCount}</span>
                    <span className="text-[7px] text-zinc-600">{fameShort(restValue)}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="min-w-[260px] flex-1" style={{ position: "relative", aspectRatio: "2 / 1" }}>
          <PriceHistoryChart items={chartItems} potionQty={10} foodQty={1} focusedId={focusedId} onFocus={setFocusedId} />
        </div>
      </div>
    </div>
  );
}

type KFSortKey = "t" | "fame" | "value";

function formatHms(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function KillFeed({ events, prices }: { events: KillEvent[]; prices: Map<string, number> }) {
  const t = useT();
  const [sortKey, setSortKey] = useState<KFSortKey>("t");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [expanded, setExpanded] = useState<number | null>(null);
  const expandedRef = useRef<HTMLDivElement | null>(null);

  // o painel abre pra baixo dentro de uma lista com scroll próprio — sem
  // isso, ele quase sempre nasce fora da área visível (do scroll da lista
  // ou da página) e parece que o clique não fez nada.
  useEffect(() => {
    if (expanded !== null) expandedRef.current?.scrollIntoView({ block: "nearest" });
  }, [expanded]);

  const rows = useMemo(
    () => events.map((e, i) => ({ e, i, value: buildValue(victimItems(e), prices) })),
    [events, prices],
  );

  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (sortKey === "fame") return dir * (a.e.fame - b.e.fame);
      if (sortKey === "value") return dir * (a.value - b.value);
      return dir * (new Date(a.e.t).getTime() - new Date(b.e.t).getTime());
    });
  }, [rows, sortKey, sortDir]);

  function toggleSort(key: KFSortKey) {
    if (key === sortKey) setSortDir(d => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "t" ? "asc" : "desc"); }
  }

  function toggleExpand(i: number) {
    setExpanded(prev => (prev === i ? null : i));
  }

  const sortBtn = (key: KFSortKey, label: string) => (
    <button
      onClick={() => toggleSort(key)}
      className={`whitespace-nowrap text-right text-[11px] font-medium ${
        sortKey === key ? "text-amber-300" : "text-zinc-500 hover:text-zinc-300"
      }`}
    >
      {label}{sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
    </button>
  );

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800">
      <div className="border-b border-zinc-800 px-4 py-2.5 flex items-center gap-3">
        <div className="w-4 shrink-0" />
        <div className="w-20 shrink-0">{sortBtn("t", t("timeUtcColLabel"))}</div>
        <div className="flex-1 grid grid-cols-2 gap-2 px-2 text-[11px] font-medium text-zinc-500">
          <span>{t("killerColLabel")}</span><span>{t("victimColLabel")}</span>
        </div>
        <div className="w-[176px] shrink-0 grid grid-cols-2 gap-2">
          {sortBtn("value", t("silverColLabel"))}
          {sortBtn("fame", t("fameWord"))}
        </div>
      </div>
      <div className="max-h-[480px] divide-y divide-zinc-800/60 overflow-y-auto">
        {sorted.map(({ e, i, value }) => (
          <div key={i}>
            <button onClick={() => toggleExpand(i)} className="flex w-full items-center gap-3 px-4 py-2 text-left text-xs hover:bg-zinc-800/30">
              <span className="w-4 shrink-0 text-center text-zinc-500">{expanded === i ? "▾" : "▸"}</span>
              <span className="w-20 shrink-0 tabular-nums text-zinc-500">{formatHms(e.t)}</span>
              <div className="flex-1 grid grid-cols-2 gap-2 px-2 min-w-0">
                <span className="flex items-center gap-1.5 min-w-0">
                  <WeaponIcon id={e.weapon} quality={Number(e.killer_equipment?.weapon_quality) || 0} />
                  <span className="min-w-0">
                    <span className="block truncate text-zinc-200">{e.killer}</span>
                    <span className="block truncate text-[10px] text-zinc-600">{factionLabelParts(e.killer_guild, e.killer_alliance, e.killer)}</span>
                  </span>
                </span>
                <span className="flex items-center gap-1.5 min-w-0">
                  <WeaponIcon id={e.victim_weapon} quality={Number(e.victim_equipment?.weapon_quality) || 0} />
                  <span className="min-w-0">
                    <span className="block truncate text-zinc-400">{e.victim ?? "?"}</span>
                    {e.victim && <span className="block truncate text-[10px] text-zinc-600">{factionLabelParts(e.victim_guild, e.victim_alliance, e.victim)}</span>}
                  </span>
                </span>
              </div>
              <div className="w-[176px] shrink-0 grid grid-cols-2 gap-2 text-right tabular-nums">
                <span className="text-zinc-600">{fameShort(value)}</span>
                <span className="text-amber-400/80">{fameShort(e.fame)}</span>
              </div>
            </button>
            {expanded === i && (
              <div ref={expandedRef}>
                <KillEquipExpansion event={e} prices={prices} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

interface BattlePageProps {
  code?: string;
  albionIds?: string[];
  onBack: () => void;
}

export default function BattlePage({ code, albionIds, onBack }: BattlePageProps) {
  const t = useT();
  const regionLabels = useRegionLabels();
  const [battle, setBattle] = useState<BattleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [resolving, setResolving] = useState(!!albionIds);
  const [prices, setPrices] = useState<Map<string, number>>(new Map());

  useEffect(() => {
    setBattle(null);
    setError(null);

    let cancelled = false;
    const ALWAYS_RETRYABLE = new Set([202, 502, 503, 504]);
    const MAX_404_RETRIES = 6;
    const POLL_DELAY = 3_000;
    const MAX_POLL_DELAY = 30_000;

    // Poll em /battles/by-code/{public_id} até a batalha estar deep-processada
    // (202 = ainda processando, 200 = pronta). Atualiza a URL pro código curto.
    const pollByCode = (publicId: string, delay: number) => {
      fetch(`${API}/battles/by-code/${publicId}`)
        .then(res => {
          if (cancelled) return;
          if (res.status === 404) throw new Error(t("battleNotFoundOldError"));
          if (ALWAYS_RETRYABLE.has(res.status)) {
            setTimeout(() => { if (!cancelled) pollByCode(publicId, Math.min(delay * 1.3, MAX_POLL_DELAY)); }, delay);
            return;
          }
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data?: BattleDetail) => {
          if (cancelled || !data) return;
          navigateReplace(`/${data.public_id}`);
          setBattle(data);
          setResolving(false);
        })
        .catch(e => { if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setResolving(false); } });
    };

    if (code) {
      fetch(`${API}/battles/by-code/${code}`)
        .then(res => {
          if (cancelled) return;
          if (res.status === 404) throw new Error(t("linkNotFoundError"));
          if (ALWAYS_RETRYABLE.has(res.status)) {
            setResolving(true);
            setTimeout(() => { if (!cancelled) pollByCode(code, POLL_DELAY); }, POLL_DELAY);
            return;
          }
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data?: BattleDetail) => { if (!cancelled && data) { setBattle(data); } })
        .catch(e => { if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); } });
      return () => { cancelled = true; };
    }

    if (albionIds) {
      setResolving(true);
      const attempt = (delay: number, retries404Left: number) => {
        fetch(`${API}/battles/resolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ albion_ids: albionIds }),
        })
          .then(res => {
            if (cancelled) return;
            // 404 = batalha não existe na API do Albion (ainda não indexada ou
            // antiga demais). Retry limitado pra não ficar pra sempre.
            if (res.status === 404 && retries404Left > 0) {
              setTimeout(() => { if (!cancelled) attempt(Math.min(delay * 2, 30_000), retries404Left - 1); }, delay);
              return;
            }
            if (res.status === 404) throw new Error(t("battleNotFoundOldError"));
            if (ALWAYS_RETRYABLE.has(res.status)) {
              setTimeout(() => { if (!cancelled) attempt(Math.min(delay * 2, 30_000), retries404Left); }, delay);
              return;
            }
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
          })
          .then((data?: { public_id?: string; status?: string } & Partial<BattleDetail>) => {
            if (cancelled || !data) return;
            // 202 = batalha criada mas ainda light (deep-process enfileirado).
            // Troca a URL pro código curto e faz poll até ficar pronta.
            if (data.status === "processing" && data.public_id) {
              navigateReplace(`/${data.public_id}`);
              setTimeout(() => { if (!cancelled) pollByCode(data.public_id!, POLL_DELAY); }, POLL_DELAY);
              return;
            }
            // 200 = batalha já estava deep, detalhe pronto.
            navigateReplace(`/${data.public_id}`);
            setBattle(data as BattleDetail);
            setResolving(false);
          })
          .catch(e => { if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setResolving(false); } });
      };
      attempt(3_000, MAX_404_RETRIES);
      return () => { cancelled = true; };
    }
  }, [code, albionIds]);

  function copyLink() {
    navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  const allParticipants = useMemo(
    () => battle ? [...battle.sides.flatMap(s => s.participants), ...battle.rats.participants] : [],
    [battle],
  );

  useEffect(() => {
    if (!battle) { document.title = "Ziggs"; return; }
    const tags: string[] = [];
    for (const s of battle.sides) {
      const seen = new Set<string>();
      for (const f of s.factions) {
        const tag = makeTag(f.alliance_name, f.guild_name);
        if (tag && !seen.has(tag)) { seen.add(tag); tags.push(tag); }
      }
    }
    document.title = tags.length ? tags.slice(0, 4).join(" vs ") : "Ziggs";
    return () => { document.title = "Ziggs"; };
  }, [battle]);

  // mesmas guildas/alianças escondidas por padrão na lista (baixo impacto,
  // ver splitByImpact) não devem ganhar linha própria no horizonte de
  // eventos (pedido explícito) — os kills delas continuam fora do gráfico.
  const hiddenFactionLabels = useMemo(() => {
    const { low } = splitByImpact(buildAllianceGroups(allParticipants, EMPTY_SILVER));
    return new Set(low.map(g => g.label));
  }, [allParticipants]);

  // preço só do que a vítima largou — calculado uma vez aqui em cima e
  // reaproveitado na lista de mortes, no total do header e no "Prata" por
  // guilda, em vez de cada um buscar/recalcular por conta própria.
  useEffect(() => {
    if (!battle) return;
    const ids = battle.kill_timeline.flatMap(e => victimItems(e).map(i => i.id));
    if (!ids.length) return;
    fetchCurrentPrices(ids).then(setPrices);
  }, [battle]);

  const killValues = useMemo(
    () => battle ? battle.kill_timeline.map(e => ({ e, value: buildValue(victimItems(e), prices) })) : [],
    [battle, prices],
  );
  const totalSilver = useMemo(() => killValues.reduce((s, k) => s + k.value, 0), [killValues]);
  // "Prata" na lista de guildas é o que a guilda PERDEU morrendo, não o que
  // ganhou matando — por isso por vítima, não por matador.
  const silverByVictim = useMemo(() => {
    const m = new Map<string, number>();
    for (const { e, value } of killValues) {
      if (!e.victim) continue;
      m.set(e.victim, (m.get(e.victim) ?? 0) + value);
    }
    return m;
  }, [killValues]);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <div className="mb-5 flex items-center gap-3">
        <button onClick={onBack} className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:border-zinc-500">
          {t("backToBattlesBtn")}
        </button>
        <div className="flex-1" />
        <button onClick={copyLink} className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:border-zinc-500">
          {copied ? t("linkCopiedLabel") : t("copyLinkBtn")}
        </button>
      </div>

      {error && (
        <p className="mb-4 rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400">{error}</p>
      )}

      {!battle && !error && (
        <div className="py-16 text-center text-zinc-500">
          {resolving ? t("resolvingBattleLabel") : t("loadingBattleLabel")}
        </div>
      )}

      {battle && !!battle.unresolved_ids?.length && (
        <p className="mb-4 rounded-lg border border-amber-800/50 bg-amber-900/15 px-4 py-3 text-xs text-amber-400">
          {battle.unresolved_ids.length} {t("ignoredBattlesNotice")} {battle.unresolved_ids.join(", ")}
        </p>
      )}

      {battle && (
        <>
          <AllianceList
            participants={allParticipants}
            silverByVictim={silverByVictim}
            title={
              `${regionLabels[battle.region] ?? battle.region}` +
              (battle.cluster ? ` · ${battle.cluster}` : "") +
              ` · ${durationLabel(battle.start_time, battle.end_time, t)} · ${formatUtc(battle.start_time)}` +
              (battle.battle_count > 1 ? ` · ${battle.battle_count} ${t("combinedBattlesSuffix")}` : "")
            }
          />

          <div className="mb-5 mt-3 text-center text-xs text-zinc-600">
            {fameShort(battle.total_fame)} {t("totalFameSuffix")} · {battle.kill_count} kills · {fameShort(totalSilver)} {t("silverDroppedSuffix")}
          </div>

          {!!battle.found_by?.length && (
            <p className="mb-5 -mt-3 text-center text-xs text-zinc-500">
              🤝 {t("foundByThanksPrefix")}{" "}
              <span className="font-medium text-amber-400/80">{battle.found_by.join(", ")}</span>{" "}
              {t("foundByThanksSuffix")}{" "}
              <a
                href="/download"
                className="text-zinc-400 underline decoration-zinc-600 underline-offset-2 hover:text-zinc-200"
                onClick={e => { e.preventDefault(); navigate("/download"); }}
              >Ziggs Companion</a>.
            </p>
          )}

          {(() => {
            const items = [
              { icon: "⚔️", label: t("highlightMostKills"), h: battle.highlights.top_kills },
              { icon: "👑", label: t("highlightMostFame"), h: battle.highlights.top_fame },
              { icon: "💚", label: t("highlightMostHealing"), h: battle.highlights.top_healing },
              { icon: "💀", label: t("highlightTopDeathFame"), h: battle.highlights.top_death_fame },
            ].filter(it => it.h);
            if (!items.length) return null;
            // sem highlight (ex: ninguém curou) some o card e os outros esticam
            // pra preencher o espaço, em vez de deixar um buraco no grid fixo.
            return (
              <div className="mb-5 grid gap-2" style={{ gridTemplateColumns: `repeat(${items.length}, 1fr)` }}>
                {items.map(it => <Highlight key={it.label} icon={it.icon} label={it.label} h={it.h} region={battle.region} />)}
              </div>
            );
          })()}

          <ParticipantTable participants={allParticipants} region={battle.region} />

          <div className="mt-4">
            <CompositionSection participants={allParticipants} />
          </div>

          <Panel className="mt-4 p-4">
            {battle.kill_timeline.length === 0 ? (
              <div className="text-xs text-zinc-500">{t("noKillEventsRecorded")}</div>
            ) : (
              <>
                <KillTimelineChart events={battle.kill_timeline} startTime={battle.start_time} hiddenFactions={hiddenFactionLabels} />
                <div className="my-4 border-t border-zinc-800" />
                <KillFeed events={battle.kill_timeline} prices={prices} />
              </>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
