import { useEffect, useMemo, useRef, useState } from "react";
import { navigate } from "../router";
import { dateUTC } from "../lib/format";
import { RoleIcon } from "./RoleIcons";
import { useLang, useT, REGION_LABELS } from "../i18n";
import { Panel } from "./Panel";
import LoadProgress from "./LoadProgress";

const API = import.meta.env.DEV ? "http://localhost:8000" : "";

const REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };
const PAGE_SIZE = 20;

/* ── Types ──────────────────────────────────────────────────────── */

interface WindowStat { "7d": number; "30d": number; all: number }
type Win = "7d" | "30d" | "all";

interface BattleFaction {
  guild_id: string; guild_name: string; alliance_name: string | null;
  kills: number; player_count: number;
}

export interface GuildMember {
  albion_id: string; name: string; region: string;
  battles: number; kills: number; deaths: number;
  kill_fame: number; last_seen: string;
  roles: Record<string, number>;
  is_deleted?: boolean;
}

export interface AllianceGuild {
  albion_id: string; name: string;
  battles: number; kills: number; deaths: number;
  kill_fame: number; last_seen: string;
}

interface ProfileBattle {
  public_id: string; region: string; start_time: string;
  cluster: string | null; is_zvz: boolean; players_total: number;
  kills: number; deaths: number; kill_fame: number;
  result: "win" | "loss" | "draw" | null;
  factions: BattleFaction[];
}

interface AllianceEvent {
  event: "joined" | "left";
  date: string;
  alliance_id: string;
  alliance_name: string | null;
}

interface RosterGuild { guild_id: string; name: string; battles: number; is_deleted?: boolean }
interface RosterEvent {
  date: string;
  added: RosterGuild[];
  removed: RosterGuild[];
  roster_before: RosterGuild[];
  roster_after: RosterGuild[];
}

interface GuildProfile {
  albion_id: string; name: string;
  alliance_id: string | null; alliance_name: string | null;
  last_synced_at: string | null;
  // Estado de refresh compartilhado (do profile_warmer). Enquanto != null,
  // TODOS os visitantes vêem "atualizando" e o botão fica disabled.
  refresh_requested_at?: string | null;
  kills_total: number; deaths_total: number;
  kill_fame: WindowStat; silver_dropped: WindowStat; battles: WindowStat;
  members: GuildMember[];
  battles_count: number;
  alliance_history: AllianceEvent[];
  // Primeira página da aba Batalhas (page=0, filtros padrão) — já vem pronta
  // no cold-load, mesmo motivo do `members`: evita reabrir a aba e esperar
  // de novo por algo que já foi calculado minutos atrás.
  battles_page0: BattlesPage;
}

interface AllianceProfile extends Omit<GuildProfile, "members" | "alliance_history"> {
  guilds: AllianceGuild[];
  roster_log: RosterEvent[];
}

interface ColdLoadStub { _cold_load: true; albion_id: string }

interface BattlesPage { battles: ProfileBattle[]; total: number; page: number; pages: number }

type Profile = GuildProfile | AllianceProfile;
type ProfileOrCold = Profile | ColdLoadStub;
type Tab = "members" | "battles" | "alliances" | "roster";

/* ── Helpers ────────────────────────────────────────────────────── */

