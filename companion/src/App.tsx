import { useEffect, useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useT, useLang, LANG_LABELS, LANG_FULL, type Lang, type LangPref } from "./i18n";

// Espelha o CompanionConfig do Rust (src-tauri/src/config.rs). Nome do
// personagem, região e URL do backend não estão aqui: são detectados
// automaticamente / hardcoded no binário. battles e prices são sempre ligados
// (razão de ser do companion) — sem toggle.
type CompanionConfig = {
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
  lootlog_guild_id: string | null;
  auto_lootlog_submit: boolean;
  install_id: string;
  spell_index_offset: number;
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
};

type LootRow = {
  ts: string | null;
  item_id: string;
  item_name: string;
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

type ActiveEvent = {
  event_id: number;
  title: string | null;
  scheduled_at: string | null;
};

type LootlogIngestOut = {
  id: number;
  row_count: number;
  silver_total: number;
  is_update: boolean;
};

// Status do Albion para o card da sidebar.
type AlbionStatus =
  | { kind: "ok" }
  | { kind: "closed" }
  | { kind: "sniff_error"; msg: string };

type SkillRow = {
  id: number; name: string | null; unique_name: string | null;
  hits: number; total: number; avg: number; max_hit: number; pct: number;
};
type DamageRow = { name: string; damage: number; dps: number; skills: SkillRow[]; timeline: number[] };

type Tab = "lootlog" | "damage" | "tunnel" | "config";

export default function App() {
  const t = useT();
  const { pref, setPref } = useLang();
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [tab, setTab] = useState<Tab>("lootlog");
  const [sniffStats, setSniffStats] = useState<SniffStats | null>(null);
  const [tunnelStatus, setTunnelStatus] = useState<TunnelStatus | null>(null);
  const [updateStatus, setUpdateStatus] = useState<"downloading" | "installed" | null>(null);

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
    return () => { unlisten?.(); unlistenUpd?.(); };
  }, [refreshConfig]);

  const updateConfig = async (key: keyof CompanionConfig, value: unknown) => {
    await invoke("set_config", { key, value: value as never });
    await refreshConfig();
  };

  // Poll: sniffer stats + tunnel status. Scanner/stats de coleta não são
  // exibidos — operam em background sem notificar o usuário.
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const s = await invoke<SniffStats>("get_sniff_stats");
        if (alive) setSniffStats(s);
      } catch { /* sniffer indisponível */ }
      try {
        const s = await invoke<TunnelStatus>("tunnel_status");
        if (alive) setTunnelStatus(s);
      } catch { /* tunnel indisponível */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const albionStatus: AlbionStatus = (() => {
    if (sniffStats?.error && sniffStats.running) {
      return { kind: "sniff_error", msg: sniffStats.error };
    }
    // Online/offline vem do sniffer: pacotes do Albion chegando = online.
    if (!sniffStats?.online) return { kind: "closed" };
    return { kind: "ok" };
  })();

  if (!config) {
    return <div className="splash"><div className="splash-logo">Z</div><div className="splash-text">{t("splashText")}</div></div>;
  }

  // Abas laterais. Lootlog e Damage têm toggle on/off direto na sidebar
  // (off por padrão) — a aba continua acessível pra ver o estado desligado.
  const tabs: { id: Tab; icon: IconName; label: string; toggleKey?: keyof CompanionConfig }[] = [
    { id: "lootlog", icon: "list", label: t("navLootlog"), toggleKey: "collect_auto_lootlog" },
    { id: "damage", icon: "sword", label: t("navDamage"), toggleKey: "collect_damage_meter" },
    { id: "tunnel", icon: "route", label: t("navTunnel") },
    { id: "config", icon: "gear", label: t("navConfig") },
  ];

  const activeTab = tabs.some(tb => tb.id === tab) ? tab : "lootlog";

  // Nome do personagem vem do sniffer. Guild/aliança/mapa chegam ANTES do
  // nome no stream — sem nome, mostra só "carregando…" pra não ficar feio.
  const playerName = sniffStats?.player_name || "";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="logo">Z</span>
          <span className="brand-name">Ziggs</span>
          <DiscordButton config={config} onChange={refreshConfig} />
        </div>

        <nav className="sidebar-nav">
          {tabs.map(tb => (
            <button
              key={tb.id}
              className={`nav-btn ${activeTab === tb.id ? "active" : ""}`}
              onClick={() => setTab(tb.id)}
            >
              <span className="nav-icon"><Icon name={tb.icon} /></span>
              <span className="nav-label">{tb.label}</span>
              {tb.toggleKey && (
                <span className="nav-toggle" onClick={(e) => { e.stopPropagation(); updateConfig(tb.toggleKey!, !config[tb.toggleKey!]); }}>
                  <Toggle on={!!config[tb.toggleKey]} onChange={() => {}} />
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <div className={`status-card ${albionStatus.kind === "ok" ? "ok" : albionStatus.kind === "sniff_error" ? "err" : "idle"}`}>
            <div className="status-card-head">
              <span className={`status-dot ${albionStatus.kind === "ok" ? "on" : "off"}`} />
              <span className="status-card-title">
                {albionStatus.kind === "ok"
                  ? t("albionDetected")
                  : albionStatus.kind === "sniff_error"
                    ? t("albionSniffError")
                    : t("albionClosed")}
              </span>
            </div>
            <div className="status-card-body">
              {albionStatus.kind === "ok" && (playerName ? (
                <>
                  <div className="status-name" title={playerName}>{playerName}</div>
                  {(sniffStats?.alliance_name || sniffStats?.guild_name) && (
                    <div className="status-sub" title={`${sniffStats?.alliance_name} ${sniffStats?.guild_name}`.trim()}>
                      {sniffStats?.alliance_name && <span className="status-ally">[{sniffStats.alliance_name}]</span>}{" "}
                      {sniffStats?.guild_name}
                    </div>
                  )}
                  {sniffStats?.last_map_name && <div className="status-sub" title={sniffStats.last_map}>🗺 {sniffStats.last_map_name}</div>}
                </>
              ) : (
                <div className="status-sub">{t("statusLoading")}</div>
              ))}
              {albionStatus.kind === "closed" && (
                <div className="status-sub">{t("albionClosedHint")}</div>
              )}
              {albionStatus.kind === "sniff_error" && (
                <div className="status-sub" title={albionStatus.msg}>{albionStatus.msg}</div>
              )}
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <h1 className="topbar-title">{tabLabel(activeTab, t)}</h1>
        </header>

        <div className="content">
          <div key={activeTab} className="tab-fade">
            {activeTab === "tunnel" && <TunnelTab config={config} update={updateConfig} tunnelStatus={tunnelStatus} />}
            {activeTab === "lootlog" && <LootlogTab config={config} update={updateConfig} sniffStats={sniffStats} />}
            {activeTab === "damage" && <DamageTab config={config} update={updateConfig} sniffStats={sniffStats} />}
            {activeTab === "config" && (
              <ConfigTab
                config={config} update={updateConfig}
                lang={pref} setLang={setPref}
              />
            )}
          </div>
        </div>
      </main>

      {updateStatus && (
        <div className="update-toast">
          <span className="update-toast-dot" />
          <span>{updateStatus === "downloading" ? t("updateDownloading") : t("updateInstalling")}</span>
        </div>
      )}
    </div>
  );
}

function tabLabel(tab: Tab, t: (k: import("./i18n").TKey, v?: Record<string, string | number>) => string): string {
  switch (tab) {
    case "lootlog": return t("titleLootlog");
    case "damage": return t("navDamage");
    case "tunnel": return t("titleTunnel");
    case "config": return t("titleConfig");
  }
}

// ─── Ícones SVG (JSX, não string) ───────────────────────────────────────────

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

// ─── Damage meter (captura de combate via packet sniffing) ───────────────────

// Formato compacto: 1234567 → "1.23M", 45600 → "45.6K".
function fmtC(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

/// Timeline dos últimos 3 min de um jogador: uma barra por segundo, altura
/// proporcional ao pico dele. preserveAspectRatio="none" deixa o SVG esticar
/// na largura da linha sem deformar a altura das barras.
function DamageTimeline({ data }: { data: number[] }) {
  const t = useT();
  const peak = data.reduce((m, v) => Math.max(m, v), 0);
  if (peak <= 0) return <div className="dmg-tl-empty hint">{t("dmgTimelineEmpty")}</div>;
  return (
    <div className="dmg-tl">
      <div className="dmg-tl-head">
        <span>{t("dmgTimeline")}</span>
        <span className="dmg-tl-peak">{t("dmgPeak")}: {fmtC(peak)}/s</span>
      </div>
      <svg className="dmg-tl-svg" viewBox={`0 0 ${data.length} 40`} preserveAspectRatio="none">
        {data.map((v, i) => v > 0 && (
          <rect key={i} x={i} y={40 - (v / peak) * 40} width={0.9} height={(v / peak) * 40} />
        ))}
      </svg>
      <div className="dmg-tl-axis">
        <span>-3m</span><span>-2m</span><span>-1m</span><span>{t("dmgNow")}</span>
      </div>
    </div>
  );
}

function DamageTab({ config, sniffStats }: {
  config: CompanionConfig;
  update: (key: keyof CompanionConfig, value: unknown) => Promise<void>;
  sniffStats: SniffStats | null;
}) {
  const t = useT();
  const on = config.collect_damage_meter;
  const [rows, setRows] = useState<DamageRow[]>([]);
  const [partyOnly, setPartyOnly] = useState(false);
  const [minDamage, setMinDamage] = useState(0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!on) { setRows([]); return; }
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const r = await invoke<DamageRow[]>("get_damage_meter");
        if (alive) setRows(r);
      } catch { /* sniffer indisponível */ }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(id); };
  }, [on]);

  // Filtro por party: membros do grupo + o próprio jogador.
  const partySet = new Set([
    ...(sniffStats?.party_members ?? []),
    ...(sniffStats?.player_name ? [sniffStats.player_name] : []),
  ]);

  const filtered = rows
    .filter(r => !partyOnly || partySet.has(r.name))
    .filter(r => r.damage >= minDamage);

  const max = filtered.reduce((m, r) => Math.max(m, r.damage), 0) || 1;
  const totalDmg = filtered.reduce((s, r) => s + r.damage, 0) || 1;

  // Export em texto: SÓ jogador e dano. Colar no Discord tem que ser legível
  // no celular — mapa, %, cura e DPS só poluíam a lista.
  const copyMeter = async () => {
    const lines = filtered.map((r, i) => `#${i + 1} ${r.name} — ${fmtC(r.damage)}`);
    await navigator.clipboard.writeText(lines.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>{t("navDamage")}</h2>
        <span className={`pill ${on ? "ok" : "idle"}`}>{on ? t("stateOn") : t("stateOff")}</span>
      </div>
      <p className="card-desc">{t("dmgDesc")}</p>
      {!on ? (
        <div className="hint">{t("dmgOffHint")}</div>
      ) : (
        <>
          <div className="dmg-filters">
            <label className="dmg-filter">
              <Toggle on={partyOnly} onChange={setPartyOnly} />
              <span>{t("dmgPartyOnly")}</span>
            </label>
            <label className="dmg-filter">
              <span>{t("dmgMinDamage")}</span>
              <input
                type="number" min={0} step={1000} value={minDamage || ""}
                placeholder="0"
                onChange={(e) => setMinDamage(Number(e.target.value) || 0)}
              />
            </label>
            <span className="dmg-filter-spacer" />
            <button className="btn small" onClick={copyMeter} disabled={filtered.length === 0}>
              {copied ? t("copied") : t("dmgCopy")}
            </button>
            <button className="btn small" onClick={() => invoke("clear_damage_meter").then(() => setRows([]))} disabled={rows.length === 0}>
              {t("clearLoot")}
            </button>
          </div>
          {filtered.length === 0 ? (
            <div className="hint">{t("dmgEmptyHint")}</div>
          ) : (
            <div className="dmg-list">
              {filtered.map((r, i) => (
                <div key={r.name} className="dmg-entry">
                  <div
                    className={`dmg-row clickable ${expanded[r.name] ? "open" : ""}`}
                    onClick={() => setExpanded(e => ({ ...e, [r.name]: !e[r.name] }))}
                  >
                    <span className="dmg-caret">{expanded[r.name] ? "▾" : "▸"}</span>
                    <span className="dmg-rank">{i + 1}</span>
                    <span className="dmg-name">{r.name}</span>
                    <span className="dmg-bar-wrap">
                      <span className="dmg-bar" style={{ width: `${(r.damage / max) * 100}%` }} />
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
                                {/* Barra de fundo = fatia desta skill no dano do jogador. */}
                                <span className="dmg-skill-bar" style={{ width: `${sk.pct}%` }} />
                                <span className="dmg-skill-label">
                                  {sk.id < 0
                                    ? t("dmgAutoAttack")
                                    : sk.name || t("dmgSkillN", { id: sk.id })}
                                  {/* id cru sempre visível: é com ele que se
                                      confere se o nome resolvido bate com a
                                      skill que você realmente usou. */}
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
          )}
        </>
      )}
    </div>
  );
}

// ─── Lootlog ────────────────────────────────────────────────────────────────

function LootlogTab({ config, update, sniffStats }: { config: CompanionConfig; update: (key: keyof CompanionConfig, value: unknown) => Promise<void>; sniffStats: SniffStats | null }) {
  const t = useT();
  const [loot, setLoot] = useState<LootRow[]>([]);
  const [debug, setDebug] = useState<DebugLine[]>([]);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [events, setEvents] = useState<ActiveEvent[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [submitMsg, setSubmitMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const terminalRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const loggedIn = !!config.discord_token;
  const guildSet = !!config.lootlog_guild_id;

  // Poll: loot capturado + debug do sniffer (a cada 2s).
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const [rows, lines] = await Promise.all([
          invoke<LootRow[]>("get_captured_loot"),
          invoke<DebugLine[]>("get_sniffer_debug"),
        ]);
        if (!alive) return;
        setLoot(rows);
        setDebug(lines);
      } catch { /* sniffer não rodando */ }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Auto-scroll pro fim do terminal quando chega conteúdo novo.
  const totalLines = loot.length + debug.length;
  useEffect(() => {
    if (autoScroll && terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [totalLines, autoScroll]);

  const refreshEvents = useCallback(async () => {
    if (!loggedIn || !guildSet) { setEvents([]); return; }
    try {
      const evs = await invoke<ActiveEvent[]>("get_active_events");
      setEvents(evs);
      if (evs.length === 1) setSelectedEvent(evs[0].event_id);
    } catch { setEvents([]); }
  }, [loggedIn, guildSet]);

  useEffect(() => { refreshEvents(); }, [refreshEvents]);

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

  const handleSubmit = async (eventId: number) => {
    setSubmitMsg(null);
    try {
      const out = await invoke<LootlogIngestOut>("submit_captured_loot", { eventId });
      setSubmitMsg({ ok: true, text: t("lootlogSubmitted", { n: out.row_count, id: out.id }) });
    } catch (e) {
      setSubmitMsg({ ok: false, text: String(e) });
    }
  };

  // Auto-submit quando há loot novo + auto_lootlog_submit + evento selecionado.
  const lastAutoSubCount = useRef(0);
  useEffect(() => {
    if (config.auto_lootlog_submit && selectedEvent && loot.length > lastAutoSubCount.current) {
      lastAutoSubCount.current = loot.length;
      handleSubmit(selectedEvent);
    }
  }, [loot.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const onTerminalScroll = () => {
    if (!terminalRef.current) return;
    const el = terminalRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    setAutoScroll(atBottom);
  };

  // Lootlog desligado (toggle na sidebar): não captura nada.
  if (!config.collect_auto_lootlog) {
    return (
      <div className="card">
        <div className="card-head">
          <h2>{t("lootlogTitle")}</h2>
          <span className="pill idle">{t("stateOff")}</span>
        </div>
        <p className="card-desc">{t("lootlogDesc")}</p>
        <div className="hint">{t("lootlogOffHint")}</div>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>{t("lootlogTitle")}</h2>
          <span className={`pill ${loot.length > 0 ? "ok" : "idle"}`}>{t("lootRows", { n: loot.length })}</span>
        </div>
        <p className="card-desc">{t("lootlogDesc")}</p>
        <div className="row">
          <button className="btn primary" onClick={handleDownload} disabled={loot.length === 0}>
            {t("downloadCsv")}
          </button>
          <button className="btn" onClick={handleClear} disabled={loot.length === 0}>
            {t("clearLoot")}
          </button>
        </div>
        {savedPath && <div className="hint ok">{t("savedAt", { path: savedPath })}</div>}
        {saveErr && <div className="warning-box">{saveErr}</div>}
      </div>

      {loggedIn ? (
        <div className="card">
          <div className="card-head">
            <h2>{t("autoSubmitTitle")}</h2>
          </div>
          <p className="card-desc">{t("autoSubmitDesc")}</p>
          <div className="row">
            <label>{t("cfgLootlogGuild")}</label>
            <input
              type="text" className="mono"
              value={config.lootlog_guild_id || ""}
              onChange={(e) => update("lootlog_guild_id", e.target.value)}
              placeholder="Discord server ID (snowflake)"
            />
          </div>
          <div className="hint">{t("cfgLootlogGuildHint")}</div>
          {guildSet && (
            <>
              <ToggleRow
                label={t("autoSubmitToggle")}
                on={config.auto_lootlog_submit}
                onChange={(v) => update("auto_lootlog_submit", v)}
              />
              {events.length > 0 ? (
                <div className="row">
                  <label>{t("selectEvent")}</label>
                  <select value={selectedEvent ?? ""} onChange={(e) => setSelectedEvent(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">{t("choose")}</option>
                    {events.map(ev => (
                      <option key={ev.event_id} value={ev.event_id}>
                        #{ev.event_id}{ev.title ? ` — ${ev.title}` : ""}
                      </option>
                    ))}
                  </select>
                  <button className="btn primary" onClick={() => selectedEvent && handleSubmit(selectedEvent)} disabled={loot.length === 0 || !selectedEvent}>
                    {t("submitNow")}
                  </button>
                </div>
              ) : (
                <div className="hint">{t("noActiveEvents")}</div>
              )}
              {submitMsg && (
                <div className={submitMsg.ok ? "hint ok" : "warning-box"}>{submitMsg.text}</div>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="hint">{t("connectDiscordForLootlog")}</div>
      )}

      <div className="card">
        <h2>{t("capturedLoot")}</h2>
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
              <span className="t-time">{r.ts}</span>{" "}
              <span className="t-loot-tag">[LOOT]</span>{" "}
              <span className="t-player">{r.looted_by}</span>{" "}
              <span className="t-action">{t("lootedBy")}</span>{" "}
              <span className="t-item">{r.quantity}× {r.item_id}</span>{" "}
              <span className="t-from">{t("from")}</span>{" "}
              <span className="t-source">{r.looted_from}</span>
            </div>
          ))}
          {submitMsg && (
            <div className="terminal-line">
              <span className={submitMsg.ok ? "t-ok-tag" : "t-err-tag"}>[{submitMsg.ok ? "OK" : "ERR"}]</span>{" "}
              <span className={submitMsg.ok ? "t-ok-msg" : "t-err"}>{submitMsg.text}</span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ─── Tunnel ─────────────────────────────────────────────────────────────────

function TunnelTab({
  config, tunnelStatus,
}: {
  config: CompanionConfig;
  update: (k: keyof CompanionConfig, v: unknown) => Promise<void>;
  tunnelStatus: TunnelStatus | null;
}) {
  const t = useT();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    invoke<boolean>("tunnel_is_admin").then(setIsAdmin).catch(() => setIsAdmin(false));
  }, []);

  useEffect(() => {
    setRunning(!!tunnelStatus?.running);
  }, [tunnelStatus?.running]);

  // Sem VPS configurada (endpoint definido no código quando estiver pronta):
  // mostra o placeholder "em breve" em vez da UI funcional.
  if (!config.tunnel_endpoint) {
    return (
      <div className="tunnel-soon">
        <div className="tunnel-soon-icon"><Icon name="route" /></div>
        <h2>{t("tunnelSoonTitle")}</h2>
        <p>{t("tunnelSoonDesc")}</p>
        <span className="pill idle">{t("tunnelSoonPill")}</span>
      </div>
    );
  }

  const toggle = async () => {
    if (running) {
      await invoke("tunnel_stop");
      setRunning(false);
    } else {
      await invoke("tunnel_start");
      setRunning(true);
    }
  };

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>{t("tunnelTitle")}</h2>
          {tunnelStatus && (
            <span className={`pill ${tunnelStatus.using_tunnel ? "ok" : tunnelStatus.connected ? "idle" : "err"}`}>
              {tunnelStatus.using_tunnel ? t("tunnelActive") : tunnelStatus.connected ? t("tunnelConnected") : t("tunnelOff")}
            </span>
          )}
        </div>
        <p className="card-desc">{t("tunnelDesc")}</p>

        {isAdmin === false && (
          <div className="warning-box">{t("noAdmin")}</div>
        )}

        <div className="row">
          <button className={`btn ${running ? "danger" : "primary"}`} onClick={toggle} disabled={isAdmin === false}>
            {running ? t("turnOffTunnel") : t("turnOnTunnel")}
          </button>
        </div>
      </div>

      {tunnelStatus && (tunnelStatus.direct_latency_ms != null || tunnelStatus.tunnel_latency_ms != null) && (
        <div className="card">
          <h2>{t("latencyTitle")}</h2>
          <div className="stat-grid">
            <Stat label={t("directLatency")} value={tunnelStatus.direct_latency_ms != null ? `${tunnelStatus.direct_latency_ms.toFixed(0)}ms` : "—"} />
            <Stat
              label={t("viaTunnel")}
              value={tunnelStatus.tunnel_latency_ms != null ? `${tunnelStatus.tunnel_latency_ms.toFixed(0)}ms` : "—"}
              color={tunnelStatus.tunnel_latency_ms != null && tunnelStatus.direct_latency_ms != null && tunnelStatus.tunnel_latency_ms < tunnelStatus.direct_latency_ms ? "green" : "red"}
            />
            <Stat label={t("inUse")} value={tunnelStatus.using_tunnel ? t("tunnel") : t("direct")} small color={tunnelStatus.using_tunnel ? "green" : "muted"} />
            <Stat label={t("traffic")} value={`${(tunnelStatus.bytes_sent / 1024).toFixed(1)}K↑ ${(tunnelStatus.bytes_received / 1024).toFixed(1)}K↓`} small />
          </div>
          {tunnelStatus.last_error && <div className="warning-box amber">{tunnelStatus.last_error}</div>}
        </div>
      )}
    </>
  );
}

// ─── Config ─────────────────────────────────────────────────────────────────

function ConfigTab({
  config, update, lang, setLang,
}: {
  config: CompanionConfig;
  update: (key: keyof CompanionConfig, value: unknown) => Promise<void>;
  lang: LangPref;
  setLang: (l: LangPref) => void;
}) {
  const t = useT();
  return (
    <>
      <div className="card">
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

      <div className="card">
        <h2>{t("cfgSystem")}</h2>
        <ToggleRow label={t("autostart")} on={config.autostart} onChange={(v) => update("autostart", v)} />
        <ToggleRow label={t("minimizeTray")} on={config.minimize_to_tray} onChange={(v) => update("minimize_to_tray", v)} />
      </div>

      <div className="card">
        <h2>{t("cfgShareTitle")}</h2>
        <p className="card-desc">{t("cfgShareDesc")}</p>
        <ToggleRow label={t("feedAodp")} on={config.feed_aodp} onChange={(v) => update("feed_aodp", v)} />
      </div>

      <div className="card">
        <h2>{t("cfgCalibTitle")}</h2>
        <p className="card-desc">{t("cfgCalibDesc")}</p>
        <div className="config-row">
          <span>{t("cfgCalibOffset")}</span>
          <input
            type="number" step={1} value={config.spell_index_offset}
            onChange={(e) => update("spell_index_offset", Number(e.target.value) || 0)}
          />
        </div>
      </div>
    </>
  );
}

// ─── Discord (botão no topo da sidebar, ao lado da logo) ─────────────────────

function DiscordIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M20.3 4.4A19.8 19.8 0 0 0 15.4 3l-.2.4a18.3 18.3 0 0 1 4.3 1.3 13.5 13.5 0 0 0-11-.0A18.3 18.3 0 0 1 12.8 3.4L12.6 3A19.8 19.8 0 0 0 7.7 4.4C4.6 9 3.8 13.5 4.2 17.9a19.9 19.9 0 0 0 6 3l.5-.7a13 13 0 0 1-1.9-.9l.4-.3a14.2 14.2 0 0 0 12.1 0l.4.3c-.6.4-1.2.7-1.9.9l.5.7a19.9 19.9 0 0 0 6-3c.5-5.1-.8-9.6-3.5-13.5ZM9.7 15.3c-1.2 0-2.1-1.1-2.1-2.4S8.5 10.5 9.7 10.5s2.1 1.1 2.1 2.4-.9 2.4-2.1 2.4Zm4.6 0c-1.2 0-2.1-1.1-2.1-2.4s.9-2.4 2.1-2.4 2.1 1.1 2.1 2.4-.9 2.4-2.1 2.4Z"/>
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