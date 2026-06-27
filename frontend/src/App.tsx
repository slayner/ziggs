import { useEffect, useRef, useState } from "react";
import Dashboard from "./components/Dashboard";
import CompBuilder from "./components/CompBuilder";
import EventsPage from "./components/EventsPage";
import CraftCalculator from "./components/CraftCalculator";
import BattleTracker from "./components/BattleTracker";
import BattlePage from "./components/BattlePage";
import PlayerProfilePage from "./components/PlayerProfilePage";
import { useLocation, navigate, parseBattleRoute, parsePlayerRoute, parseGuildRoute } from "./router";
import PlayerLookup from "./components/PlayerLookup";
import GuildPicker from "./components/GuildPicker";
import GuildConfig from "./components/GuildConfig";
import GuildProfilePage from "./components/GuildProfilePage";
import { api, setGuild, NO_PERMS, type Me, type Permissions, type SiteGuild } from "./api";
import { useLang, useT, useServer, LANG_LABELS, LANG_FULL, SERVER_LABELS, SERVER_FULL, type Lang, type GameServer } from "./i18n";
import ClaimsPanel from "./components/ClaimsPanel";

type PublicView = "dashboard" | "craft" | "players" | "battles";
type GuildView = "comps" | "events" | "config";
type View = PublicView | GuildView;

function guildIconUrl(guild: SiteGuild) {
  if (!guild.icon) return null;
  return `https://cdn.discordapp.com/icons/${guild.id}/${guild.icon}.png?size=32`;
}

