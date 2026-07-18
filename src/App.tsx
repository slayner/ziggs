import { useEffect, useState, useCallback, useRef } from "react";
import type { ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useT, useLang, LANG_LABELS, LANG_FULL, type Lang, type LangPref } from "./i18n";

// Mirrors CompanionConfig from Rust. Character name, region, and backend URL
// are not here — they are auto-detected/hardcoded in the binary. Battles and
// prices are always on (core companion features) — no toggle.
type CompanionConfig = {
  api_base_url: string;
  collect_damage_meter: boolean;
  collect_auto_lootlog: boolean;
  autostart: boolean;
  minimize_to_tray: boolean;
  tunnel_enabled: boolean;
  tunnel_endpoint: string;
  tunnel_server_pubkey: string;
  tunnel_client_privkey: string;
  pvp_pause_transfer: boolean;
  feed_aodp: boolean;
  discord_token: string | null;
  discord_user_id: string | null;
  discord_username: string | null;
  auto_lootlog_submit: boolean;
  install_id: string;
  spell_index_offset: number;
};

type InternetPath = {
  name: string;
  local_ip: string;
  priority: number;
  latency_ms: number | null;
  available: boolean;
  active: boolean;
};

type TunnelStatus = {
  running: boolean;
  connected: boolean;
  direct_latency_ms: number | null;
  tunnel_latency_ms: number | null;
  using_tunnel: boolean;
  last_error: string | null;
  bytes_sent: number;
  bytes_received: number;
  active_interface: string | null;
  failover_count: number;
  internet_paths: InternetPath[];
};

type LootRow = {
  ts: string | null;
  item_id: string;
  item_name: string;
  item_name_pt: string;
  item_name_es: string;
  quantity: number;
  looted_by: string;
  looted_by_guild: string;
  looted_from: string;
};

type SniffStats = {
  running: boolean;
  online: boolean;
  packets_captured: number;
  packets_parsed: number;
  operations_extracted: number;
  loot_count: number;
  /// Aggregated by Rust get_sniff_stats. Feeds the Damage tab badge without
  /// a separate get_damage_meter poll.
  damage_total: number;
  /// Damage dealt by the current player. Aggregated by Rust, like damage_total.
  my_damage: number;
  /// Illustrative silver estimate for the session's loot. Non-critical for
  /// payouts/reconcile — used only for the Lootlog badge.
  loot_silver_total: number;
  last_map: string;
  last_map_name: string;
  last_zone: string;
  player_name: string;
  guild_name: string;
  alliance_name: string;
  party_members: string[];
  error: string | null;
};

type DebugLine = {
  ts: string;
  level: string;
  msg: string;
};

type AuthPollResult = {
  token: string;
  user_id: string;
  username: string;
  global_name: string | null;
};

// Status do Albion para o card da sidebar.
type AlbionStatus =
  | { kind: "ok" }
  | { kind: "closed" }
  | { kind: "sniff_error"; msg: string };

type SkillRow = {
  id: number; name: string | null; unique_name: string | null; icon: string | null;
  name_pt: string | null; name_es: string | null;
  hits: number; total: number; avg: number; max_hit: number; pct: number;
};

/// Tier adjective per language, indexed by tier. Albion item names include the
/// tier word ("Elder's Guardian Boots"), which is redundant in the terminal.
/// Hand-coded table: deriving it from the dump misses cases like "Raw Beef".
const TIER_WORDS: Record<Lang, string[]> = {
  en: ["", "Beginner's", "Novice's", "Journeyman's", "Adept's", "Expert's", "Master's", "Grandmaster's", "Elder's"],
  pt: ["", "do Calouro", "do Novato", "do Iniciante", "do Adepto", "do Perito", "do Mestre", "do Grão-mestre", "do Ancião"],
  es: ["", "del principiante", "del novato", "del obrero", "del iniciado", "del experto", "del maestro", "del gran maestro", "del anciano"],
};


// ── Breathing glow ─────────────────────────────────────────────────────────────
// Mirror of the site's Panel.tsx: hover gate (0.6s fade) + two golden radial
// glows per corner, randomized negative animation-delay. Stored in useState so
// re-renders every 2s poll don't reset the glow phase.
const GLOW_PERIOD_S = 7; // matches dash-glow-breathe animation duration
function CardGlow() {
  const [delays] = useState(() => [Math.random() * GLOW_PERIOD_S, Math.random() * GLOW_PERIOD_S]);
  return (
    <span className="dash-cglowwrap" aria-hidden>
      <span className="dash-cglow dash-cglow-tl" style={{ animationDelay: `-${delays[0].toFixed(2)}s` }} />
      <span className="dash-cglow dash-cglow-br" style={{ animationDelay: `-${delays[1].toFixed(2)}s` }} />
    </span>
  );
}

/// Periodic poll with cleanup. Keeps running when minimized; the Rust
/// `heavy_work_ok` (zone-based) decides when to work, not window state.
/// `fn` is stored in a ref so the timer doesn't recreate on every render.
function usePoll(fn: () => void | Promise<void>, ms: number, deps: unknown[] = []) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    let alive = true;
    const tick = () => { if (alive) void latest.current(); };
    tick();
    const id = setInterval(tick, ms);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

/// Localized item name with the tier word stripped for UI display. Backend
/// sends all three languages; webview language lives in localStorage. The CSV
/// keeps the full original name.
function itemName(r: LootRow, lang: Lang, tier?: number): string {
  const full = lang === "pt" ? r.item_name_pt : lang === "es" ? r.item_name_es : r.item_name;
  const word = tier != null ? TIER_WORDS[lang]?.[tier] : undefined;
  if (!word) return full;
  if (full.startsWith(word + " ")) return full.slice(word.length + 1);
  if (full.endsWith(" " + word)) return full.slice(0, -(word.length + 1));
  return full;  // resources/food items have no tier adjective
}

/// "T4_CAPEITEM_SMUGGLER@3" → `{ label: "4.3", tier: 4, ench: 3 }`.
/// Matches the site's tier notation. Keeps ".0" visible for terminal alignment.
function itemTierParts(itemId: string): { label: string; tier: number; ench: number } | null {
  const m = /^T(\d)_/.exec(itemId);
  if (!m) return null;  // IDX_123 for unknown items
  const tier = Number(m[1]);
  const ench = Number(/@(\d)$/.exec(itemId)?.[1] ?? 0);
  return { label: `${tier}.${ench}`, tier, ench };
}

/// ISO timestamp → "HH:MM". Session log doesn't need date or seconds. UTC,
/// matches the CSV for cross-referencing.
function shortTime(ts: string | null): string {
  return ts?.slice(11, 16) || "";
}

/// Render a loot row in the terminal: tier text color + enchant underline.
/// Enchanted items stand out; unenchanted ones stay plain.
function LootItem({ row, lang }: { row: LootRow; lang: Lang }) {
  const p = itemTierParts(row.item_id);
  const name = itemName(row, lang, p?.tier);
  if (!p) return <span className="t-item" title={row.item_id}>{name}</span>;
  return (
    <span
      className={`t-item t-tier-${p.tier}${p.ench > 0 ? ` t-ench-u t-ench-u-${p.ench}` : ""}`}
      title={row.item_id}
    >
      <span className="t-tier">{p.label}</span> {name}
    </span>
  );
}

