import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getVersion } from "@tauri-apps/api/app";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { IconBackpack, IconChartBar, IconCheck, IconChevronRight, IconCopy, IconDownload, IconMinus, IconSettings, IconTrash, IconX } from "@tabler/icons-react";
import { RenderImage } from "./components/RenderImage";
import { itemDisplayName, skillDisplayName } from "./catalog";
import { meterText, partyRows, shareOf, shortNumber, skillIconCandidates, WEAPON_FAMILIES } from "./damageMeter";
import { preloadSkillIcon } from "./preload";
import { itemRenderCandidates } from "./render";
import { useWebviewZoom } from "./useWebviewZoom";
import { useLang, useT, LANG_FULL, type LangPref } from "./i18n";

type CaptureMethod = "npcap" | "windivert";
type CaptureIssue = "npcap_missing" | "npcap_unavailable" | "npcap_no_packets" | "windivert_needs_admin";
type ConfigProblem = "save" | "elevation" | null;
type NpcapStatus = { installed: boolean; version: string | null };
type CompanionConfig = {
  api_base_url: string;
  collect_damage_meter: boolean;
  collect_auto_lootlog: boolean;
  capture_method: CaptureMethod;
  autostart: boolean;
  minimize_to_tray: boolean;
  spell_index_offset: number;
};
type SniffStats = {
  running: boolean;
  online: boolean;
  packets_captured: number;
  loot_count: number;
  damage_total: number;
  player_name: string;
  party_members: string[];
  last_map_name: string;
  error: string | null;
  capture_issue?: CaptureIssue | null;
};
type LootRow = {
  ts: string | null;
  item_id: string;
  item_name: string;
  item_name_pt: string;
  item_name_es: string;
  quantity: number;
  looted_by: string;
  looted_from: string;
};
type SkillRow = {
  id: number;
  name: string | null;
  name_pt: string | null;
  name_es: string | null;
  unique_name: string | null;
  icon: string | null;
  hits: number;
  total: number;
  avg: number;
  max_hit: number;
  pct: number;
};
type DamageRow = {
  name: string;
  weapon: string | null;
  damage: number;
  dps: number;
  skills: SkillRow[];
  timeline: number[];
};
// Lives above the tabs, so going to the Lootlog and back keeps the meter as it was left.
type MeterView = { vsPlayers: boolean; partyOnly: boolean; open: ReadonlySet<string> };
type DebugLine = { ts: string; level: string; msg: string };
type CatalogStatus = { state: "loading" | "ready" | "degraded"; source: "fallback" | "cache" | "backend"; count: number; error: string | null };
type CatalogStatuses = { spells: CatalogStatus; items: CatalogStatus };

export async function executePoll(poll: () => void | Promise<void>): Promise<void> {
  try {
    await poll();
  } catch {}
}

export function ignoreAsyncFailure(operation: Promise<unknown>): void {
  void operation.catch(() => {});
}

function usePoll(fn: () => void | Promise<void>, ms: number, deps: unknown[] = []) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (!alive) return;
      void executePoll(latest.current);
    };
    tick();
    const id = setInterval(tick, ms);
    return () => { alive = false; clearInterval(id); };
  }, [ms, ...deps]);
}

function fmt(n: number, lang: string) { return Math.round(n).toLocaleString(lang); }
function compact(n: number, lang: string) { return new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(n); }
function time(ts: string | null) { return ts?.slice(11, 16) ?? ""; }

type ConfigUpdate = (key: keyof CompanionConfig, value: unknown) => Promise<void>;

const METHOD_NAME: Record<CaptureMethod, string> = { npcap: "Npcap", windivert: "WinDivert" };