export default function App() {
  const { lang, setLang } = useLang();
  const { server, setServer, servers, toggleServer } = useServer();
  const t = useT();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [view, setView] = useState<View>("dashboard");
  const [siteGuilds, setSiteGuilds] = useState<SiteGuild[]>([]);
  const [perms, setPerms] = useState<Permissions>(NO_PERMS);
  const [pickingGuild, setPickingGuild] = useState(false);
  const [guildDropOpen, setGuildDropOpen] = useState(false);
  const [userDropOpen, setUserDropOpen] = useState(false);
  const [userPanel, setUserPanel] = useState<"main" | "lang" | "server" | "claims">("main");
  const dropRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);
  const loc = useLocation();

  const battleRoute = parseBattleRoute(loc);
  const playerRoute = parsePlayerRoute(loc);
  const guildRoute  = parseGuildRoute(loc);

  useEffect(() => {
    // a página de batalha também mostra o topbar (login/idioma/servidor), então
    // precisa da sessão igual o resto do site — só não busca de novo se já tem.
    if (me !== undefined) return;
    api.me().then(m => {
      setMe(m);
      if (m?.guild_id) {
        setGuild(m.guild_id);
        Promise.all([api.mySiteGuilds(), api.myPermissions()]).then(([gs, p]) => {
          setSiteGuilds(gs);
          setPerms(p);
        });
      }
    });
  }, [loc, me]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const node = e.target as Node;
      if (dropRef.current && !dropRef.current.contains(node)) setGuildDropOpen(false);
      if (userRef.current && !userRef.current.contains(node)) {
        setUserDropOpen(false);
        setUserPanel("main");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  if (me === undefined) return null;

  const loggedIn = me !== null;
  const botGuilds     = siteGuilds.filter(g => g.bot_present);
  const currentGuild  = botGuilds.find(g => g.id === me?.guild_id) ?? null;
  const hasGuild      = !!me?.guild_id && !!currentGuild;
  const multiGuild    = botGuilds.length > 1;
  const hasAnyGuildPerm = Object.values(perms).some(Boolean);

  async function logout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    setMe(null); setSiteGuilds([]); setPerms(NO_PERMS);
    setView("dashboard");
  }

  function onGuildSelected(_id: string, _name: string, _bot: boolean) {
    setGuild(_id);
    setMe(prev => prev ? { ...prev, guild_id: _id } : prev);
    setPickingGuild(false);
    Promise.all([api.mySiteGuilds(), api.myPermissions()]).then(([gs, p]) => {
      setSiteGuilds(gs);
      setPerms(p);
    });
    setView(_bot ? "comps" : "config");
  }

  async function switchGuild(guild: SiteGuild) {
    setGuildDropOpen(false);
    const res = await api.switchGuild(guild.id);
    setGuild(guild.id);
    setMe(prev => prev ? { ...prev, guild_id: guild.id } : prev);
    const [gs, p] = await Promise.all([api.mySiteGuilds(), api.myPermissions()]);
    setSiteGuilds(gs); setPerms(p);
    setView(res.bot_present ? "comps" : "config");
  }

  const nb = (v: View, icon: string, label: string) => (
    <button className={!battleRoute && !playerRoute && !guildRoute && view === v ? "active" : ""} onClick={() => { navigate("/"); setView(v); }}>
      <i className={`ti ${icon}`} aria-hidden="true" /> {label}
    </button>
  );

  // Quick-switch: only visible when user has 2+ servers selected
  const serverQuickSwitch = servers.length > 1 ? (
    <div className="server-quick-switch">
      {servers.map(sv => (
        <button
          key={sv}
          className={"server-quick-btn" + (sv === server ? " active" : "")}
          title={SERVER_FULL[sv]}
          onClick={() => setServer(sv)}
        >
          {SERVER_LABELS[sv]}
        </button>
      ))}
    </div>
  ) : null;

  const userMenu = (
    <div className="topbar-dropdown" ref={userRef}>
      <button className="topbar-dropdown-btn" onClick={() => { setUserDropOpen(o => !o); setUserPanel("main"); }}>
        <i className={`ti ${loggedIn ? "ti-user-circle" : "ti-settings"}`} />
        <span>{loggedIn ? (me!.global_name || me!.username) : t("config")}</span>
        <i className="ti ti-chevron-down" style={{ fontSize: 10 }} />
      </button>
      {userDropOpen && (
        <div className="topbar-dropdown-menu user-menu">

          {userPanel === "claims" && (
            <ClaimsPanel onBack={() => setUserPanel("main")} />
          )}

          {userPanel === "main" && <>
            {loggedIn && <>
              <div className="user-menu-header">
                <i className="ti ti-user-circle" style={{ fontSize: 28, color: "var(--muted)" }} />
                <span>{me!.global_name || me!.username}</span>
              </div>
              <div className="user-menu-divider" />
              <button className="topbar-dropdown-item" onClick={() => setUserPanel("claims")}>
                <i className="ti ti-user-check" />
                Meus personagens
                <i className="ti ti-chevron-right" style={{ fontSize: 11 }} />
              </button>
              <div className="user-menu-divider" />
            </>}
            <button className="topbar-dropdown-item" onClick={() => setUserPanel("lang")}>
              <i className="ti ti-language" />
              {t("lang")}
              <span className="user-menu-current">{LANG_FULL[lang]}</span>
              <i className="ti ti-chevron-right" style={{ fontSize: 11 }} />
            </button>
            <button className="topbar-dropdown-item" onClick={() => setUserPanel("server")}>
              <i className="ti ti-server-2" />
              {t("server")}
              <span className="user-menu-current">{servers.map(s => SERVER_LABELS[s]).join(" · ")}</span>
              <i className="ti ti-chevron-right" style={{ fontSize: 11 }} />
            </button>
            <div className="user-menu-divider" />
            {loggedIn ? (
              <button className="topbar-dropdown-item danger" onClick={() => { setUserDropOpen(false); setUserPanel("main"); logout(); }}>
                <i className="ti ti-logout" /> {t("logout")}
              </button>
            ) : (
              <a className="topbar-dropdown-item" href="/auth/discord/login">
                <i className="ti ti-brand-discord" /> {t("loginDiscord")}
              </a>
            )}
          </>}

          {userPanel === "lang" && <>
            <button className="user-menu-back" onClick={() => setUserPanel("main")}>
              <i className="ti ti-arrow-left" /> {t("lang")}
            </button>
            <div className="user-menu-divider" />
            {(Object.keys(LANG_LABELS) as Lang[]).map(l => (
              <button key={l} className={"topbar-dropdown-item" + (l === lang ? " active" : "")}
                onClick={() => { setLang(l); setUserPanel("main"); }}>
                <span className="user-menu-abbr">{LANG_LABELS[l]}</span>
                {LANG_FULL[l]}
                {l === lang && <i className="ti ti-check" style={{ marginLeft: "auto", fontSize: 13 }} />}
              </button>
            ))}
          </>}

          {userPanel === "server" && <>
            <button className="user-menu-back" onClick={() => setUserPanel("main")}>
              <i className="ti ti-arrow-left" /> {t("server")}
            </button>
            <div className="user-menu-divider" />
            {(Object.keys(SERVER_LABELS) as GameServer[]).map(sv => {
              const selected = servers.includes(sv);
              const isActive = sv === server;
              return (
                <button key={sv} className={"topbar-dropdown-item" + (selected ? " active" : "")} onClick={() => toggleServer(sv)}>
                  <i className={"ti " + (selected ? "ti-square-check-filled" : "ti-square")} style={{ fontSize: 15 }} />
                  <span className="user-menu-abbr">{SERVER_LABELS[sv]}</span>
                  {SERVER_FULL[sv]}
                  {isActive && selected && servers.length > 1 && (
                    <span className="user-menu-active-dot" />
                  )}
                </button>
              );
            })}
          </>}

        </div>
      )}
    </div>
  );

  // ── Conteúdo ─────────────────────────────────────────────────────────────
  let content: React.ReactNode;

  if (battleRoute) {
    content = battleRoute.type === "code"
      ? <BattlePage code={battleRoute.code} onBack={() => navigate("/")} />
      : <BattlePage albionIds={battleRoute.albionIds} onBack={() => navigate("/")} />;
  } else if (playerRoute) {
    content = <PlayerProfilePage region={playerRoute.region} name={playerRoute.name} onBack={() => navigate("/")} />;
  } else if (guildRoute) {
    content = <GuildProfilePage mode={guildRoute.type} albionId={guildRoute.albionId} onBack={() => navigate("/")} />;
  } else if (pickingGuild && loggedIn) {
    content = <GuildPicker onSelect={onGuildSelected} />;
  } else if (!loggedIn && (view === "comps" || view === "events" || view === "config")) {
    content = (
      <div className="login-gate">
        <i className="ti ti-lock login-gate-icon" />
        <p className="login-gate-title">{t("guildOnly")}</p>
        <p className="login-gate-sub">{t("loginRequired")}</p>
        <a className="btn btn-discord" href="/auth/discord/login">
          <i className="ti ti-brand-discord" /> {t("loginDiscord")}
        </a>
      </div>
    );
  } else if (loggedIn && (view === "comps" || view === "events") && !hasGuild) {
    content = <GuildPicker onSelect={onGuildSelected} />;
  } else if (view === "dashboard") { content = <Dashboard onOpenBattles={() => { navigate("/"); setView("battles"); }} />; }
  else if (view === "battles") { content = <BattleTracker />; }
  else if (view === "players")   { content = <PlayerLookup />; }
  else if (view === "craft")     { content = <CraftCalculator />; }
  else if (view === "comps")     { content = <CompBuilder perms={perms} />; }
  else if (view === "events")    { content = <EventsPage perms={perms} />; }
  else                           { content = <GuildConfig guildId={me!.guild_id!} onSwitch={() => setPickingGuild(true)} />; }

  // ── Seletor de servidor na nav ────────────────────────────────────────────
  const guildLabel = hasGuild && currentGuild ? (
    multiGuild ? (
      <div className="guild-switcher" ref={dropRef}>
        <button className="guild-switcher-btn" onClick={() => setGuildDropOpen(o => !o)}>
          {guildIconUrl(currentGuild) && <img src={guildIconUrl(currentGuild)!} alt="" className="guild-switcher-icon" />}
          <span>{currentGuild.name}</span>
          <i className="ti ti-chevron-down" style={{ fontSize: 11 }} />
        </button>
        {guildDropOpen && (
          <div className="guild-switcher-dropdown">
            {botGuilds.map(g => (
              <button key={g.id} className={`guild-switcher-option${g.id === me?.guild_id ? " active" : ""}`} onClick={() => switchGuild(g)}>
                {guildIconUrl(g) ? <img src={guildIconUrl(g)!} alt="" className="guild-switcher-icon" /> : <span className="guild-switcher-icon-placeholder">{g.name[0]}</span>}
                <span>{g.name}</span>
              </button>
            ))}
            <button className="guild-switcher-option guild-switcher-add" onClick={() => { setGuildDropOpen(false); setPickingGuild(true); }}>
              <i className="ti ti-plus" /> {t("addServer")}
            </button>
          </div>
        )}
      </div>
    ) : (
      <div className="guild-switcher" ref={dropRef}>
        <button className="guild-switcher-btn" onClick={() => setGuildDropOpen(o => !o)}>
          {guildIconUrl(currentGuild) && <img src={guildIconUrl(currentGuild)!} alt="" className="guild-switcher-icon" />}
          <span>{currentGuild.name}</span>
          <i className="ti ti-chevron-down" style={{ fontSize: 11 }} />
        </button>
        {guildDropOpen && (
          <div className="guild-switcher-dropdown">
            <button className={`guild-switcher-option active`} onClick={() => setGuildDropOpen(false)}>
              {guildIconUrl(currentGuild) ? <img src={guildIconUrl(currentGuild)!} alt="" className="guild-switcher-icon" /> : <span className="guild-switcher-icon-placeholder">{currentGuild.name[0]}</span>}
              <span>{currentGuild.name}</span>
              <i className="ti ti-circle-check-filled" style={{ color: "var(--green)", fontSize: 12, marginLeft: "auto" }} />
            </button>
            <button className="guild-switcher-option guild-switcher-add" onClick={() => { setGuildDropOpen(false); setPickingGuild(true); }}>
              <i className="ti ti-plus" /> {t("addServer")}
            </button>
          </div>
        )}
      </div>
    )
  ) : (
    loggedIn ? (
      <button className="guild-switcher-btn" onClick={() => setPickingGuild(true)}>
        <i className="ti ti-server" style={{ fontSize: 14 }} />
        <span>{t("selectServer")}</span>
        <i className="ti ti-chevron-down" style={{ fontSize: 11 }} />
      </button>
    ) : (
      <span className="nav-guild-label">
        <i className="ti ti-lock" /> Guilda
      </span>
    )
  );

  // abas visíveis baseadas em permissões
  const showComps  = loggedIn && hasGuild && perms["comps.view"];
  const showEvents = loggedIn && hasGuild && perms["events.view"];
  const showConfig = loggedIn && hasGuild && perms["guild.admin"];
  const showGuildBox = !loggedIn || !hasGuild || hasAnyGuildPerm;

  return (
    <>
      <div className="topbar">
        <div className="brand">
          <span className="logo"><i className="ti ti-shield-half" /></span>
          Ziggs
        </div>

        <nav className="nav nav-public">
          {nb("dashboard", "ti-home",         t("dashboard"))}
          {nb("battles", "ti-shield-bolt",    t("battles"))}
          {nb("players", "ti-sword",          t("players"))}
          {nb("craft",   "ti-hammer",         t("craft"))}
        </nav>

        <div className="nav-sep" />

        {showGuildBox && (
          <div className={`nav-guild-box${loggedIn && hasGuild && !hasAnyGuildPerm ? " locked" : (!loggedIn ? " locked" : "")}`}>
            {guildLabel}
            <nav className="nav">
              {(showComps  || (!loggedIn || !hasGuild)) && nb("comps",  "ti-layout-grid",    t("comps"))}
              {(showEvents || (!loggedIn || !hasGuild)) && nb("events", "ti-calendar-event", t("events"))}
              {showConfig && nb("config", "ti-settings", t("config"))}
            </nav>
          </div>
        )}

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          {serverQuickSwitch}
          {userMenu}
        </div>
      </div>

      {content}
    </>
  );
}