function fameShort(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "0";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function shortDateUTC(ts: string): string {
  const d = new Date(ts);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const yy = String(d.getUTCFullYear()).slice(2);
  return `${dd}/${mm}/${yy}`;
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d`;
  return `${Math.floor(d / 30)}mo`;
}

// "agora" cobre os primeiros 10min — mesmo tempo que o REFRESH_COOLDOWN de
// guilda/aliança no backend (profiles.py).
const JUST_NOW_MS = 10 * 60_000;
// O sufixo "atrás" só faz sentido junto de um tempo real (5m atrás, 3h
// atrás) — "agora atrás" não é frase em nenhum dos 3 idiomas (nem "now ago"
// em EN). Decide aqui, uma vez só, em vez de deixar o caller colar o sufixo
// incondicionalmente por fora.
function ageLabel(ts: string, justNow: string, suffix: string): string {
  return Date.now() - new Date(ts).getTime() < JUST_NOW_MS ? justNow : `${timeAgo(ts)} ${suffix}`;
}

/* ── Reused heatmap (mesmo modelo de PlayerProfilePage) ─────────── */

const HEAT_MAX: [number, number, number] = [0x66, 0x71, 0x60];
const HEAT_MIN: [number, number, number] = [0x52, 0x52, 0x5c];

function heatColor(kills: number, maxK: number, minK: number): string {
  const t = maxK === minK ? 0 : (maxK - kills) / (maxK - minK);
  const [r, g, b] = HEAT_MAX.map((c, i) => Math.round(c + (HEAT_MIN[i] - c) * t));
  return `rgb(${r}, ${g}, ${b})`;
}

function FactionHeatmap({ factions }: { factions: BattleFaction[] }) {
  if (!factions.length) return null;
  const maxK = Math.max(...factions.map(f => f.kills));
  const minK = Math.min(...factions.map(f => f.kills));
  return (
    <span className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-sm font-semibold">
      {factions.map(f => (
        <span key={f.guild_id} className="flex flex-col items-center leading-tight">
          <span style={{ color: heatColor(f.kills, maxK, minK) }}>
            {f.alliance_name ? `[${f.alliance_name}]` : f.guild_name}
          </span>
          <span className="text-[10px] font-normal text-zinc-600">{f.player_count}</span>
        </span>
      ))}
    </span>
  );
}

/* ── Sub-components ─────────────────────────────────────────────── */

function Pagination({ page, total, onChange }: { page: number; total: number; onChange: (p: number) => void }) {
  if (total <= 1) return null;
  const lo = Math.max(0, page - 2);
  const hi = Math.min(total - 1, page + 2);
  const pages = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  return (
    <div className="mt-4 flex items-center justify-center gap-1">
      <button className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-30" disabled={page === 0} onClick={() => onChange(0)}>«</button>
      <button className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-30" disabled={page === 0} onClick={() => onChange(page - 1)}>‹</button>
      {pages.map(p => (
        <button key={p} onClick={() => onChange(p)}
          className={`w-7 h-7 rounded text-xs font-medium ${p === page ? "bg-amber-500/20 border border-amber-500/50 text-amber-300" : "text-zinc-500 hover:text-zinc-300"}`}>
          {p + 1}
        </button>
      ))}
      <button className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-30" disabled={page === total - 1} onClick={() => onChange(page + 1)}>›</button>
      <button className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-30" disabled={page === total - 1} onClick={() => onChange(total - 1)}>»</button>
    </div>
  );
}

function GuildBattleRow({ b }: { b: ProfileBattle }) {
  const t = useT();
  const { lang } = useLang();
  return (
    <a
      href={`/${b.public_id}`}
      onClick={e => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(`/${b.public_id}`);
      }}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5 transition-colors hover:border-zinc-600 hover:bg-zinc-800/40"
    >
      <span className="w-20 shrink-0 flex flex-col leading-tight">
        <span className="text-[10px] uppercase tracking-wide text-zinc-600">{REGION_LABELS[lang][b.region] ?? b.region}</span>
        <span className="text-xs text-zinc-500 tabular-nums">{dateUTC(b.start_time)}</span>
      </span>

      <span className="flex-1 min-w-0 text-center">
        {b.factions.length > 0
          ? <FactionHeatmap factions={b.factions} />
          : <span className="truncate text-sm text-zinc-300">{b.cluster ?? t("unknownZone")}</span>}
      </span>

      <span className="flex items-center gap-2 shrink-0">
        <span className="w-14 text-right text-xs font-semibold text-amber-400/80 tabular-nums">{fameShort(b.kill_fame)}</span>
        <span className="w-10 text-right text-xs text-zinc-600 tabular-nums">{timeAgo(b.start_time)}</span>
      </span>
    </a>
  );
}

/* ── Alliance history (guild profile) ───────────────────────────── */

type AllianceStint = { alliance_id: string; alliance_name: string | null; joined: string; left: string | null };

function eventsToStints(events: AllianceEvent[]): AllianceStint[] {
  const chrono = [...events].reverse();
  const stints: AllianceStint[] = [];
  for (const e of chrono) {
    if (e.event === "joined") {
      stints.push({ alliance_id: e.alliance_id, alliance_name: e.alliance_name, joined: e.date, left: null });
    } else {
      const open = [...stints].reverse().find(s => s.alliance_id === e.alliance_id && s.left === null);
      if (open) open.left = e.date;
    }
  }
  return stints.reverse();
}

function AllianceHistoryTab({ events }: { events: AllianceEvent[] }) {
  const t = useT();
  if (!events.length)
    return <p className="py-8 text-center text-zinc-600">{t("noAllianceHistoryYet")}</p>;
  const stints = eventsToStints(events);
  return (
    <div className="flex flex-col gap-2">
      {stints.map((s, i) => (
        <div key={i} className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
          <span className="w-20 shrink-0 text-center text-[10px] text-zinc-500 tabular-nums">{shortDateUTC(s.joined)}</span>
          <div className="flex-1 min-w-0 flex justify-center">
            <button onClick={() => navigate(`/alliance/${s.alliance_id}`)}
              className="text-sm font-semibold text-amber-400 hover:opacity-75 transition-opacity">
              [{s.alliance_name ?? s.alliance_id}]
            </button>
          </div>
          <span className="w-20 shrink-0 text-center text-xs tabular-nums">
            {s.left
              ? <span className="text-zinc-500">{dateUTC(s.left)}</span>
              : <span className="font-medium text-emerald-400">{t("currentLabel")}</span>
            }
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Roster log (alliance profile) ──────────────────────────────── */

function RosterGuildPill({ g, highlight }: { g: RosterGuild; highlight: "added" | "removed" | null }) {
  const t = useT();
  if (g.is_deleted && highlight !== "removed") {
    return (
      <span className="rounded border border-zinc-700/40 bg-zinc-800/30 px-2 py-0.5 text-xs text-zinc-600 line-through" title={t("guildDeletedTitle")}>
        {g.name}
      </span>
    );
  }
  const cls =
    highlight === "added"   ? "border-green-900/60 bg-green-950/40 text-green-400" :
    highlight === "removed" ? "border-red-900/60 bg-red-950/40 text-red-400 line-through" :
                              "border-zinc-700/60 bg-zinc-800/60 text-zinc-400 hover:text-zinc-200";
  return (
    <button onClick={() => navigate(`/guild/${g.guild_id}`)}
      className={`rounded border px-2 py-0.5 text-xs transition-colors hover:opacity-80 ${cls}`}>
      {g.name}
    </button>
  );
}

function RosterLogTab({ events }: { events: RosterEvent[] }) {
  const t = useT();
  if (!events.length)
    return <p className="py-8 text-center text-zinc-600">{t("noRosterLogYet")}</p>;

  return (
    <div className="flex flex-col gap-3">
      {events.map((e, i) => {
        const addedIds = new Set(e.added.map(g => g.guild_id));
        const sortedAfter = [...e.roster_after].sort((a, b) => b.battles - a.battles);
        const total = e.roster_after.length;

        // unchanged = roster permanente (sem os recém-adicionados)
        const unchanged = sortedAfter.filter(g => !addedIds.has(g.guild_id));
        // added vai sempre ao final, depois dos permanentes
        const addedInAfter = sortedAfter.filter(g => addedIds.has(g.guild_id));
        const allDisplay: (RosterGuild & { hl: "added" | "removed" | null })[] = [
          ...unchanged.map(g => ({ ...g, hl: null })),
          ...addedInAfter.map(g => ({ ...g, hl: "added" as const })),
          ...e.removed.map(g => ({ ...g, hl: "removed" as const })),
        ];

        return (
          <div key={i} className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3">
            <div className="shrink-0 text-center text-[10px] text-zinc-500 tabular-nums w-16">{shortDateUTC(e.date)}</div>
            <div className="flex-1 flex flex-wrap justify-center gap-1.5">
              {allDisplay.map(g => (
                <RosterGuildPill key={`${g.guild_id}-${g.hl}`} g={g} highlight={g.hl} />
              ))}
            </div>
            <div className="shrink-0 flex flex-col items-center leading-tight w-10">
              <span className="text-sm font-bold tabular-nums text-zinc-200">{total}</span>
              <span className="text-[10px] uppercase tracking-wide text-zinc-600">{t("guildWordPlural")}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const ROLE_ORDER = ["tank", "healer", "support", "dps", "pierce", "battlemount"];

type SortKey = "battles" | "kills" | "deaths" | "last_seen" | `role:${string}`;

function EntityTable({ items, mode }: { items: (GuildMember | AllianceGuild)[]; mode: "guild" | "alliance" }) {
  const t = useT();
  const [sortKey, setSortKey] = useState<SortKey>("battles");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);

  const filterRole = sortKey.startsWith("role:") ? sortKey.slice(5) : null;
  const maxBattles = useMemo(() => Math.max(...items.map(m => m.battles), 1), [items]);
  const isGuild = mode === "guild";

  function toggle(k: SortKey) {
    if (sortKey === k) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortKey(k); setSortDir("desc"); }
    setPage(0);
  }

  const filtered = useMemo(() =>
    filterRole
      ? items.filter(m => ((m as GuildMember).roles?.[filterRole] ?? 0) > 0)
      : items,
    [items, filterRole]
  );

  const sorted = useMemo(() => [...filtered].sort((a, b) => {
    const va = filterRole
      ? ((a as GuildMember).roles?.[filterRole] ?? 0)
      : (a[sortKey as keyof typeof a] ?? 0) as number | string;
    const vb = filterRole
      ? ((b as GuildMember).roles?.[filterRole] ?? 0)
      : (b[sortKey as keyof typeof b] ?? 0) as number | string;
    return sortDir === "desc" ? (va < vb ? 1 : va > vb ? -1 : 0) : (va < vb ? -1 : va > vb ? 1 : 0);
  }), [filtered, sortKey, sortDir, filterRole]);

  const paged = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);

  function presenceColor(b: number) {
    const pct = b / maxBattles;
    return pct >= 0.6 ? "var(--green)" : pct >= 0.3 ? "var(--gold)" : "var(--muted)";
  }

  const Th = ({ col, label }: { col: SortKey; label: string }) => (
    <button onClick={() => toggle(col)}
      className={`whitespace-nowrap text-right text-[10px] uppercase tracking-wide transition-colors ${sortKey === col ? "text-amber-400" : "text-zinc-600 hover:text-zinc-400"}`}>
      {label}{sortKey === col ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
    </button>
  );

  // guild:    name | battles | kills | deaths | [gap] | tank…bm | last
  // alliance: name | battles | kills | deaths | last
  const cols = isGuild
    ? "grid-cols-[1fr_52px_44px_52px_12px_36px_36px_36px_36px_36px_36px_56px]"
    : "grid-cols-[1fr_60px_52px_52px_60px]";

  return (
    <div>
      <div className={`mb-1 grid ${cols} gap-x-2 gap-y-0 px-3 items-center`}>
        <span className="text-left text-[10px] uppercase tracking-wide text-zinc-600">
          {isGuild ? t("colPlayer") : t("gpGuildWord")}
        </span>
        <Th col="battles" label={t("battlesCountSuffix")} />
        <Th col="kills" label={t("killsSuffix")} />
        <Th col="deaths" label={t("deathsWord")} />
        {isGuild && <span />}
        {isGuild && ROLE_ORDER.map(r => (
          <button key={r} title={r} onClick={() => toggle(`role:${r}`)}
            className={`flex justify-center transition-colors ${filterRole === r ? "text-amber-400" : "text-zinc-500 hover:text-zinc-300"}`}>
            <RoleIcon role={r} size={16} />
          </button>
        ))}
        <Th col="last_seen" label={t("colLastSeen")} />
      </div>

      <div className="flex flex-col gap-px">
        {paged.map(m => {
          const roles = isGuild ? ((m as GuildMember).roles ?? {}) : null;
          return (
            <div key={m.albion_id}
              className={`grid ${cols} gap-x-2 rounded px-3 py-1.5 text-right text-sm hover:bg-zinc-800/40 cursor-pointer items-center`}
              onClick={() => navigate(
                isGuild
                  ? `/${REGION_PREFIX[(m as GuildMember).region] ?? "am"}/${encodeURIComponent(m.name)}`
                  : `/guild/${m.albion_id}`
              )}>
              {(m as GuildMember).is_deleted
                ? <span className="text-left text-sm text-zinc-600 truncate line-through" title={t("playerDeletedTitle")}>{m.name}</span>
                : <span className="text-left text-sm text-zinc-100 truncate hover:text-amber-400">{m.name}</span>
              }
              <span className="tabular-nums font-medium" style={{ color: presenceColor(m.battles) }}>{m.battles}</span>
              <span className="tabular-nums text-blue-400">{m.kills}</span>
              <span className="tabular-nums text-red-400">{m.deaths}</span>
              {roles && <span />}
              {roles && ROLE_ORDER.map(r => (
                <span key={r} className={`text-center text-xs tabular-nums transition-colors ${filterRole === r ? "text-zinc-200 font-medium" : "text-zinc-500"}`}>
                  {roles[r] ?? 0}
                </span>
              ))}
              <span className="tabular-nums text-zinc-600 text-xs">{timeAgo(m.last_seen)}</span>
            </div>
          );
        })}
      </div>

      <Pagination page={page} total={totalPages} onChange={p => { setPage(p); }} />
    </div>
  );
}

/* ── Main ────────────────────────────────────────────────────────── */

export default function GuildProfilePage({ mode, albionId, onBack }: {
  mode: "guild" | "alliance";
  albionId: string;
  onBack: () => void;
}) {
  const t = useT();
  const [data, setData] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [win, setWin] = useState<Win>("30d");
  const [tab, setTab] = useState<Tab>("members");
  const [battlesPage, setBattlesPage] = useState<BattlesPage | null>(null);
  const [battlesLoading, setBattlesLoading] = useState(false);
  const [battlesCurPage, setBattlesCurPage] = useState(0);
  const [minPlayers, setMinPlayers] = useState("25");
  const [minKills, setMinKills] = useState("5");
  const [filteredMembers, setFilteredMembers] = useState<(GuildMember | AllianceGuild)[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [entityDeleted, setEntityDeleted] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStage, setRefreshStage] = useState<string | null>(null);
  const [loadStage, setLoadStage] = useState<string | null>(null);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const refreshPollRef = useRef<object | null>(null);
  const refreshIdentityRef = useRef("");
  refreshIdentityRef.current = `${mode}\0${albionId}`;
  const refreshingRef = useRef(false);
  useEffect(() => { refreshingRef.current = refreshing; }, [refreshing]);

  // Guarda de resposta obsoleta (mesmo padrão de GuildConfig.tsx): ao trocar
  // de mode/albionId (ex.: clicar na aliança a partir do perfil da guilda),
  // um fetch da entidade ANTERIOR pode ainda estar em voo — sem isso, se ele
  // resolver DEPOIS do fetch da entidade nova (comum sob contenção/lentidão
  // do backend, onde a ordem de chegada não é garantida), ele sobrescreve os
  // dados certos com os da entidade antiga (era o bug relatado: trocar pra
  // aliança e continuar vendo os jogadores da guilda anterior).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setRetryAttempt(0);
    setData(null);
    setBattlesPage(null);
    setBattlesCurPage(0);
    setMinPlayers("25");
    setMinKills("5");
    setFilteredMembers([]);
    setEntityDeleted(false);
    setTab("members");
    setRefreshing(false);
    setRefreshStage(null);
    refreshPollRef.current = null;
    const endpoint = mode === "guild" ? "guilds" : "alliances";
    const TRANSIENT = new Set([502, 503, 504]);
    const attempt = (delay: number) => {
      if (cancelled) return;
      fetch(`${API}/public/${endpoint}/${encodeURIComponent(albionId)}`)
        .then(async r => {
          if (cancelled) return null;
          if (TRANSIENT.has(r.status)) {
            // instabilidade — o LoadProgress mostra o aviso de retry
            setRetryAttempt(a => a + 1);
            setTimeout(() => attempt(Math.min(delay * 2, 30_000)), delay);
            return null;
          }
          if (!r.ok) throw new Error(`${r.status}`);
          return r.json();
        })
        .then((d: ProfileOrCold | null) => {
          if (cancelled || d === null) return;
          // Cold load em andamento: backend disparou task em background e
          // retornou stub. NÃO setData — mantém loading=true e re-tenta em 2s.
          // A barra de progresso continua de onde estava (o stage vem do
          // /load-progress, que é alimentado pela task em background, não por
          // esta request). Quando a task termina, o resultado é cacheado e a
          // próxima leitura retorna o perfil completo.
          if ("_cold_load" in d) {
            setTimeout(() => attempt(2_000), 2_000);
            return;
          }
          setData(d); setLoading(false);
        })
        .catch(e => { if (!cancelled) { setError(e.message); setLoading(false); } });
    };
    attempt(3_000);
    fetch(`${API}/public/${endpoint}/${encodeURIComponent(albionId)}/check`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setEntityDeleted(d.exists === false); })
      .catch(() => {});
    return () => { cancelled = true; refreshPollRef.current = null; };
  }, [mode, albionId]);

  useEffect(() => {
    if (data && "name" in data) document.title = data.name;
    return () => { document.title = "Ziggs"; };
  }, [data]);
  // no backend leva ~1min pra guildas grandes; só roda enquanto carrega.
  useEffect(() => {
    if (!loading) { setLoadStage(null); return; }
    let alive = true;
    const iv = setInterval(() => {
      fetch(`${API}/public/load-progress/${mode}/${encodeURIComponent(albionId)}`)
        .then(r => r.json())
        .then(d => { if (alive) setLoadStage(d.stage ?? null); })
        .catch(() => {});
    }, 1000);
    return () => { alive = false; clearInterval(iv); };
  }, [loading, mode, albionId]);
  // Mesmo padrão do fix dos Membros: o payload principal já traz a página 0
  // com os filtros padrão prontos — usa direto, sem reabrir a aba e esperar
  // de novo pela mesma busca que o cold-load acabou de fazer.
  useEffect(() => {
    if (!data) return;
    setBattlesPage(data.battles_page0);
  }, [data]);

  useEffect(() => {
    if (tab !== "battles") return;
    // Página 0 com filtro padrão já veio no payload — só busca ao vivo
    // quando o usuário pagina ou muda o filtro de verdade.
    if (battlesCurPage === 0 && minPlayers === "25" && minKills === "5") return;
    let cancelled = false;
    setBattlesLoading(true);
    const endpoint = mode === "guild" ? "guilds" : "alliances";
    const params = new URLSearchParams({
      page: String(battlesCurPage),
      min_players: minPlayers || "0",
      min_kills: minKills || "0",
    });
    fetch(`${API}/public/${endpoint}/${encodeURIComponent(albionId)}/battles?${params}`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setBattlesPage(d); })
      .finally(() => { if (!cancelled) setBattlesLoading(false); });
    return () => { cancelled = true; };
  }, [tab, albionId, mode, battlesCurPage, minPlayers, minKills]);
  // O payload principal (cold-load) já traz members/guilds com os MESMOS
  // filtros padrão (25/5 — ver profiles.py) que esta aba usa de início. Usa
  // direto, sem round-trip: evita refazer a mesma agregação pesada que
  // acabou de rodar segundos/minutos atrás só pra mostrar a mesma coisa.
  useEffect(() => {
    if (!data) return;
    setFilteredMembers(("members" in data ? data.members : data.guilds) ?? []);
  }, [data]);

  // Só busca ao vivo quando o filtro sai do padrão embutido no payload —
  // reabrir o mesmo perfil sem mexer no filtro não deveria re-agregar do zero.
  useEffect(() => {
    if (minPlayers === "25" && minKills === "5") return;
    let cancelled = false;
    setMembersLoading(true);
    const endpoint = mode === "guild" ? "guilds" : "alliances";
    const params = new URLSearchParams({ min_players: minPlayers || "0", min_kills: minKills || "0" });
    fetch(`${API}/public/${endpoint}/${encodeURIComponent(albionId)}/members?${params}`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setFilteredMembers(d); })
      .catch(() => { if (!cancelled) setFilteredMembers([]); })
      .finally(() => { if (!cancelled) setMembersLoading(false); });
    return () => { cancelled = true; };
  }, [albionId, mode, minPlayers, minKills]);

  // Botão ⟳: enfileira no backend (POST /refresh) e faz polling até
  // refresh_requested_at sumir — mesmo padrão do PlayerProfilePage. O estado
  // é COMPARTILHADO: enquanto refresh_requested_at != null, qualquer outro
  // usuário olhando o mesmo perfil também vê "atualizando". Cooldown de 5min.
  function forceRefresh() {
    if (refreshing) return;
    const identity = `${mode}\0${albionId}`;
    setRefreshing(true);
    setRefreshStage("queued");
    fetch(`${API}/public/${mode === "guild" ? "guilds" : "alliances"}/${encodeURIComponent(albionId)}/refresh`, { method: "POST" })
      .then(r => r.json())
      .then((res: { queued?: boolean; cooldown_seconds?: number }) => {
        if (refreshIdentityRef.current !== identity) return;
        if (!res.queued) {
          // Cooldown — perfil acabou de ser atualizado há menos de 5min.
          setRefreshing(false);
          setRefreshStage(null);
          return;
        }
        startRefreshPoll();
      })
      .catch(() => { if (refreshIdentityRef.current === identity) startRefreshPoll(); });  // POST falhou — tenta polling do estado
  }

  function startRefreshPoll() {
    const endpoint = mode === "guild" ? "guilds" : "alliances";
    const identity = `${mode}\0${albionId}`;
    if (refreshIdentityRef.current !== identity) return;
    const run = {};
    refreshPollRef.current = run;
    const active = () => refreshPollRef.current === run && refreshIdentityRef.current === identity;
    const poll = () => {
      if (!active()) return;
      fetch(`${API}/public/${endpoint}/${encodeURIComponent(albionId)}`)
        .then(r => (r.ok ? r.json() : null))
        .then((d: ProfileOrCold | null) => {
          if (!active()) return;
          if (!d) { setTimeout(poll, 2_000); return; }
          // O refresh do warmer bumpa last_seen_at, o que invalida o
          // cold-cache — o próximo poll pode pegar a rota disparando um novo
          // cold-load e devolvendo o stub (_cold_load), não o perfil
          // completo. stub.refresh_requested_at é sempre undefined, então
          // sem essa checagem `stillRefreshing` dava falso NEGATIVO (achava
          // que tinha terminado) e `setData` recebia um objeto sem
          // members/guilds — quebrava o render. Só continua esperando.
          if ("_cold_load" in d) { setTimeout(poll, 2_000); return; }
          const stillRefreshing = d.refresh_requested_at != null;
          setData(d);
          if (stillRefreshing) setTimeout(poll, 2_000);
          else {
            // Refresh terminou — busca o stage final pra pegar error:timeout.
            fetch(`${API}/public/refresh-progress/${mode}/${encodeURIComponent(albionId)}`)
              .then(r => (r.ok ? r.json() : null))
              .then((sd: { stage?: string | null } | null) => {
                if (!active()) return;
                if (sd?.stage?.startsWith("error:")) setRefreshStage(sd.stage);
                else setRefreshStage(null);
                setRefreshing(false);
                refreshPollRef.current = null;
              })
              .catch(() => { if (active()) { setRefreshStage(null); setRefreshing(false); refreshPollRef.current = null; } });
          }
        })
        .catch(() => { if (active()) setTimeout(poll, 3_000); });
    };
    const stagePoll = () => {
      if (!active()) return;
      fetch(`${API}/public/refresh-progress/${mode}/${encodeURIComponent(albionId)}`)
        .then(r => (r.ok ? r.json() : null))
        .then((d: { stage?: string | null } | null) => {
          if (!active()) return;
          if (d?.stage) setRefreshStage(d.stage);
        })
        .catch(() => {})
        .finally(() => { if (active() && refreshingRef.current) setTimeout(stagePoll, 1_000); });
    };
    setTimeout(poll, 2_000);
    setTimeout(stagePoll, 500);
  }

  function refreshStageLabel(): string {
    if (!refreshStage) return t("refreshingLabel");
    if (refreshStage.startsWith("error:")) {
      if (refreshStage === "error:timeout") return t("refreshStageTimeout");
      return t("refreshingLabel");
    }
    const map: Record<string, string> = {
      queued: t("refreshStageQueued"),
      fetching: t("refreshStageFetching"),
      building: t("refreshStageBuilding"),
    };
    return map[refreshStage] ?? t("refreshingLabel");
  }

  // Se o perfil chegou com refresh_requested_at != null (outro usuário pediu),
  // entra no polling automaticamente — todo mundo vê "atualizando" desde o início.
  useEffect(() => {
    if (data?.refresh_requested_at && !refreshing && !refreshPollRef.current) {
      setRefreshing(true);
      setRefreshStage("queued");
      startRefreshPoll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.refresh_requested_at]);

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <button onClick={onBack} className="mb-6 flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-300">
          <i className="ti ti-arrow-left" /> {t("back")}
        </button>
        <LoadProgress
          key={`${mode}:${albionId}`}
          steps={[
            { key: "stats", label: t("gpStepStats") },
            { key: "silver", label: t("silverDroppedLabel") },
            { key: "members", label: mode === "guild" ? t("membersTabLabel") : t("guildsLabel") },
            { key: "history", label: mode === "guild" ? t("allianceHistoryTabLabel") : t("rosterHistoryTabLabel") },
          ]}
          stage={loadStage}
          retrying={retryAttempt > 0}
          hint={t("gpCrunching")}
        />
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-64 rounded bg-zinc-800" />
          <div className="grid grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-16 rounded-lg bg-zinc-800/60" />)}
          </div>
          <div className="h-10 rounded bg-zinc-800/40" />
          <div className="space-y-2">
            {Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-9 rounded bg-zinc-800/30" />)}
          </div>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <button onClick={onBack} className="mb-6 flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-300">
          <i className="ti ti-arrow-left" /> {t("back")}
        </button>
        <div className="rounded-lg border border-red-900/50 bg-red-950/20 px-4 py-6 text-center">
          <p className="text-red-400">{error === "404" ? (mode === "guild" ? t("gpGuildNotFound") : t("gpAllianceNotFound")) : `${t("gpLoadError")} (${error}).`}</p>
        </div>
      </div>
    );
  }

  const members = ("members" in data ? data.members : data.guilds) ?? [];
  const { kill_fame, silver_dropped, battles, battles_count } = data;
  const allianceHistory = mode === "guild" ? (data as GuildProfile).alliance_history ?? [] : [];
  const rosterLog = mode === "alliance" ? (data as AllianceProfile).roster_log ?? [] : [];

  const tabs: { id: Tab; label: string }[] = [
    { id: "members", label: mode === "guild" ? `${t("membersTabLabel")} (${members.length})` : `${t("guildsLabel")} (${members.length})` },
    { id: "battles", label: `${t("battles")} (${battles_count})` },
    ...(mode === "guild" ? [{ id: "alliances" as Tab, label: `${t("allianceHistoryTabLabel")} (${eventsToStints(allianceHistory).length})` }] : []),
    ...(mode === "alliance" ? [{ id: "roster" as Tab, label: `${t("rosterHistoryTabLabel")} (${rosterLog.length})` }] : []),
  ];

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      {/* back */}
      <button onClick={onBack} className="mb-5 flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-300">
        <i className="ti ti-arrow-left" /> {t("back")}
      </button>

      {/* header */}
      <Panel className="mb-5 px-5 py-4">
        <div className="mb-3 flex flex-wrap items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold text-zinc-100">{data.name}</h1>
              <span className="rounded border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
                {mode === "guild" ? t("gpGuildWord") : t("gpAllianceWord")}
              </span>
              {entityDeleted && (
                <span className="rounded border border-red-900/60 bg-red-950/40 px-2 py-0.5 text-[10px] text-red-400 uppercase tracking-wide">
                  {t("deletedBadge")}
                </span>
              )}
            </div>
            {mode === "guild" && (data as GuildProfile).alliance_name && (() => {
              const gp = data as GuildProfile;
              return (
                <p className="mt-0.5 text-sm text-zinc-500">
                  {t("gpAllianceLabel")}{" "}
                  {gp.alliance_id
                    ? <button onClick={() => navigate(`/alliance/${gp.alliance_id}`)} className="font-medium text-zinc-300 hover:text-amber-400 transition-colors">[{gp.alliance_name}]</button>
                    : <span className="font-medium text-zinc-300">[{gp.alliance_name}]</span>
                  }
                </p>
              );
            })()}
            <p className="mt-1 text-sm text-zinc-500">
              {data.kills_total.toLocaleString("pt-BR")} {t("killsSuffix")} · {data.deaths_total.toLocaleString("pt-BR")} {t("deathsWord")}
            </p>
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-zinc-600">
              <button
                onClick={forceRefresh}
                disabled={refreshing}
                title={t("forceRefreshTitle")}
                className="text-zinc-500 hover:text-amber-400 disabled:opacity-40 transition-colors"
              >
                <i className={`ti ti-refresh inline-block${refreshing ? " animate-spin" : ""}`} aria-hidden="true" />
              </button>
              {refreshing && <span className="refresh-mini-bar" aria-hidden="true" />}
              {refreshing ? (
                <span className="text-amber-400/80">{refreshStageLabel()}</span>
              ) : (
                data.last_synced_at
                  ? ageLabel(data.last_synced_at, t("justNowLabel"), t("justAgoSuffix"))
                  : t("neverSyncedLabel")
              )}
            </div>
          </div>

          {/* window selector */}
          <div className="flex gap-1 text-xs">
            {(["7d", "30d", "all"] as Win[]).map(w => (
              <button key={w} onClick={() => setWin(w)}
                className={`dash-chip ${win === w ? "dash-chip-on" : ""}`}>
                {w === "all" ? t("rangeAll") : w}
              </button>
            ))}
          </div>
        </div>

        {/* stat cards */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-2">
          {[
            { label: "Kill Fame", value: fameShort(kill_fame[win]) },
            { label: t("silverDroppedLabel"), value: fameShort(silver_dropped[win]) },
            { label: t("battles"), value: String(battles[win]) },
          ].map(s => (
            <div key={s.label} className="flex flex-col items-center justify-center gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
              <span className="text-[10px] uppercase tracking-wide text-zinc-500">{s.label}</span>
              <span className="text-sm font-bold tabular-nums text-zinc-100">{s.value}</span>
            </div>
          ))}
        </div>
      </Panel>

      {/* tabs + filter */}
      <div className="mb-4 flex items-end gap-1 border-b border-zinc-800">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${tab === t.id ? "border-amber-500 text-amber-300" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}>
            {t.label}
          </button>
        ))}
        <div className="ml-auto mb-1 flex items-center gap-2.5 rounded-lg border border-zinc-800 bg-zinc-900/40 px-2.5 py-1.5">
          <span className="text-[10px] uppercase tracking-wide text-zinc-600">{t("minPlayersLabel")}</span>
          <input type="text" inputMode="numeric" value={minPlayers}
            onChange={e => { setMinPlayers(e.target.value); setBattlesCurPage(0); }}
            className="w-8 bg-transparent text-xs text-zinc-100 outline-none text-center tabular-nums" />
          <span className="h-3.5 w-px shrink-0 bg-zinc-800" />
          <span className="text-[10px] uppercase tracking-wide text-zinc-600">{t("minKillsLabel")}</span>
          <input type="text" inputMode="numeric" value={minKills}
            onChange={e => { setMinKills(e.target.value); setBattlesCurPage(0); }}
            className="w-8 bg-transparent text-xs text-zinc-100 outline-none text-center tabular-nums" />
        </div>
      </div>

      {/* tab content */}
      {tab === "members" && (
        membersLoading
          ? <div className="py-8 text-center text-sm text-zinc-500">{t("loading")}</div>
          : <EntityTable items={filteredMembers} mode={mode} />
      )}

      {tab === "battles" && (
        <div className="flex flex-col gap-2">
          {battlesLoading && !battlesPage && (
            <div className="py-8 text-center text-sm text-zinc-500">{t("loading")}</div>
          )}
          {!battlesLoading && battlesPage?.battles.length === 0 && (
            <p className="py-8 text-center text-zinc-600">{t("noGuildProfileBattlesYet")}</p>
          )}
          {battlesPage?.battles.map(b => <GuildBattleRow key={b.public_id} b={b} />)}
          {battlesPage && battlesPage.pages > 1 && (
            <Pagination page={battlesCurPage} total={battlesPage.pages} onChange={p => { setBattlesCurPage(p); window.scrollTo(0, 0); }} />
          )}
        </div>
      )}

      {tab === "alliances" && <AllianceHistoryTab events={allianceHistory} />}

      {tab === "roster" && <RosterLogTab events={rosterLog} />}

    </div>
  );
}
