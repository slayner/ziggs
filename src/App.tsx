import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getVersion } from "@tauri-apps/api/app";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { RenderImage } from "./components/RenderImage";
import { itemDisplayName, skillDisplayName } from "./catalog";
import { itemRenderCandidates, skillRenderCandidates } from "./render";
import { useWebviewZoom } from "./useWebviewZoom";
import { useLang, useT, LANG_FULL, type LangPref } from "./i18n";

type CompanionConfig = {
  api_base_url: string;
  collect_damage_meter: boolean;
  collect_auto_lootlog: boolean;
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
function time(ts: string | null) { return ts?.slice(11, 16) ?? ""; }

type ConfigUpdate = (key: keyof CompanionConfig, value: unknown) => Promise<void>;

export default function App() {
  useWebviewZoom();
  const t = useT();
  const { lang, pref, setPref } = useLang();
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [stats, setStats] = useState<SniffStats | null>(null);
  const [tab, setTab] = useState<"damage" | "loot">("damage");
  const [version, setVersion] = useState("");
  const [settings, setSettings] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [configError, setConfigError] = useState(false);
  const [catalogs, setCatalogs] = useState<CatalogStatuses | null>(null);
  const settingsOrigin = useRef<HTMLElement | null>(null);
  const refresh = useCallback(async () => setConfig(await invoke<CompanionConfig>("get_config")), []);

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
  usePoll(async () => { try { setStats(await invoke<SniffStats>("get_sniff_stats")); } catch {} }, 2000);
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
    setConfigError(false);
    try {
      await invoke("set_config", { key, value });
      await refresh();
    } catch {
      setConfigError(true);
    }
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
    return <div className="splash"><img className="splash-logo" src="/logo.png" alt="Ziggs" /><div className="splash-text">{t("splashText")}</div></div>;
  }

  return (
    <div className="ck-root">
      <header className="ck-bar" onMouseDown={event => {
        if (event.button === 0 && !(event.target as HTMLElement).closest("button")) {
          ignoreAsyncFailure(getCurrentWindow().startDragging());
        }
      }}>
        <img className="logo" src="/logo.png" alt="Ziggs" />
        <span className="ck-brand">ZIGGS</span>
        <span className="ck-version">v{version}</span>
        <span className="ck-sep" />
        <span className="ck-chip"><span className="ck-lbl">{t("ckPackets")}</span><b className="ck-num">{fmt(stats?.packets_captured ?? 0, lang)}</b></span>
        <span className="ck-winbtns">
          <button className="ck-winbtn" aria-label={t("windowMinimize")} onClick={() => ignoreAsyncFailure(getCurrentWindow().minimize())}>—</button>
          <button className="ck-winbtn" aria-label={t("windowClose")} onClick={() => ignoreAsyncFailure(getCurrentWindow().close())}>×</button>
        </span>
      </header>
      <div className="ck-shell">
        <aside className="ck-side">
          <button className={`ck-side-tab ${tab === "damage" ? "selected" : ""}`} onClick={() => setTab("damage")}>
            <span>{t("navDamage")}</span><b className="ck-ok">{fmt(stats?.damage_total ?? 0, lang)}</b>
          </button>
          <button className={`ck-side-tab ${tab === "loot" ? "selected" : ""}`} onClick={() => setTab("loot")}>
            <span>{t("navLoot")}</span><b className="ck-ok">{stats?.loot_count ?? 0}</b>
          </button>
          <div className="ck-side-foot">
            <b className={stats?.error ? "ck-capture-error" : undefined}>{stats?.error || (stats?.online ? stats.last_map_name || t("statusLoading") : t("albionClosed"))}</b>
            {updateAvailable && <button className="ck-side-update" onClick={() => ignoreAsyncFailure(applyUpdate())} disabled={updating} aria-live="polite">{updating ? t("updateDownloading") : t("updateApply")}</button>}
            <button className="ck-side-gear" onClick={event => openSettings(event.currentTarget)}>{t("navConfig")}</button>
          </div>
        </aside>
        <main className="ck-main"><div className="ck-full">
          {tab === "damage"
            ? <Damage config={config} stats={stats} openSettings={openSettings} />
            : <Loot config={config} openSettings={openSettings} />}
        </div></main>
      </div>
      {settings && <Settings catalogs={catalogs} config={config} configError={configError} pref={pref} setPref={setPref} update={update} close={closeSettings} />}
    </div>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (value: boolean) => void }) {
  return <button className={`toggle ${on ? "on" : ""}`} type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}><span /></button>;
}