export default function App() {
  useWebviewZoom();
  const t = useT();
  const { lang, pref, setPref } = useLang();
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [stats, setStats] = useState<SniffStats | null>(null);
  const [tab, setTab] = useState<"damage" | "loot">("damage");
  const [meterView, setMeterView] = useState<MeterView>({ vsPlayers: false, partyOnly: false, open: new Set() });
  const [version, setVersion] = useState("");
  const [settings, setSettings] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [configProblem, setConfigProblem] = useState<ConfigProblem>(null);
  const [catalogs, setCatalogs] = useState<CatalogStatuses | null>(null);
  const settingsOrigin = useRef<HTMLElement | null>(null);
  const refresh = useCallback(async () => setConfig(await invoke<CompanionConfig>("get_config")), []);
  const loadStats = useCallback(async () => { try { setStats(await invoke<SniffStats>("get_sniff_stats")); } catch {} }, []);

  useEffect(() => {
    ignoreAsyncFailure(refresh());
    ignoreAsyncFailure(getVersion().then(setVersion));
  }, [refresh]);
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    ignoreAsyncFailure(listen<string>("update-status", event => {
      setUpdateAvailable(event.payload === "available");
      setUpdating(event.payload === "downloading");
    }).then(stop => { unlisten = stop; }));
    return () => unlisten?.();
  }, []);
  usePoll(loadStats, 2000);
  usePoll(async () => {
    try {
      const status = await invoke<CatalogStatuses>("get_catalog_status");
      if (status?.spells && status?.items) setCatalogs(status);
    } catch {}
  }, 5000);

  const applyUpdate = async () => {
    if (updating) return;
    setUpdating(true);
    try { await invoke("check_and_apply_update"); } catch { setUpdating(false); }
  };
  const update: ConfigUpdate = async (key, value) => {
    setConfigProblem(null);
    try {
      await invoke("set_config", { key, value });
      await refresh();
      // The capture restarted: the old method's problem should not stay on screen.
      if (key === "capture_method") await loadStats();
    } catch (error) {
      setConfigProblem(error === "elevation_declined" ? "elevation" : "save");
    }
  };
  const reopenAsAdmin = async () => {
    setConfigProblem(null);
    try { await invoke("relaunch_as_admin"); } catch { setConfigProblem("elevation"); }
  };
  const openSettings = (origin: HTMLElement) => {
    settingsOrigin.current = origin;
    setSettings(true);
  };
  const closeSettings = () => {
    setSettings(false);
  };

  const wasSettingsOpen = useRef(false);
  useEffect(() => {
    if (wasSettingsOpen.current && !settings) settingsOrigin.current?.focus();
    wasSettingsOpen.current = settings;
  }, [settings]);

  if (!config) {
    return <div className="cp-splash"><img src="/logo.png" alt="Ziggs" /><span>{t("splashText")}</span></div>;
  }

  const issue = stats?.capture_issue ?? null;
  const captureBlocked = issue === "npcap_missing" || issue === "npcap_unavailable" || issue === "windivert_needs_admin";
  const captureState = stats?.error || captureBlocked ? "error" : stats?.online ? "online" : "closed";
  const captureLabel = issue === "npcap_missing" ? t("npcapMissingShort")
    : issue === "npcap_unavailable" ? t("npcapUnavailableShort")
    : issue === "windivert_needs_admin" ? t("windivertNeedsAdminShort")
    : stats?.error ? t("captureFailed")
    : stats?.online ? stats.last_map_name || t("statusLoading")
    : issue === "npcap_no_packets" ? t("noPacketsShort")
    : t("albionClosed");

  return (
    <div className="cp-root">
      <header className="cp-bar" onMouseDown={event => {
        if (event.button === 0 && !(event.target as HTMLElement).closest("button")) {
          ignoreAsyncFailure(getCurrentWindow().startDragging());
        }
      }}>
        <img className="cp-logo" src="/logo.png" alt="Ziggs" />
        <span className="cp-brand">Ziggs Companion</span>
        <span className="cp-version">v{version}</span>
        <span className={`cp-capture is-${captureState}`}><i aria-hidden="true" />{captureLabel}</span>
        <span className="cp-packets">{METHOD_NAME[config.capture_method]} · <b>{fmt(stats?.packets_captured ?? 0, lang)}</b> {t("ckPackets")}</span>
        <span className="cp-winbtns">
          <button className="cp-winbtn" aria-label={t("windowMinimize")} onClick={() => ignoreAsyncFailure(getCurrentWindow().minimize())}><IconMinus size={16} aria-hidden="true" /></button>
          <button className="cp-winbtn is-close" aria-label={t("windowClose")} onClick={() => ignoreAsyncFailure(getCurrentWindow().close())}><IconX size={16} aria-hidden="true" /></button>
        </span>
      </header>
      {stats?.error && !issue && <p className="cp-capture-error" role="alert">{stats.error}</p>}
      <div className="cp-shell">
        <aside className="cp-side">
          <nav className="cp-nav" aria-label={t("navMain")}>
            <button className={`cp-nav-item ${tab === "damage" ? "is-active" : ""}`} aria-current={tab === "damage" ? "page" : undefined} onClick={() => setTab("damage")}>
              <IconChartBar size={18} stroke={1.75} aria-hidden="true" /><span>{t("navDamage")}</span><b>{compact(stats?.damage_total ?? 0, lang)}</b>
            </button>
            <button className={`cp-nav-item ${tab === "loot" ? "is-active" : ""}`} aria-current={tab === "loot" ? "page" : undefined} onClick={() => setTab("loot")}>
              <IconBackpack size={18} stroke={1.75} aria-hidden="true" /><span>{t("navLoot")}</span><b>{fmt(stats?.loot_count ?? 0, lang)}</b>
            </button>
          </nav>
          <div className="cp-side-foot">
            {updateAvailable && <button className="ds-btn ds-btn-primary cp-update" onClick={() => ignoreAsyncFailure(applyUpdate())} disabled={updating} aria-live="polite">{updating ? t("updateDownloading") : t("updateApply")}</button>}
            <button className="cp-nav-item" onClick={event => openSettings(event.currentTarget)}>
              <IconSettings size={18} stroke={1.75} aria-hidden="true" /><span>{t("navConfig")}</span>
            </button>
          </div>
        </aside>
        <main className="cp-main">
          {issue && <CaptureIssuePanel issue={issue} detail={stats?.error ?? null} configProblem={configProblem} switchMethod={method => void update("capture_method", method)} reopenAsAdmin={() => void reopenAsAdmin()} />}
          {tab === "damage"
            ? <Damage captureBlocked={captureBlocked} config={config} stats={stats} openSettings={openSettings} view={meterView} setView={setMeterView} />
            : <Loot captureBlocked={captureBlocked} config={config} openSettings={openSettings} />}
        </main>
      </div>
      {settings && <Settings catalogs={catalogs} config={config} configProblem={configProblem} pref={pref} setPref={setPref} update={update} close={closeSettings} />}
    </div>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (value: boolean) => void }) {
  return <button className="cp-switch" type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}><span /></button>;
}