/// Localized skill name. Many internal sub-spells have no translation, so
/// they fall back to English.
function skillName(sk: SkillRow, lang: Lang): string | null {
  if (lang === "pt") return sk.name_pt ?? sk.name;
  if (lang === "es") return sk.name_es ?? sk.name;
  return sk.name;
}

/// Auto-attack has no game art, so reuse skill icons by attack type.
/// Use stable unique IDs, not localized names, in case the game renames skills.
const AUTO_ATTACK_ICON = {
  melee: "PASSIVE_KNOCKBACKCHANCE",          // "Forceful Bolts"
  ranged: "SPEEDSHOT2",                      // "Speed Shot"
  magic: "PASSIVE_ATTACKBUFF_ARCANESTAFF",   // "Lingering Power"
};

const MELEE_FAMS = new Set([
  "sword", "axe", "mace", "hammer", "quarterstaff", "spear", "dagger", "knuckles",
]);
const RANGED_FAMS = new Set(["bow", "crossbow"]);

/// Auto-attack type from weapon family. Staff families are magical; add any
/// new melee/ranged families to the corresponding set.
function autoAttackKind(weapon: string | null): keyof typeof AUTO_ATTACK_ICON | null {
  if (!weapon) return null;
  if (MELEE_FAMS.has(weapon)) return "melee";
  if (RANGED_FAMS.has(weapon)) return "ranged";
  return "magic";
}

/// Skill icon served through our backend proxy/cache. First fetch hits Albion
/// CDN, then serves from disk. Same as `/render/item/` on the site.
function SkillIcon({ uniqueName, apiBase, gray }: {
  uniqueName: string | null; apiBase: string; gray?: boolean;
}) {
  if (!uniqueName) return null;
  return (
    <img
      className={`dmg-skill-icon${gray ? " gray" : ""}`}
      // v=2 busts the webview cache. The render response is served with a
      // one-year max-age + immutable; bump this if a placeholder is ever cached.
      src={`${apiBase}/render/spell/${encodeURIComponent(uniqueName)}?v=2`}
      alt=""
      loading="lazy"
      // v=2 busts the webview cache. Hide broken/missing icons instead of showing a placeholder.
      onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
    />
  );
}
type DamageRow = {
  name: string; weapon: string | null;
  damage: number; dps: number; skills: SkillRow[]; timeline: number[];
};

/// Albion weapon family labels, kept short for the damage meter row.
const WEAPON_LABELS: Record<Lang, Record<string, string>> = {
  en: {
    sword: "Sword", axe: "Axe", mace: "Mace", hammer: "Hammer", quarterstaff: "Staff",
    spear: "Spear", dagger: "Dagger", knuckles: "Gloves", bow: "Bow", crossbow: "Crossbow",
    firestaff: "Fire", froststaff: "Frost", arcanestaff: "Arcane", cursestaff: "Curse",
    holystaff: "Holy", naturestaff: "Nature", shapeshifterstaff: "Shifter",
  },
  pt: {
    sword: "Espada", axe: "Machado", mace: "Maça", hammer: "Martelo", quarterstaff: "Bastão",
    spear: "Lança", dagger: "Adaga", knuckles: "Manopla", bow: "Arco", crossbow: "Besta",
    firestaff: "Fogo", froststaff: "Gelo", arcanestaff: "Arcano", cursestaff: "Maldito",
    holystaff: "Sagrado", naturestaff: "Natureza", shapeshifterstaff: "Metamorfo",
  },
  es: {
    sword: "Espada", axe: "Hacha", mace: "Maza", hammer: "Martillo", quarterstaff: "Bastón",
    spear: "Lanza", dagger: "Daga", knuckles: "Guantes", bow: "Arco", crossbow: "Ballesta",
    firestaff: "Fuego", froststaff: "Hielo", arcanestaff: "Arcano", cursestaff: "Maldito",
    holystaff: "Sagrado", naturestaff: "Naturaleza", shapeshifterstaff: "Metamorfo",
  },
};