function Damage({ config, stats, openSettings }: { config: CompanionConfig; stats: SniffStats | null; openSettings: (origin: HTMLElement) => void }) {
  const t = useT();
  const { lang } = useLang();
  const [rows, setRows] = useState<DamageRow[]>([]);
  const [vsPlayers, setVsPlayers] = useState(false);
  usePoll(async () => {
    if (config.collect_damage_meter) setRows(await invoke<DamageRow[]>("get_damage_meter", { vsPlayers }));
  }, 2000, [config.collect_damage_meter, vsPlayers]);

  if (!config.collect_damage_meter) {
    return <div className="ck-panel ck-dmg"><h2>{t("navDamage")}</h2><div className="empty-area">{t("dmgOffHint")} <button className="btn small" onClick={event => openSettings(event.currentTarget)}>{t("openSettings")}</button></div></div>;
  }

  return <div className="ck-panel ck-dmg">
    <div className="card-head"><h2>{t("navDamage")}</h2><button className={`dmg-chip ${vsPlayers ? "active" : ""}`} onClick={() => setVsPlayers(!vsPlayers)}>{t("dmgVsPlayers")}</button><button className="btn small" onClick={() => ignoreAsyncFailure(invoke("clear_damage_meter").then(() => setRows([])))}>{t("clearLoot")}</button></div>
    {rows.length === 0 ? <div className="empty-area">{t("dmgEmptyHint")}</div> : <div className="dmg-list">{rows.map((row, index) => <div className="dmg-entry" key={row.name}>
      <div className="dmg-row"><span className="dmg-rank">{index + 1}</span><span className="dmg-name">{row.name}</span><span className="dmg-val">{fmt(row.damage, lang)}</span><span className="dmg-dps">{fmt(row.dps, lang)}/s</span></div>
      <div className="dmg-skills">{row.skills.map(skill => {
        const name = skillDisplayName(skill, lang, id => t("dmgSkillN", { id }));
        return <div className="dmg-skill-row" key={skill.id} title={skill.unique_name ?? undefined}>
          <RenderImage apiBaseUrl={config.api_base_url} kind="spell" candidates={skillRenderCandidates(skill)} alt={name} size={32} />
          <span>{name}</span><span>{skill.hits}×</span><span>{fmt(skill.total, lang)}</span>
        </div>;
      })}</div>
    </div>)}</div>}
    <small>{stats?.player_name}</small>
  </div>;
}