/* Npcap can't be bundled, so the user installs it. Nothing here needs an app restart:
   the capture keeps retrying and this panel goes away once it starts. */
function CaptureIssuePanel({ issue, detail, configProblem, switchMethod, reopenAsAdmin }: {
  issue: CaptureIssue;
  detail: string | null;
  configProblem: ConfigProblem;
  switchMethod: (method: CaptureMethod) => void;
  reopenAsAdmin: () => void;
}) {
  const t = useT();
  const problem = <ConfigProblemNotice problem={configProblem} />;

  if (issue === "npcap_no_packets") {
    return <>
      <section className="cp-issue-row" role="status">
        <p>{t("noPacketsText")}</p>
        <button className="ds-btn" onClick={() => switchMethod("windivert")}>{t("useWinDivert")}</button>
      </section>
      {problem}
    </>;
  }

  if (issue === "windivert_needs_admin") {
    return <section className="cp-guide" aria-labelledby="capture-guide-title">
      <h2 id="capture-guide-title">{t("needsAdminTitle")}</h2>
      <p>{t("needsAdminIntro")}</p>
      <div className="cp-guide-actions">
        <button className="ds-btn ds-btn-primary" onClick={reopenAsAdmin}>{t("openAsAdmin")}</button>
        <button className="ds-btn" onClick={() => switchMethod("npcap")}>{t("useNpcap")}</button>
      </div>
      {problem}
    </section>;
  }

  const missing = issue === "npcap_missing";
  const download = <button className="ds-btn ds-btn-primary" onClick={() => ignoreAsyncFailure(invoke("open_npcap_download"))}><IconDownload size={15} aria-hidden="true" /> {t("guideDownload")}</button>;
  return <section className="cp-guide" aria-labelledby="capture-guide-title">
    <h2 id="capture-guide-title">{t(missing ? "guideMissingTitle" : "guideUnavailableTitle")}</h2>
    <p>{t(missing ? "guideMissingIntro" : "guideUnavailableIntro")}</p>
    {!missing && detail && <p className="cp-guide-detail">{detail}</p>}
    {missing
      ? <ol className="cp-guide-steps">
        <li><span>{t("guideStep1")}</span>{download}</li>
        <li><span>{t("guideStep2")}</span></li>
        <li><span>{t("guideStep3")}</span></li>
      </ol>
      : <div>{download}</div>}
    <div className="cp-guide-foot">
      <span>{t("guideAlternative")}</span>
      <button className="ds-btn" onClick={() => switchMethod("windivert")}>{t("useWinDivert")}</button>
    </div>
    {problem}
  </section>;
}