export default function App() {
  const t = useT();
  const { pref, setPref } = useLang();
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [sniffStats, setSniffStats] = useState<SniffStats | null>(null);
  const [tunnelStatus, setTunnelStatus] = useState<TunnelStatus | null>(null);
  const [updateStatus, setUpdateStatus] = useState<"downloading" | "installed" | null>(null);
  // Live tabs: Route/Tunnel is the default; Damage and Lootlog have their own
  // tabs. Badges read App state because tab components unmount when not focused.
  const [gearOpen, setGearOpen] = useState(false);
  // Local connection details from tunnel failover.
  const [routesOpen, setRoutesOpen] = useState(false);
  const [tab, setTab] = useState<"route" | "damage" | "loot">("route");
  const [pending, setPending] = useState(0);
  // Damage Meter filters live in App state, not DamageTab, so they survive
  // tab switching.
  const [dmgPartyOnly, setDmgPartyOnly] = useState(false);
  const [dmgVsPlayers, setDmgVsPlayers] = useState(false);
  // Npcap tutorial shown every session while Npcap is missing. Dismissing it
  // only hides the modal; the sidebar banner remains.
  const [npcapTutorialDismissed, setNpcapTutorialDismissed] = useState(false);
  // Tunnel vs direct latency history lives in App, not TunnelHero, to survive
  // tab switching. 120 samples × 5s = 10 min.
  const [hist, setHist] = useState<{ d: number | null; tn: number | null }[]>([]);
  // Session clock (1s tick) + packet rate derived from poll deltas. Rust only
  // exposes accumulated counts, not rate.
  const sessionStart = useRef(Date.now());
  const lastHeaderClick = useRef(0);
  const [nowTs, setNowTs] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNowTs(Date.now()), 1000); return () => clearInterval(id); }, []);
  const prevPkt = useRef<{ n: number; t: number } | null>(null);
  const [pktRate, setPktRate] = useState(0);

  const refreshConfig = useCallback(async () => {
    const c = await invoke<CompanionConfig>("get_config");
    setConfig(c);
  }, []);

  useEffect(() => {
    refreshConfig();
    let unlisten: UnlistenFn | null = null;
    listen("scanner-restart", () => {
      invoke("stop_scanner").then(() => invoke("start_scanner"));
    }).then((u) => (unlisten = u));
    let unlistenUpd: UnlistenFn | null = null;
    listen<string>("update-status", (e) => {
      setUpdateStatus(e.payload as "downloading" | "installed");
    }).then((u) => (unlistenUpd = u));
    let unlistenCfg: UnlistenFn | null = null;
    listen("config-changed", () => { refreshConfig(); }).then((u) => (unlistenCfg = u));
    return () => { unlisten?.(); unlistenUpd?.(); unlistenCfg?.(); };
  }, [refreshConfig]);

  const updateConfig = async (key: keyof CompanionConfig, value: unknown) => {
    await invoke("set_config", { key, value: value as never });
    await refreshConfig();
  };

  // Poll sniffer stats + tunnel status. Scanner/collection stats run silently
  // in the background.
  usePoll(async () => {
    try {
      const st = await invoke<SniffStats>("get_sniff_stats");
      setSniffStats(st);
      const now = Date.now();
      if (prevPkt.current && now > prevPkt.current.t) {
        setPktRate(Math.max(0, (st.packets_captured - prevPkt.current.n) / ((now - prevPkt.current.t) / 1000)));
      }
      prevPkt.current = { n: st.packets_captured, t: now };
    } catch { /* sniffer unavailable */ }
    try {
      const ts = await invoke<TunnelStatus>("tunnel_status");
      setTunnelStatus(ts);
      setHist(h => [...h.slice(-119), { d: ts.direct_latency_ms, tn: ts.tunnel_latency_ms }]);
    } catch { /* tunnel unavailable */ }
  }, 5000);

  usePoll(async () => {
    try { setPending(await invoke<number>("pending_count")); } catch { /* sem fila */ }
  }, 15000);

  const albionStatus: AlbionStatus = (() => {
    if (sniffStats?.error && sniffStats.running) {
      return { kind: "sniff_error", msg: sniffStats.error };
    }
    // Online/offline from sniffer: Albion packets arriving = online.
    if (!sniffStats?.online) return { kind: "closed" };
    return { kind: "ok" };
  })();

  if (!config) {
    return <div className="splash"><div className="splash-logo">Z</div><div className="splash-text">{t("splashText")}</div></div>;
  }

  const npcapMissing = !!(sniffStats?.error && /npcap/i.test(sniffStats.error));
  const playerName = sniffStats?.player_name || "";
  const zone = sniffStats?.last_zone || "unknown";
  const up = Math.max(0, Math.floor((nowTs - sessionStart.current) / 1000));
  const uptime = `${String(Math.floor(up / 3600)).padStart(2, "0")}:${String(Math.floor((up % 3600) / 60)).padStart(2, "0")}:${String(up % 60).padStart(2, "0")}`;

  // Switch back to Route if the active feature is disabled (tab would be
  // disabled).
  if (tab === "damage" && !config.collect_damage_meter) setTab("route");
  if (tab === "loot" && !config.collect_auto_lootlog) setTab("route");

  return (
    <div className="ck-root">
      <header
        className="ck-bar"
        onMouseDown={(e) => {
          // Drag only on left-click outside interactive elements. Programmatic
          // Tauri API is more reliable than data-tauri-drag-region in React.
          if (e.button !== 0 || (e.target as HTMLElement).closest("button") != null) return;
          // Manual double-click detection: startDragging() releases capture, so
          // DOM dblclick is unreliable. Detect the second click by mousedown timing.
          const now = Date.now();
          if (now - lastHeaderClick.current < 400) {
            lastHeaderClick.current = 0;
            getCurrentWindow().toggleMaximize();
            return;
          }
          lastHeaderClick.current = now;
          getCurrentWindow().startDragging();
        }}
      >
        <span className="logo">Z</span>
        <span className="ck-brand">ZIGGS</span>
        <span className="ck-sep" />
        <span className="ck-chip"><span className="ck-lbl">{t("ckSession")}</span><b className="ck-num">{uptime}</b></span>
        <span className="ck-chip"><span className="ck-lbl">{t("ckPackets")}</span><b className="ck-num">{pktRate >= 1000 ? `${(pktRate / 1000).toFixed(1)}k/s` : `${Math.round(pktRate)}/s`}</b></span>
        <span className="ck-chip"><span className="ck-lbl">{t("ckQueue")}</span><b className="ck-num">{pending}</b></span>
        {config.feed_aodp && (
          <span className="ck-chip"><span className="ck-lbl">AODP</span><b className="ck-num ck-ok">●</b></span>
        )}
        <span className="ck-winbtns">
          <button className="ck-winbtn" onClick={() => getCurrentWindow().minimize()} title="Minimizar" aria-label="minimize">
            <svg viewBox="0 0 10 10" width="10" height="10"><rect x="1" y="4.5" width="8" height="1" fill="currentColor"/></svg>
          </button>
          <button className="ck-winbtn" onClick={() => getCurrentWindow().toggleMaximize()} title="Maximizar" aria-label="maximize">
            <svg viewBox="0 0 10 10" width="10" height="10"><rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1"/></svg>
          </button>
          <button className="ck-winbtn ck-winbtn-close" onClick={() => getCurrentWindow().close()} title="Fechar" aria-label="close">
            <svg viewBox="0 0 10 10" width="10" height="10"><path d="M1.5 1.5 L8.5 8.5 M8.5 1.5 L1.5 8.5" stroke="currentColor" strokeWidth="1.2" fill="none"/></svg>
          </button>
        </span>
      </header>

      {/* Sidebar on the left + content on the right. Tabs live in the sidebar
          with toggles and expanded details when active, regardless of selected
          tab. Footer shows player/map/Albion status and the config button. */}
      <div className="ck-shell">
        <aside className="ck-side">
          <nav className="ck-side-tabs">
            <SideTab
              label={t("navTunnel")}
              value={tunnelStatus?.using_tunnel && tunnelStatus?.tunnel_latency_ms != null ? `${tunnelStatus.tunnel_latency_ms.toFixed(0)}ms` : ""}
              valueTone="ok"
              selected={tab === "route"}
              onSelect={() => setTab("route")}
              expandedContent={
                tunnelStatus?.using_tunnel && tunnelStatus.direct_latency_ms != null && tunnelStatus.tunnel_latency_ms != null && tunnelStatus.direct_latency_ms > 0
                  ? `−${(tunnelStatus.direct_latency_ms - tunnelStatus.tunnel_latency_ms).toFixed(0)}ms`
                  : null
              }
            />
            <SideTab
              label={t("navDamage")}
              value={fmtFull(sniffStats?.damage_total ?? 0)}
              valueTone="ok"
              selected={tab === "damage"}
              onSelect={() => setTab("damage")}
              onToggle={config.collect_damage_meter ? () => updateConfig("collect_damage_meter", false) : () => updateConfig("collect_damage_meter", true)}
              toggleOn={config.collect_damage_meter}
              inspectable={config.collect_damage_meter}
            />
            <SideTab
              label="Lootlog"
              value={String(sniffStats?.loot_count ?? 0)}
              valueTone="ok"
              selected={tab === "loot"}
              onSelect={() => setTab("loot")}
              onToggle={config.collect_auto_lootlog ? () => updateConfig("collect_auto_lootlog", false) : () => updateConfig("collect_auto_lootlog", true)}
              toggleOn={config.collect_auto_lootlog}
              inspectable={config.collect_auto_lootlog}
            />
          </nav>

          {/* Two vertical ad slots below the tabs. Sidebar width already fits a
              300px creative with minor overflow only once the creative loads. */}
          <div className="ck-side-ads">
            <AdSlot variant="side" />
            <AdSlot variant="side" />
          </div>

          {/* Npcap missing banner inside the sidebar. Manual install only. */}
          {npcapMissing && (
            <div className="ck-npcap ck-side-npcap">
              <span>{t("npcapNeeded")}</span>
              <button className="btn small" onClick={() => invoke("open_npcap_download")}>
                {t("npcapInstall")}
              </button>
              <span className="ck-npcap-hint">{t("npcapHint")}</span>
            </div>
          )}

          {/* Sidebar footer: player/map/Albion status + config. Always visible. */}
          <div className="ck-side-foot">
            <div className="ck-side-status" title={albionStatus.kind === "sniff_error" ? albionStatus.msg : t("albionClosedHint")}>
              <span className={`status-dot ${albionStatus.kind === "ok" ? "on" : "off"}`} />
              {albionStatus.kind === "ok" ? (
                <div className="ck-side-status-main">
                  <b>{playerName || t("statusLoading")}</b>
                  {sniffStats?.guild_name && <span className="ck-side-guild">{sniffStats.guild_name}</span>}
                </div>
              ) : (
                <div className="ck-side-status-main">
                  <b className="ck-off">{albionStatus.kind === "closed" ? t("albionClosed") : t("albionSniffError")}</b>
                </div>
              )}
            </div>
            {sniffStats?.last_map_name && (
              <div className={`ck-side-zone ${zone}`}>
                {sniffStats.last_map_name}{zone === "blue" ? ` · ${t("ckZoneBlue")}` : zone === "pvp" ? " · PVP" : ""}
              </div>
            )}
            <button className="ck-side-gear" onClick={() => setGearOpen(true)} title={t("navConfig")}>
              <Icon name="gear" />
              <span>{t("navConfig")}</span>
            </button>
            <DiscordButton config={config} onChange={refreshConfig} />
          </div>
        </aside>

        <main className="ck-main">
          {tab === "route" && (
            <div className="ck-route-col">
              <div className="ck-route-scroll">
                <TunnelHero config={config} tunnelStatus={tunnelStatus} hist={hist} onOpenRoutes={() => setRoutesOpen(true)} />
                <ConnPanel config={config} tunnelStatus={tunnelStatus} />
              </div>
              <AdSlot />
            </div>
          )}

          {tab === "damage" && (
            <div className="ck-full">
              <DamageTab
                config={config} update={updateConfig} sniffStats={sniffStats}
                partyOnly={dmgPartyOnly} setPartyOnly={setDmgPartyOnly}
                vsPlayers={dmgVsPlayers} setVsPlayers={setDmgVsPlayers}
              />
              <AdSlot />
            </div>
          )}
          {tab === "loot" && (
            <div className="ck-full">
              <LootlogTab config={config} update={updateConfig} sniffStats={sniffStats} />
              <AdSlot />
            </div>
          )}
        </main>
      </div>

      {gearOpen && (
        <div className="ck-modal-backdrop" onClick={() => setGearOpen(false)}>
          <div className="ck-modal" onClick={e => e.stopPropagation()}>
            <div className="ck-modal-head">
              <h2>{t("navConfig")}</h2>
              <button className="ck-modal-close" onClick={() => setGearOpen(false)} aria-label="close">✕</button>
            </div>
            <div className="ck-modal-body">
              <ConfigTab config={config} update={updateConfig} lang={pref} setLang={setPref} npcapMissing={npcapMissing} />
            </div>
          </div>
        </div>
      )}

      {routesOpen && (
        <InternetPathsModal status={tunnelStatus} onClose={() => setRoutesOpen(false)} />
      )}

      {/* Npcap tutorial shown every session while Npcap is missing. Dismissal
          is per-session; the sidebar banner remains. */}
      {npcapMissing && !npcapTutorialDismissed && (
        <div className="ck-modal-backdrop" onClick={() => setNpcapTutorialDismissed(true)}>
          <div className="ck-modal" onClick={e => e.stopPropagation()}>
            <div className="ck-modal-head">
              <h2>{t("npcapTutorialTitle")}</h2>
              <button className="ck-modal-close" onClick={() => setNpcapTutorialDismissed(true)} aria-label="close">✕</button>
            </div>
            <div className="ck-modal-body">
              <p className="card-desc">{t("npcapTutorialIntro")}</p>
              <ol className="ck-npcap-steps">
                <li>{t("npcapStep1")}</li>
                <li>{t("npcapStep2")}</li>
                <li>{t("npcapStep3")}</li>
              </ol>
              <div className="ck-npcap-modal-actions">
                <button className="btn" onClick={() => invoke("open_npcap_download")}>
                  {t("npcapInstall")}
                </button>
                <button className="btn small" onClick={() => setNpcapTutorialDismissed(true)}>
                  {t("npcapDismiss")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {updateStatus && (
        <div className="update-toast">
          <span className="update-toast-dot" />
          <span>{updateStatus === "downloading" ? t("updateDownloading") : t("updateInstalling")}</span>
        </div>
      )}
    </div>
  );
}


// ─── Vertical sidebar ───────────────────────────────────────────────────────

/// Sidebar tab. When a feature is on, the tab expands and shows
/// `expandedContent` regardless of selected tab. The badge value updates live
//  from App state, not from this component.
function SideTab({
  label, value, valueTone, selected, onSelect, onToggle, toggleOn, expandedContent, inspectable = true,
}: {
  label: string;
  value: string;
  valueTone?: "ok" | "muted";
  selected: boolean;
  onSelect: () => void;
  onToggle?: () => void;
  toggleOn?: boolean;
  expandedContent?: ReactNode;
  inspectable?: boolean;
}) {
  const expanded = !!expandedContent && inspectable;
  return (
    <button
      className={`ck-side-tab${selected ? " selected" : ""}${expanded ? " expanded" : ""}${!inspectable ? " disabled" : ""}`}
      // Don't use <button disabled>: disabled buttons don't propagate clicks
      // to children, but the toggle must remain clickable while off.
      onClick={inspectable ? onSelect : undefined}
    >
      <span className="ck-side-tab-head">
        <span className="ck-side-tab-label">{label}</span>
        {onToggle && (
          <div
            className="ck-side-tab-toggle"
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            role="switch"
            aria-checked={toggleOn}
          >
            <Toggle on={!!toggleOn} onChange={() => { /* handlado no onClick do wrapper */ }} />
          </div>
        )}
      </span>
      {inspectable && value && <span className={`ck-side-tab-val ck-num ${valueTone === "ok" ? "ck-ok" : ""}`}>{value}</span>}
      {expanded && <div className="ck-side-tab-body">{expandedContent}</div>}
    </button>
  );
}


// ─── SVG icons (JSX) ────────────────────────────────────────────────────────

type IconName = "scan" | "list" | "route" | "globe" | "gear" | "sword";

function Icon({ name }: { name: IconName }) {
  switch (name) {
    case "scan": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10"/></svg>;
    case "list": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>;
    case "route": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="6" cy="19" r="3"/><circle cx="18" cy="5" r="3"/><path d="M9 19h6a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6"/></svg>;
    case "globe": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg>;
    case "gear": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>;
    case "sword": return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.5 17.5 3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4M19 21l2-2"/></svg>;
  }
}

// ─── Damage meter (packet capture) ──────────────────────────────────────────

// Compact number format: 1234567 → "1.23M", 45600 → "45.6K".
/// Short format with one decimal only when mantissa < 10. Keeps copied
/// ranking compact for party chat.
function fmtC(n: number): string {
  const short = (v: number, suffix: string) => {
    const oneDec = Math.round(v * 10) / 10;
    const txt = oneDec >= 10 ? String(Math.round(v)) : oneDec.toFixed(1);
    return `${txt.replace(/\.0$/, "")}${suffix}`;
  };
  // Cross the boundary early so 999500 doesn't become "1000K".
  if (n >= 999_500) return short(n / 1e6, "M");
  if (n >= 1e3) return short(n / 1e3, "K");
  return String(Math.round(n));
}

/// Full number with thousands separator for badges where the exact value
/// matters (session damage, loot count).
function fmtFull(n: number): string {
  return Math.round(n).toLocaleString("pt-BR");
}

/// 3-minute damage timeline: one bar per second, height relative to the peak.
/// preserveAspectRatio="none" lets the SVG stretch horizontally.
function DamageTimeline({ data }: { data: number[] }) {
  const t = useT();
  const peak = data.reduce((m, v) => Math.max(m, v), 0);
  if (peak <= 0) return <div className="dmg-tl-empty empty-inline">{t("dmgTimelineEmpty")}</div>;
  const nowSec = Math.floor(Date.now() / 1000);
  const last = data.length - 1;
  return (
    <div className="dmg-tl">
      <div className="dmg-tl-head">
        <span>{t("dmgTimeline")}</span>
        <span className="dmg-tl-peak">{t("dmgPeak")}: {fmtC(peak)}/s</span>
      </div>
        <svg className="dmg-tl-svg" viewBox={`0 0 ${data.length} 40`} preserveAspectRatio="none">
        {/* Key by absolute second, not array index. Backend aligns data[last]
            to now; keying by time makes bars slide left smoothly between polls. */}
        {data.map((v, i) => v > 0 && (
          <rect key={nowSec - (last - i)} x={i} y={40 - (v / peak) * 40} width={0.9} height={(v / peak) * 40} />
        ))}
      </svg>
      <div className="dmg-tl-axis">
        <span>-3m</span><span>-2m</span><span>-1m</span><span>{t("dmgNow")}</span>
      </div>
    </div>
  );
}

function DamageTab({
  config, update, sniffStats, partyOnly, setPartyOnly, vsPlayers, setVsPlayers,
}: {
  config: CompanionConfig;
  update: (key: keyof CompanionConfig, value: unknown) => Promise<void>;
  sniffStats: SniffStats | null;
  partyOnly: boolean; setPartyOnly: (v: boolean) => void;
  vsPlayers: boolean; setVsPlayers: (v: boolean) => void;
}) {
  const t = useT();
  const { lang } = useLang();
  const on = config.collect_damage_meter;
  const [rows, setRows] = useState<DamageRow[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);

  useEffect(() => { if (!on) setRows([]); }, [on]);
  // vsPlayers is not a row filter; the backend keeps a separate accumulator
  // because target info is gone once damage is summed by causer. Hence it is a
  // poll dependency.
  usePoll(async () => {
    if (!on) return;
    try {
      setRows(await invoke<DamageRow[]>("get_damage_meter", { vsPlayers }));
    } catch { /* sniffer unavailable */ }
  }, 2000, [on, vsPlayers]);

  // Party filter: party members + self.
  const partySet = new Set([
    ...(sniffStats?.party_members ?? []),
    ...(sniffStats?.player_name ? [sniffStats.player_name] : []),
  ]);

  // Single source of truth for display + copy. vsPlayers already narrowed by
  // the backend poll, so it is not filtered here.
  const filtered = rows.filter(r => !partyOnly || partySet.has(r.name));

  const max = filtered.reduce((m, r) => Math.max(m, r.damage), 0) || 1;
  const totalDmg = filtered.reduce((s, r) => s + r.damage, 0) || 1;

  // Copy visible ranking as text. Excludes map/heal/DPS for mobile readability.
  // Percentages are over the filtered total so paste matches the screen.
  const copyMeter = async () => {
    // No '#': Albion chat treats it as a command. Percentages use filtered total.
    const lines = filtered.map(
      (r, i) => `${i + 1}. ${r.name} — ${fmtC(r.damage)} (${((r.damage / totalDmg) * 100).toFixed(1)}%)`
    );
    await navigator.clipboard.writeText(lines.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="ck-panel ck-dmg"><CardGlow />
      <div className="card-head">
        <h2 title={t("dmgDesc")}>{t("navDamage")}</h2>
      </div>
      {!on ? (
        <div className="empty-area">{t("dmgOffHint")}</div>
      ) : (
        <>
          {/* Cockpit hero: the number you glance at mid-fight. */}
          <div className="ck-hero">
            <b className="ck-num">{fmtC(filtered.length ? totalDmg : 0)}</b>
            <span className="ck-hero-sub">
              {t("ckPartyDmg")} · {t("ckPeak")}{" "}
              <b className="ck-num">{fmtC(filtered.length ? Math.max(...filtered.map(r => r.dps)) : 0)}/s</b>
              {" "}· {t("ckInCombat", { n: filtered.length })}
            </span>
          </div>
          <div className="dmg-filters">
            <button
              className={`dmg-chip${partyOnly ? " active" : ""}`}
              onClick={() => setPartyOnly(!partyOnly)}
              title={t("dmgPartyOnly")}
            >
              {t("dmgPartyOnly")}
            </button>
            <button
              className={`dmg-chip${vsPlayers ? " active" : ""}`}
              onClick={() => setVsPlayers(!vsPlayers)}
              title={t("dmgVsPlayersHint")}
            >
              {t("dmgVsPlayers")}
            </button>
            <span className="dmg-filter-spacer" />
            <button className="btn small" onClick={copyMeter} disabled={filtered.length === 0}>
              {copied ? t("copied") : t("dmgCopy")}
            </button>
            <button className="btn small" onClick={() => invoke("clear_damage_meter").then(() => setRows([]))} disabled={rows.length === 0}>
              {t("clearLoot")}
            </button>
          </div>
          {filtered.length === 0 ? (
            <div className="dmg-list-scroll">
              <div className="empty-area">{t("dmgEmptyHint")}</div>
            </div>
          ) : (
            <div className="dmg-list-scroll">
              <div className="dmg-list">
                {filtered.map((r, i) => (
                  <div key={r.name} className={`dmg-entry${r.weapon ? ` w-${r.weapon}` : ""}`}>
                    <div
                      className={`dmg-row clickable ${expanded[r.name] ? "open" : ""}`}
                      onClick={() => setExpanded(e => ({ ...e, [r.name]: !e[r.name] }))}
                    >
                      <span className="dmg-caret">{expanded[r.name] ? "▾" : "▸"}</span>
                      <span className="dmg-rank">{i + 1}</span>
                      <span className="dmg-name">
                        {r.name}
                        {/* Weapon family inferred from skills; nbsp preserves
                            row height when none is available. */}
                        <small className="dmg-cls">
                          {r.weapon ? WEAPON_LABELS[lang][r.weapon] ?? r.weapon : "\u00A0"}
                        </small>
                      </span>
                    <span className="dmg-bar-wrap">
                      <span className="dmg-bar" style={{ width: `${(r.damage / max) * 100}%` }}>
                        {r.damage / max >= 0.22 ? fmtC(r.damage) : ""}
                      </span>
                    </span>
                    <span className="dmg-val" title={r.damage.toLocaleString()}>{fmtC(r.damage)}</span>
                    <span className="dmg-dps" title={t("dmgDpsHint")}>{fmtC(r.dps)}/s</span>
                    <span className="dmg-pct">{((r.damage / totalDmg) * 100).toFixed(1)}%</span>
                  </div>
                  {expanded[r.name] && (
                    <div className="dmg-detail">
                      <DamageTimeline data={r.timeline} />
                      {r.skills.length === 0 ? (
                        <div className="dmg-skill-row muted">{t("dmgNoSkills")}</div>
                      ) : (
                        <div className="dmg-skills">
                          <div className="dmg-skill-row header">
                            <span>{t("dmgSkill")}</span>
                            <span>{t("dmgHits")}</span>
                            <span>{t("dmgAvg")}</span>
                            <span>{t("dmgMax")}</span>
                            <span>{t("dmgTotal")}</span>
                          </div>
                          {r.skills.map(sk => (
                            <div key={sk.id} className="dmg-skill-row">
                              <span className="dmg-skill-name">
                                {/* Background bar = this skill's share of row damage. */}
                                <span className="dmg-skill-bar" style={{ width: `${sk.pct}%` }} />
                                <span className="dmg-skill-label">
                                  {sk.id >= 0 ? (
                                    <SkillIcon uniqueName={sk.icon ?? sk.unique_name} apiBase={config.api_base_url} />
                                  ) : (
                                    // Auto-attack icon comes from the row's
                                    // inferred weapon family, not the skill.
                                    (() => {
                                      const kind = autoAttackKind(r.weapon);
                                      // Grayscale recycled skill icons: no color
                                      // distinguishes auto-attack from real skills.
                                      return kind && (
                                        <SkillIcon uniqueName={AUTO_ATTACK_ICON[kind]} apiBase={config.api_base_url} gray />
                                      );
                                    })()
                                  )}
                                  {sk.id < 0
                                    ? t("dmgAutoAttack")
                                    : skillName(sk, lang) || t("dmgSkillN", { id: sk.id })}
                                  {/* Raw skill id stays visible to verify the
                                      resolved name against the skill you used. */}
                                  {sk.id >= 0 && (
                                    <span className="dmg-skill-id" title={sk.unique_name ?? ""}>
                                      #{sk.id}
                                    </span>
                                  )}
                                </span>
                              </span>
                              <span>{sk.hits}×</span>
                              <span>{fmtC(sk.avg)}</span>
                              <span>{fmtC(sk.max_hit)}</span>
                              <span className="dmg-skill-total">
                                {fmtC(sk.total)} <em>{sk.pct.toFixed(0)}%</em>
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Lootlog ────────────────────────────────────────────────────────────────

function LootlogTab({ config, update, sniffStats }: { config: CompanionConfig; update: (key: keyof CompanionConfig, value: unknown) => Promise<void>; sniffStats: SniffStats | null }) {
  const t = useT();
  const { lang } = useLang();
  const [loot, setLoot] = useState<LootRow[]>([]);
  const [debug, setDebug] = useState<DebugLine[]>([]);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const terminalRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const loggedIn = !!config.discord_token;

  // Poll: loot capturado + debug do sniffer (a cada 2s).
  usePoll(async () => {
    try {
      const [rows, lines] = await Promise.all([
        invoke<LootRow[]>("get_captured_loot"),
        invoke<DebugLine[]>("get_sniffer_debug"),
      ]);
      setLoot(rows);
      setDebug(lines);
     } catch { /* sniffer not running */ }
  }, 2000);

  // Auto-scroll to terminal bottom on new content.
  const totalLines = loot.length + debug.length;
  useEffect(() => {
    if (autoScroll && terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [totalLines, autoScroll]);

  const handleDownload = async () => {
    setSaveErr(null);
    try {
      const path = await invoke<string>("save_lootlog_csv");
      setSavedPath(path);
    } catch (e) {
      setSaveErr(String(e));
    }
  };

  const handleClear = async () => {
    await invoke("clear_captured_loot");
    setLoot([]);
  };

  // Auto-submit runs in the Rust worker when an event enters review. Do NOT
  // submit from React: each new loot would overwrite the previous partial log.

  const onTerminalScroll = () => {
    if (!terminalRef.current) return;
    const el = terminalRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    setAutoScroll(atBottom);
  };

  // Lootlog disabled via sidebar toggle.
  if (!config.collect_auto_lootlog) {
    return (
      <div className="ck-panel ck-loot"><CardGlow />
        <div className="card-head">
          <h2>{t("lootlogTitle")}</h2>
        </div>
        <p className="card-desc">{t("lootlogDesc")}</p>
        <div className="empty-area">{t("lootlogOffHint")}</div>
      </div>
    );
  }

  return (
    <>
      <div className="ck-panel ck-loot"><CardGlow />
        <div className="card-head">
          {/* Long description moved to tooltips. */}
          <h2 title={t("lootlogDesc")}>{t("capturedLoot")}</h2>
        </div>

        <div className="loot-toolbar">
          {/* Single toggle button, same pattern as Damage Meter chips. */}
          <button
            className={`dmg-chip${config.auto_lootlog_submit ? " active" : ""}`}
            onClick={() => update("auto_lootlog_submit", !config.auto_lootlog_submit)}
            disabled={!loggedIn}
            title={!loggedIn ? t("connectDiscordForLootlog") : `${t("autoSubmitDesc")}\n\n${t("autoSubmitWhen")}`}
          >
            {t("autoSubmitToggle")}
          </button>
          <button className="btn" onClick={handleDownload} disabled={loot.length === 0}
                  title={t("downloadCsvHint")}>
            {t("downloadCsv")}
          </button>
          <button className="btn" onClick={handleClear} disabled={loot.length === 0}
                  title={t("clearLootHint")}>
            {t("clearLoot")}
          </button>
        </div>

        <div className="terminal" ref={terminalRef} onScroll={onTerminalScroll}>
          {debug.map((l, i) => (
            <div key={`d${i}`} className="terminal-line">
              <span className="t-time">{l.ts}</span>{" "}
              <span className={l.level === "err" ? "t-err-tag" : l.level === "warn" ? "t-warn-tag" : "t-info-tag"}>
                [{l.level === "err" ? "ERR" : l.level === "warn" ? "WARN" : "INFO"}]
              </span>{" "}
              <span className={l.level === "err" ? "t-err" : l.level === "warn" ? "t-warn" : "t-info-msg"}>{l.msg}</span>
            </div>
          ))}
          {loot.map((r, i) => (
            <div key={`l${i}`} className="terminal-line">
              <span className="t-time">{shortTime(r.ts)}</span>{" "}
              <span className="t-loot-tag">[LOOT]</span>{" "}
              <span className="t-player">{r.looted_by}</span>{" "}
              <span className="t-action">{t("lootedBy")}</span>{" "}
              {/* Skip quantity when it is 1 to keep lines short. */}
              {r.quantity > 1 && <span className="t-qty">{r.quantity}× </span>}
              <LootItem row={r} lang={lang} />{" "}
              <span className="t-from">{t("from")}</span>{" "}
              <span className="t-source">{r.looted_from}</span>
            </div>
          ))}
          {/* Download result also appears as a log line. */}
          {savedPath && (
            <div className="terminal-line">
              <span className="t-ok-tag">[OK]</span>{" "}
              <span className="t-ok-msg">{t("savedAt", { path: savedPath })}</span>
            </div>
          )}
          {saveErr && (
            <div className="terminal-line">
              <span className="t-err-tag">[ERR]</span> <span className="t-err">{saveErr}</span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ─── Hero: Route/Tunnel (main cockpit view) ──────────────────────────────────

/// Ad placeholder in the site's visual language. No ad network wired yet.
function AdSlot({ variant = "strip" }: { variant?: "strip" | "side" } = {}) {
  const t = useT();
  // side: 300×250 or 160×600 vertical under the tabs.
  // strip: 728×90 horizontal at the bottom of the tab content.
  return (
    <div className={`ck-ad ck-ad-${variant}`}>
      <span className="ck-ad-tag">{t("ckAd")}</span>
      <span className="ck-ad-ph">{variant === "side" ? "300 × 250" : "728 × 90"}</span>
    </div>
  );
}

/// Route tab connection panel with tunnel operational details. VPS settings
/// (endpoint, keys) are hardcoded in the binary, not exposed in the UI.
function ConnPanel({ config, tunnelStatus }: {
  config: CompanionConfig;
  tunnelStatus: TunnelStatus | null;
}) {
  const t = useT();
  const configured = !!config.tunnel_endpoint;
  const activeEndpoint = config.tunnel_endpoint;
  return (
    <div className="ck-panel ck-conn"><CardGlow />
      <div className="card-head">
        <h2>{t("ckConn")}</h2>
      </div>
      {configured ? (
        <div className="ck-conn-rows">
          <div className="ck-conn-row"><span className="ck-lbl">VPS</span>
            <b className="ck-num" title={activeEndpoint}>{activeEndpoint.split(":")[0]}</b></div>
          <div className="ck-conn-row"><span className="ck-lbl">{t("ckInternetActive")}</span>
            <b>{tunnelStatus?.active_interface ?? "—"}</b></div>
          <div className="ck-conn-row"><span className="ck-lbl">{t("traffic")}</span>
            <b className="ck-num">{tunnelStatus ? `${(tunnelStatus.bytes_sent / 1024).toFixed(0)}K↑ ${(tunnelStatus.bytes_received / 1024).toFixed(0)}K↓` : "—"}</b></div>
          <div className="ck-conn-row"><span className="ck-lbl">{t("ckSplit")}</span>
            <b className="ck-ok">{t("ckSplitOnly")}</b></div>
          <div className="ck-conn-row"><span className="ck-lbl">{t("ckFallback")}</span>
            <b style={{ color: "var(--muted)" }}>{t("ckFallbackCount", { n: tunnelStatus?.failover_count ?? 0 })}</b></div>
          {tunnelStatus?.last_error && <div className="warning-box amber">{tunnelStatus.last_error}</div>}
        </div>
      ) : (
        <div className="empty-inline">{t("tunnelSoonDesc")}</div>
      )}
    </div>
  );
}

/* DamageRail removed with live tabs: the Damage badge shows the live total and
   the full meter is the tab itself. */

// ─── Multi-internet: local connections feeding the same VPS ────────────────

function InternetPathsModal({ status, onClose }: { status: TunnelStatus | null; onClose: () => void }) {
  const t = useT();
  const paths = status?.internet_paths ?? [];
  return (
    <div className="ck-modal-backdrop" onClick={onClose}>
      <div className="ck-modal ck-routes-modal" onClick={e => e.stopPropagation()}>
        <div className="ck-modal-head">
          <h2>{t("ckMultiInternet")}</h2>
          <button className="ck-modal-close" onClick={onClose} aria-label="close">✕</button>
        </div>
        <div className="ck-modal-body">
          <p className="card-desc">{t("ckMultiInternetDesc")}</p>
          {paths.length === 0 ? <div className="empty-area">{t("ckMultiInternetEmpty")}</div> : (
            <div className="ck-internet-list">
              {paths.map(path => (
                <div className={`ck-internet-path${path.active ? " active" : ""}`} key={`${path.name}:${path.local_ip}`}>
                  <span className="ck-route-prio">#{path.priority}</span>
                  <span className={`status-dot ${path.available ? "on" : "off"}`} />
                  <span className="ck-internet-name"><b>{path.name}</b><small>{path.local_ip}</small></span>
                  <span className="ck-internet-lat ck-num">{path.latency_ms != null ? `${path.latency_ms.toFixed(0)} ms` : "—"}</span>
                  <span className={`ck-internet-state ${path.active ? "active" : path.available ? "ready" : "off"}`}>
                    {path.active ? t("ckInternetInUse") : path.available ? t("ckInternetReady") : t("ckInternetUnavailable")}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="ck-internet-foot">
            <span>{t("ckFallbackCount", { n: status?.failover_count ?? 0 })}</span>
            <span>{t("ckMultiInternetFixedIp")}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/// Main Route/Tunnel view. No fabricated numbers — everything comes from the
/// real tunnel_status. Unmeasured metrics (per-leg, jitter) are not shown.
/// When VPS is not configured, renders the same skeleton in "waiting" mode.
function TunnelHero({ config, tunnelStatus, hist, onOpenRoutes }: {
  config: CompanionConfig;
  tunnelStatus: TunnelStatus | null;
  // History is accumulated in App (5s poll) so it survives tab switching.
  hist: { d: number | null; tn: number | null }[];
  onOpenRoutes: () => void;
}) {
  const t = useT();
  const configured = !!config.tunnel_endpoint;
  const [running, setRunning] = useState(false);
  useEffect(() => { setRunning(!!tunnelStatus?.running); }, [tunnelStatus?.running]);

  const direct = tunnelStatus?.direct_latency_ms ?? null;
  const tun = tunnelStatus?.tunnel_latency_ms ?? null;
  const tunnelUp = !!tunnelStatus?.connected && !!tunnelStatus?.using_tunnel;
  const gain = direct != null && tun != null && direct > 0 ? direct - tun : null;

  const activeHost = (config.tunnel_endpoint || "").split(":")[0];

  const toggle = async () => {
    if (!configured) return; // setup is in ConnPanel below
    if (running) { await invoke("tunnel_stop"); setRunning(false); }
    else { await invoke("tunnel_start"); setRunning(true); }
  };

  // Chart polylines scaled to the largest observed value (min 60ms so good
  // connections don't amplify noise).
  const chartMax = Math.max(60, ...hist.flatMap(h => [h.d ?? 0, h.tn ?? 0]));
  const line = (pick: (h: { d: number | null; tn: number | null }) => number | null) =>
    hist.map((h, i) => {
      const v = pick(h);
      return v == null ? null : `${(i / Math.max(1, hist.length - 1)) * 600},${90 - (v / chartMax) * 82}`;
    }).filter(Boolean).join(" ");

  return (
    <div className="ck-panel ck-tun"><CardGlow />
      <div className="card-head">
        <h2>{t("navTunnel")}</h2>
      </div>

      <div className="ck-tun-hero">
        <div>
          <div className="ck-lbl">{t("ckLatVia")}</div>
          <div className="ck-tun-big ck-num">
            <b>{tun != null ? tun.toFixed(0) : "—"}</b><small> ms</small>
          </div>
        </div>
        <div className="ck-tun-col">
          <div className="ck-lbl">{t("ckLatDirect")}</div>
          <div className="ck-tun-v ck-num">{direct != null ? `${direct.toFixed(0)} ms` : "—"}</div>
        </div>
        <div className="ck-tun-col">
          <div className="ck-lbl">{t("ckGainLbl")}</div>
          <div className={`ck-tun-v ck-num ${gain != null && gain > 0 ? "ck-ok" : ""}`}>
            {gain != null ? `−${gain.toFixed(0)} ms · ${Math.round((gain / (direct as number)) * 100)}%` : "—"}
          </div>
        </div>
        <div className="ck-tun-actions">
          <button className={`ck-tun-btn ${running ? "off" : ""}`} onClick={toggle} disabled={!configured}>
            {configured ? (running ? t("turnOffTunnel") : t("turnOnTunnel")) : t("tunnelOff")}
          </button>
          <button className="ck-routes-btn" onClick={onOpenRoutes}>
            {t("ckMultiInternet")}
            {(tunnelStatus?.internet_paths.length ?? 0) > 0 && <span className="ck-routes-count">{tunnelStatus!.internet_paths.length}</span>}
          </button>
        </div>
      </div>

      <div className={`ck-route ${configured ? "" : "waiting"}`}>
        <div className={`ck-hop ${tunnelUp ? "on" : ""}`}>
          <div className="ck-hop-ic"><Icon name="globe" /></div>
          <b>{t("ckYou")}</b>
        </div>
        <div className="ck-leg" />
        <div className={`ck-hop ${tunnelUp ? "on" : ""}`}>
          <div className="ck-hop-ic"><Icon name="route" /></div>
          <b>{t("ckVps")}</b>
          <span>{configured ? activeHost : t("ckVpsWaiting")}</span>
        </div>
        <div className="ck-leg" />
        <div className={`ck-hop ${tunnelUp ? "on" : ""}`}>
          <div className="ck-hop-ic"><Icon name="sword" /></div>
          <b>Albion</b>
        </div>
      </div>

      <div className="ck-chart">
        <div className="ck-chart-legend">
          <span><i className="sw tn" />{t("navTunnel").toLowerCase()}</span>
          <span><i className="sw d" />{t("ckLatDirect")}</span>
          <span className="ck-chart-right">{t("ckLast10")}</span>
        </div>
        {/* height 140 makes the chart the hero body. preserveAspectRatio="none"
            stretches the 90-unit viewBox without changing line() math.
            hasData: without a VPS the history fills with null samples, so length
            alone would show an empty box instead of "measuring". */}
        {hist.length < 2 || !hist.some(h => h.d != null || h.tn != null) ? (
          <div className="empty-inline">{t("ckChartEmpty")}</div>
        ) : (
          <svg viewBox="0 0 600 90" width="100%" height="140" preserveAspectRatio="none">
            <polyline fill="none" stroke="#3a3f4d" strokeWidth="1.5" points={line(h => h.d)} />
            <polyline fill="none" stroke="var(--green)" strokeWidth="2" points={line(h => h.tn)} />
          </svg>
        )}
      </div>

      {/* Operational metrics live in ConnPanel below. The hero shows latency,
          hops, and the chart. */}
    </div>
  );
}

// ─── Config ─────────────────────────────────────────────────────────────────

function ConfigTab({
  config, update, lang, setLang, npcapMissing,
}: {
  config: CompanionConfig;
  update: (key: keyof CompanionConfig, value: unknown) => Promise<void>;
  lang: LangPref;
  setLang: (l: LangPref) => void;
  npcapMissing: boolean;
}) {
  const t = useT();
  return (
    <>
      <div className="card"><CardGlow />
        <h2>{t("language")}</h2>
        <div className="row">
          <label>{t("language")}</label>
          <select
            value={lang}
            onChange={(e) => setLang(e.target.value as LangPref)}
          >
            <option value="auto">{t("langAuto")}</option>
            {(Object.keys(LANG_LABELS) as Lang[]).map(l => (
              <option key={l} value={l}>{LANG_FULL[l]}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="card"><CardGlow />
        <h2>{t("cfgSystem")}</h2>
        <ToggleRow
          label={t("autostart")} on={config.autostart} onChange={(v) => update("autostart", v)}
          hint={config.autostart && npcapMissing ? t("npcapAutostartHint") : undefined}
          hintColor="orange"
        />
        <ToggleRow label={t("minimizeTray")} on={config.minimize_to_tray} onChange={(v) => update("minimize_to_tray", v)} />
      </div>

      <div className="card"><CardGlow />
        <h2>{t("cfgShareTitle")}</h2>
        <p className="card-desc">{t("cfgShareDesc")}</p>
        <ToggleRow label={t("feedAodp")} on={config.feed_aodp} onChange={(v) => update("feed_aodp", v)} />
      </div>

      {/* Spell index calibration removed from UI: it is adjusted per patch
          via config.json, not exposed to users. The field still exists in
          config/set_config. */}

      <div className="card"><CardGlow />
        <h2>{t("aboutTitle")}</h2>
        <div className="row"><label>{t("aboutVersion")}</label><b>0.1.0</b></div>
        <p className="card-desc">{t("aboutDataCredit")}</p>
        <p className="card-desc">{t("aboutNotAffiliated")}</p>
        <div className="row about-links">
          <button className="btn small" onClick={() => invoke("open_url", { url: "https://ziggs.xyz/terms" })}>{t("aboutTerms")}</button>
          <button className="btn small" onClick={() => invoke("open_url", { url: "https://ziggs.xyz/privacy" })}>{t("aboutPrivacy")}</button>
          <button className="btn small" onClick={() => invoke("open_url", { url: "https://ziggs.xyz/cookies" })}>{t("aboutCookies")}</button>
          <button className="btn small" onClick={() => invoke("open_url", { url: "https://ziggs.xyz" })}>{t("aboutSite")}</button>
        </div>
      </div>
    </>
  );
}

// ─── Discord button (top of the sidebar, next to the logo) ────────────────

/// Official Discord mark (simple-icons path, 24×24 viewBox). Do not hand-draw
/// brand logos; replace with the full official asset if edits are needed.
function DiscordIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/>
    </svg>
  );
}

function DiscordButton({ config, onChange }: {
  config: CompanionConfig;
  onChange: () => Promise<void>;
}) {
  const t = useT();
  const [state, setState] = useState<"idle" | "waiting">("idle");
  const loggedIn = !!config.discord_token;

  const login = async () => {
    if (state === "waiting") return;
    setState("waiting");
    try {
      const nonce = await invoke<string>("companion_login");
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));
        try {
          await invoke<AuthPollResult>("companion_poll_auth", { nonce });
          await onChange();
          break;
        } catch { /* 408 = ainda aguardando */ }
      }
    } catch { /* falhou ao abrir o browser */ }
    setState("idle");
  };

  const logout = async () => {
    if (!confirm(t("discordDisconnectConfirm"))) return;
    await invoke("companion_logout");
    await onChange();
  };

  if (loggedIn) {
    return (
      <button
        className="discord-btn connected"
        onClick={logout}
        title={t("discordDisconnectHint")}
      >
        <DiscordIcon />
        <span className="discord-btn-name">{config.discord_username || config.discord_user_id}</span>
      </button>
    );
  }
  return (
    <button
      className={`discord-btn ${state === "waiting" ? "waiting" : ""}`}
      onClick={login}
      disabled={state === "waiting"}
      title={t("connectDiscord")}
    >
      <DiscordIcon />
      <span className="discord-btn-name">{state === "waiting" ? "…" : t("connectDiscord")}</span>
    </button>
  );
}

function ToggleRow({
  label, on, onChange, hint, hintColor,
}: {
  label: string; on: boolean; onChange: (v: boolean) => void;
  hint?: string; hintColor?: "orange" | "warn";
}) {
  return (
    <div className="row toggle-row">
      <label>{label}</label>
      <Toggle on={on} onChange={onChange} />
      {hint && <span className={`row-hint ${hintColor === "orange" ? "orange" : hintColor === "warn" ? "warn" : ""}`}>{hint}</span>}
    </div>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} role="switch" aria-checked={on}>
      <div className="toggle-knob" />
    </div>
  );
}

function Stat({
  label, value, color, small,
}: {
  label: string; value: number | string;
  color?: "green" | "red" | "muted" | "info"; small?: boolean;
}) {
  const colorVar = color ? `var(--${color})` : "var(--text)";
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color: colorVar, fontSize: small ? 14 : 22 }}>{value}</div>
    </div>
  );
}