function Loot({ config, openSettings }: { config: CompanionConfig; openSettings: (origin: HTMLElement) => void }) {
  const t = useT();
  const { lang } = useLang();
  const [rows, setRows] = useState<LootRow[]>([]);
  const [debug, setDebug] = useState<DebugLine[]>([]);
  usePoll(async () => {
    const [loot, lines] = await Promise.all([invoke<LootRow[]>("get_captured_loot"), invoke<DebugLine[]>("get_sniffer_debug")]);
    setRows(loot);
    setDebug(lines);
  }, 2000);

  if (!config.collect_auto_lootlog) {
    return <div className="ck-panel ck-loot"><h2>{t("navLoot")}</h2><div className="empty-area">{t("lootlogOffHint")} <button className="btn small" onClick={event => openSettings(event.currentTarget)}>{t("openSettings")}</button></div></div>;
  }

  return <div className="ck-panel ck-loot">
    <div className="card-head"><h2>{t("capturedLoot")}</h2><button className="btn" disabled={!rows.length} onClick={() => ignoreAsyncFailure(invoke("save_lootlog_csv"))}>{t("downloadCsv")}</button><button className="btn" disabled={!rows.length} onClick={() => ignoreAsyncFailure(invoke("clear_captured_loot").then(() => setRows([])))}>{t("clearLoot")}</button></div>
    <div className="terminal">
      {debug.filter(line => line.level !== "info").map((line, index) => <div className="terminal-line" key={`d${index}`}>[{line.level}] {line.msg}</div>)}
      {rows.map((row, index) => {
        const name = itemDisplayName(row, lang);
        return <div className="terminal-line terminal-loot-line" key={`l${index}`} title={row.item_id}>
          <RenderImage apiBaseUrl={config.api_base_url} kind="item" candidates={itemRenderCandidates(row)} alt={name} size={32} />
          <span className="t-time">{time(row.ts)}</span> <span className="t-loot-tag">[LOOT]</span> {row.looted_by} {t("lootedBy")} {row.quantity > 1 ? `${row.quantity}× ` : ""}{name} {t("from")} {row.looted_from}
        </div>;
      })}
    </div>
  </div>;
}

function Settings({ catalogs, config, configError, pref, setPref, update, close }: {
  catalogs: CatalogStatuses | null;
  config: CompanionConfig;
  configError: boolean;
  pref: LangPref;
  setPref: (value: LangPref) => void;
  update: ConfigUpdate;
  close: () => void;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
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

  return <div className="ck-modal-backdrop" onClick={close}><div ref={dialogRef} className="ck-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={event => event.stopPropagation()} onKeyDown={onKeyDown}>
    <div className="ck-modal-head"><h2 id="settings-title">{t("navConfig")}</h2><button ref={closeRef} aria-label={t("closeSettings")} onClick={close}>×</button></div>
    <div className="ck-modal-body">
      {configError && <p className="config-error" role="alert">{t("saveConfigError")}</p>}
      <section className="settings-section"><h3>{t("capture")}</h3>
        <label>{t("damageCapture")}<Toggle on={config.collect_damage_meter} onChange={value => void update("collect_damage_meter", value)} /></label>
        <label>{t("lootlogCapture")}<Toggle on={config.collect_auto_lootlog} onChange={value => void update("collect_auto_lootlog", value)} /></label>
      </section>
      {catalogs && <section className="settings-section catalog-status"><h3>{t("catalogs")}</h3>
        <CatalogHealth label={t("spellCatalog")} status={catalogs.spells} />
        <CatalogHealth label={t("itemCatalog")} status={catalogs.items} />
      </section>}
      <label>{t("language")}<select value={pref} onChange={event => setPref(event.target.value as LangPref)}><option value="auto">{t("langAuto")}</option>{Object.entries(LANG_FULL).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>{t("autostart")}<Toggle on={config.autostart} onChange={value => void update("autostart", value)} /></label>
      <label>{t("minimizeTray")}<Toggle on={config.minimize_to_tray} onChange={value => void update("minimize_to_tray", value)} /></label>
      <p className="zoom-hint">{t("zoomShortcut")}</p>
    </div>
  </div></div>;
}

function CatalogHealth({ label, status }: { label: string; status: CatalogStatus }) {
  const t = useT();
  const detail = status.state === "ready"
    ? t("catalogReady", { count: status.count, source: status.source })
    : status.state === "loading"
      ? t("catalogLoading", { count: status.count, source: status.source })
      : t("catalogDegraded", { count: status.count, source: status.source });
  return <div className={`catalog-health catalog-${status.state}`}>
    <span>{label}</span><span>{detail}</span>
  </div>;
}