function ConfigProblemNotice({ problem }: { problem: ConfigProblem }) {
  const t = useT();
  if (!problem) return null;
  return <p className="cp-notice is-bad" role="alert">{t(problem === "elevation" ? "elevationDeclined" : "saveConfigError")}</p>;
}

function CaptureOff({ title, hint, openSettings }: { title: string; hint: string; openSettings: (origin: HTMLElement) => void }) {
  const t = useT();
  return <>
    <div className="cp-head"><h1>{title}</h1></div>
    <div className="cp-empty"><p>{hint}</p><button className="ds-btn" onClick={event => openSettings(event.currentTarget)}>{t("openSettings")}</button></div>
  </>;
}

function Damage({ captureBlocked, config, stats, openSettings, view, setView }: {
  captureBlocked: boolean;
  config: CompanionConfig;
  stats: SniffStats | null;
  openSettings: (origin: HTMLElement) => void;
  view: MeterView;
  setView: (update: (current: MeterView) => MeterView) => void;
}) {
  const t = useT();
  const { lang } = useLang();
  const { vsPlayers, partyOnly, open } = view;
  const [rows, setRows] = useState<DamageRow[]>([]);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const copyReset = useRef<number | undefined>(undefined);
  usePoll(async () => {
    if (config.collect_damage_meter) setRows(await invoke<DamageRow[]>("get_damage_meter", { vsPlayers }));
  }, 2000, [config.collect_damage_meter, vsPlayers]);
  useEffect(() => () => window.clearTimeout(copyReset.current), []);
  useEffect(() => {
    for (const row of rows) {
      for (const skill of row.skills) preloadSkillIcon(config.api_base_url, skillIconCandidates(skill, row.weapon));
    }
  }, [config.api_base_url, rows]);

  if (!config.collect_damage_meter) {
    return <CaptureOff title={t("navDamage")} hint={t("dmgOffHint")} openSettings={openSettings} />;
  }

  // Bars, shares and the copied text all follow what is on screen.
  const visible = partyOnly ? partyRows(rows, stats?.player_name, stats?.party_members) : rows;
  const top = Math.max(...visible.map(row => row.damage), 1);
  const total = visible.reduce((sum, row) => sum + row.damage, 0);
  const toggle = (name: string) => setView(current => {
    const next = new Set(current.open);
    if (!next.delete(name)) next.add(name);
    return { ...current, open: next };
  });
  const copyMeter = async () => {
    window.clearTimeout(copyReset.current);
    try {
      await navigator.clipboard.writeText(meterText(visible));
      setCopy("copied");
      copyReset.current = window.setTimeout(() => setCopy("idle"), 1500);
    } catch {
      setCopy("failed");
    }
  };
  const clearMeter = async () => {
    await invoke("clear_damage_meter");
    setRows([]);
    setView(current => ({ ...current, open: new Set() }));
  };

  return <>
    <div className="cp-head">
      <h1>{t("navDamage")}</h1>
      {visible.length > 0 && <span className="cp-meta">{t("dmgTotal")} <b>{fmt(total, lang)}</b></span>}
      <div className="cp-actions">
        <button className={`ds-btn cp-filter ${partyOnly ? "is-on" : ""}`} aria-pressed={partyOnly} title={t("dmgPartyHint")} onClick={() => setView(current => ({ ...current, partyOnly: !current.partyOnly }))}>{t("dmgPartyOnly")}</button>
        <button className={`ds-btn cp-filter ${vsPlayers ? "is-on" : ""}`} aria-pressed={vsPlayers} onClick={() => setView(current => ({ ...current, vsPlayers: !current.vsPlayers }))}>{t("dmgVsPlayers")}</button>
        <button className="ds-btn" disabled={!visible.length} onClick={() => ignoreAsyncFailure(copyMeter())}>
          {copy === "copied" ? <IconCheck size={15} aria-hidden="true" /> : <IconCopy size={15} aria-hidden="true" />} {copy === "copied" ? t("dmgCopied") : t("dmgCopy")}
        </button>
        <button className="ds-btn" disabled={!rows.length} onClick={() => ignoreAsyncFailure(clearMeter())}><IconTrash size={15} aria-hidden="true" /> {t("clearLoot")}</button>
      </div>
    </div>
    {copy === "failed" && <p className="cp-notice is-bad" role="alert">{t("dmgCopyFailed")}</p>}
    {visible.length === 0
      ? !captureBlocked && <div className="cp-empty"><p>{rows.length > 0 ? t("dmgPartyEmptyHint") : t("dmgEmptyHint")}</p></div>
      : <section className="cp-panel cp-dmg">
        <div className="cp-scroll cp-dmg-scroll">
          <div className="cp-dmg-grid cp-dmg-head" aria-hidden="true"><span /><span>#</span><span>{t("colPlayer")}</span><span>{t("colDps")}</span><span>{t("colDamage")}</span><span>%</span></div>
          {visible.map((row, index) => <DamagePlayer
            key={row.name}
            apiBaseUrl={config.api_base_url}
            open={open.has(row.name)}
            own={row.name === stats?.player_name}
            rank={index + 1}
            row={row}
            toggle={() => toggle(row.name)}
            top={top}
            total={total}
          />)}
        </div>
      </section>}
  </>;
}

function DamagePlayer({ apiBaseUrl, open, own, rank, row, toggle, top, total }: {
  apiBaseUrl: string;
  open: boolean;
  own: boolean;
  rank: number;
  row: DamageRow;
  toggle: () => void;
  top: number;
  total: number;
}) {
  const t = useT();
  const { lang } = useLang();
  const detailId = useId();
  return <div className={`cp-dmg-player ${row.weapon && WEAPON_FAMILIES.has(row.weapon) ? `w-${row.weapon}` : ""}`}>
    <button className="cp-dmg-grid cp-dmg-row" aria-expanded={open} aria-controls={open ? detailId : undefined} onClick={toggle}>
      <span className="cp-dmg-bar" aria-hidden="true" style={{ width: `${(row.damage / top) * 100}%` }} />
      <IconChevronRight className="cp-dmg-caret" size={14} aria-hidden="true" />
      <span className="cp-num">{rank}</span>
      <span className="cp-dmg-name">
        <span className="cp-truncate">{row.name}</span>
        {own && <span className="ds-badge ds-badge-neutral">{t("you")}</span>}
      </span>
      <span className="cp-num" title={fmt(row.dps, lang)}>{shortNumber(row.dps)}</span>
      <span className="cp-num cp-dmg-total" title={fmt(row.damage, lang)}>{shortNumber(row.damage)}</span>
      <span className="cp-num">{shareOf(row.damage, total)}</span>
    </button>
    {open && <div id={detailId} className="cp-dmg-detail">
      {row.skills.length === 0
        ? <p className="cp-panel-empty">{t("dmgNoSkills")}</p>
        : <SkillTable apiBaseUrl={apiBaseUrl} row={row} />}
    </div>}
  </div>;
}

function SkillTable({ apiBaseUrl, row }: { apiBaseUrl: string; row: DamageRow }) {
  const t = useT();
  const { lang } = useLang();
  const topSkill = Math.max(...row.skills.map(skill => skill.total), 1);
  return <div role="table" aria-label={row.name}>
    <div className="cp-skill-row cp-skill-head" role="row">
      <span role="columnheader" className="cp-skill-name">{t("colSkill")}</span>
      <span role="columnheader">{t("colHits")}</span>
      <span role="columnheader">{t("colAvg")}</span>
      <span role="columnheader">{t("colMax")}</span>
      <span role="columnheader">{t("colDamage")}</span>
      <span role="columnheader">%</span>
    </div>
    {row.skills.map(skill => {
      const auto = skill.id < 0;
      const name = auto ? t("dmgAutoAttack") : skillDisplayName(skill, lang, id => t("dmgSkillN", { id }));
      return <div className={`cp-skill-row ${auto ? "is-auto" : ""}`} key={skill.id} role="row" title={skill.unique_name ?? undefined}>
        <span className="cp-skill-bar" aria-hidden="true" style={{ width: `${(skill.total / topSkill) * 100}%` }} />
        <span className="cp-skill-name" role="cell">
          <RenderImage apiBaseUrl={apiBaseUrl} kind="spell" candidates={skillIconCandidates(skill, row.weapon)} alt={name} size={24} />
          <span className="cp-truncate">{name}</span>
        </span>
        <span className="cp-num" role="cell">{fmt(skill.hits, lang)}</span>
        <span className="cp-num" role="cell" title={fmt(skill.avg, lang)}>{shortNumber(skill.avg)}</span>
        <span className="cp-num" role="cell" title={fmt(skill.max_hit, lang)}>{shortNumber(skill.max_hit)}</span>
        <span className="cp-num cp-dmg-total" role="cell" title={fmt(skill.total, lang)}>{shortNumber(skill.total)}</span>
        <span className="cp-num" role="cell">{skill.pct.toFixed(1)}</span>
      </div>;
    })}
  </div>;
}

function Loot({ captureBlocked, config, openSettings }: { captureBlocked: boolean; config: CompanionConfig; openSettings: (origin: HTMLElement) => void }) {
  const t = useT();
  const { lang } = useLang();
  const [rows, setRows] = useState<LootRow[]>([]);
  const [debug, setDebug] = useState<DebugLine[]>([]);
  const [saved, setSaved] = useState<{ ok: boolean; message: string } | null>(null);
  usePoll(async () => {
    const [loot, lines] = await Promise.all([invoke<LootRow[]>("get_captured_loot"), invoke<DebugLine[]>("get_sniffer_debug")]);
    setRows(loot);
    setDebug(lines);
  }, 2000);

  if (!config.collect_auto_lootlog) {
    return <CaptureOff title={t("navLoot")} hint={t("lootlogOffHint")} openSettings={openSettings} />;
  }

  const saveCsv = async () => {
    try {
      const path = await invoke<string>("save_lootlog_csv");
      setSaved({ ok: true, message: t("csvSaved", { path }) });
    } catch {
      setSaved({ ok: false, message: t("csvSaveFailed") });
    }
  };
  const warnings = debug.filter(line => line.level !== "info");

  return <>
    <div className="cp-head">
      <div className="cp-head-text">
        <h1>{t("navLoot")}</h1>
        <p>{t("lootCsvHint")}</p>
      </div>
      <div className="cp-actions">
        <button className="ds-btn ds-btn-primary" disabled={!rows.length} onClick={() => ignoreAsyncFailure(saveCsv())}><IconDownload size={15} aria-hidden="true" /> {t("downloadCsv")}</button>
        <button className="ds-btn" disabled={!rows.length} onClick={() => ignoreAsyncFailure(invoke("clear_captured_loot").then(() => { setRows([]); setSaved(null); }))}><IconTrash size={15} aria-hidden="true" /> {t("clearLoot")}</button>
      </div>
    </div>
    {saved && <p className={`cp-notice ${saved.ok ? "is-ok" : "is-bad"}`} role={saved.ok ? "status" : "alert"}>{saved.message}</p>}
    {warnings.length > 0 && <div className="cp-warnings">{warnings.map((line, index) => <p key={index} className={`is-${line.level}`}>{line.msg}</p>)}</div>}
    {rows.length === 0
      ? !captureBlocked && <div className="cp-empty"><p>{t("lootEmptyHint")}</p></div>
      : <section className="cp-panel cp-scroll">
        <table className="cp-table">
          <thead><tr><th className="cp-col-time">{t("colTime")}</th><th>{t("colItem")}</th><th className="cp-col-qty">{t("colQty")}</th><th className="cp-col-who">{t("colLootedBy")}</th><th className="cp-col-who">{t("colLootedFrom")}</th></tr></thead>
          <tbody>
            {rows.map((row, index) => ({ row, index })).reverse().map(({ row, index }) => {
              const name = itemDisplayName(row, lang);
              return <tr key={index} title={row.item_id}>
                <td className="cp-num cp-dim">{time(row.ts)}</td>
                <td><span className="cp-item"><RenderImage apiBaseUrl={config.api_base_url} kind="item" candidates={itemRenderCandidates(row)} alt={name} size={32} /><span className="cp-truncate">{name}</span></span></td>
                <td className="cp-num">{row.quantity}</td>
                <td><span className="cp-truncate">{row.looted_by}</span></td>
                <td><span className="cp-truncate cp-dim">{row.looted_from}</span></td>
              </tr>;
            })}
          </tbody>
        </table>
      </section>}
  </>;
}

function Settings({ catalogs, config, configProblem, pref, setPref, update, close }: {
  catalogs: CatalogStatuses | null;
  config: CompanionConfig;
  configProblem: ConfigProblem;
  pref: LangPref;
  setPref: (value: LangPref) => void;
  update: ConfigUpdate;
  close: () => void;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [npcap, setNpcap] = useState<NpcapStatus | null>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);
  usePoll(async () => {
    const status = await invoke<NpcapStatus>("get_npcap_status");
    if (typeof status?.installed === "boolean") setNpcap(status);
  }, 5000);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return <div className="cp-modal-backdrop" onClick={close}><div ref={dialogRef} className="cp-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={event => event.stopPropagation()} onKeyDown={onKeyDown}>
    <div className="cp-modal-head"><h2 id="settings-title">{t("navConfig")}</h2><button ref={closeRef} className="cp-winbtn" aria-label={t("closeSettings")} onClick={close}><IconX size={16} aria-hidden="true" /></button></div>
    <div className="cp-modal-body">
      <ConfigProblemNotice problem={configProblem} />
      <section className="cp-settings-section"><h3>{t("capture")}</h3>
        <label>{t("damageCapture")}<Toggle on={config.collect_damage_meter} onChange={value => void update("collect_damage_meter", value)} /></label>
        <label>{t("lootlogCapture")}<Toggle on={config.collect_auto_lootlog} onChange={value => void update("collect_auto_lootlog", value)} /></label>
      </section>
      <section className="cp-settings-section"><h3 id="capture-method-title">{t("captureMethod")}</h3>
        <div role="radiogroup" aria-labelledby="capture-method-title">
          <MethodOption method="npcap" hint={t("methodNpcapHint")} current={config.capture_method} update={update}
            status={npcap && (npcap.installed
              ? <span className="cp-method-status is-ok">{t("npcapInstalled")}{npcap.version && ` · ${npcap.version}`}</span>
              : <span className="cp-method-status is-bad">{t("npcapNotFound")}</span>)} />
          <MethodOption method="windivert" hint={t("methodWinDivertHint")} current={config.capture_method} update={update}
            status={<span className="cp-method-status">{t("windivertBundled")}</span>} />
        </div>
      </section>
      <section className="cp-settings-section"><h3>{t("general")}</h3>
        <label>{t("language")}<select className="ds-input" value={pref} onChange={event => setPref(event.target.value as LangPref)}><option value="auto">{t("langAuto")}</option>{Object.entries(LANG_FULL).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
        <label>{t("autostart")}<Toggle on={config.autostart} onChange={value => void update("autostart", value)} /></label>
        <label>{t("minimizeTray")}<Toggle on={config.minimize_to_tray} onChange={value => void update("minimize_to_tray", value)} /></label>
        <p className="cp-settings-hint">{t("zoomShortcut")}</p>
      </section>
      {catalogs && <section className="cp-settings-section"><h3>{t("catalogs")}</h3>
        <CatalogHealth label={t("spellCatalog")} status={catalogs.spells} />
        <CatalogHealth label={t("itemCatalog")} status={catalogs.items} />
      </section>}
    </div>
  </div></div>;
}

function MethodOption({ method, hint, current, status, update }: { method: CaptureMethod; hint: string; current: CaptureMethod; status: ReactNode; update: ConfigUpdate }) {
  return <label className="cp-method">
    <input type="radio" name="capture-method" checked={current === method} onChange={() => void update("capture_method", method)} />
    <span className="cp-method-text"><b>{METHOD_NAME[method]}</b><small>{hint}</small></span>
    {status}
  </label>;
}

function CatalogHealth({ label, status }: { label: string; status: CatalogStatus }) {
  const t = useT();
  const detail = status.state === "ready"
    ? t("catalogReady", { count: status.count, source: status.source })
    : status.state === "loading"
      ? t("catalogLoading", { count: status.count, source: status.source })
      : t("catalogDegraded", { count: status.count, source: status.source });
  return <div className={`cp-catalog is-${status.state}`}>
    <span>{label}</span><span>{detail}</span>
  </div>;
}
