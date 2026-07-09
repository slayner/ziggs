import { useEffect, useRef, useState, lazy, Suspense, Component, type ReactNode } from "react";
import { useLocation, navigate, parseBattleRoute, parsePlayerRoute, parseGuildRoute, parseEventRoute, parseRegearRoute, parseRegearEventFilter } from "./router";
import { api, setGuild, NO_PERMS, type Me, type Permissions, type SiteGuild } from "./api";
import { useLang, useT, useServer, LANG_LABELS, LANG_FULL, SERVER_LABELS, SERVER_FULL, type Lang, type GameServer } from "./i18n";
import AdBanner from "./components/AdBanner";

// code-split por página: cada view só baixa seu próprio JS quando é aberta
// pela primeira vez, em vez de tudo (CompBuilder ~2000 linhas incluso) no bundle inicial.
const Dashboard = lazy(() => import("./components/Dashboard"));
const CraftCalculator = lazy(() => import("./components/CraftCalculator"));
const BattleTracker = lazy(() => import("./components/BattleTracker"));
const HighscoresPage = lazy(() => import("./components/HighscoresPage"));
const BattlePage = lazy(() => import("./components/BattlePage"));
const PlayerProfilePage = lazy(() => import("./components/PlayerProfilePage"));
const PlayerLookup = lazy(() => import("./components/PlayerLookup"));
const GuildPicker = lazy(() => import("./components/GuildPicker"));
const GuildConfig = lazy(() => import("./components/GuildConfig"));
const GuildProfilePage = lazy(() => import("./components/GuildProfilePage"));
const EscalacaoPage = lazy(() => import("./components/EscalacaoPage"));
const RegearPage = lazy(() => import("./components/RegearPage"));
const ManagementPage = lazy(() => import("./components/ManagementPage"));
const ClaimsPanel = lazy(() => import("./components/ClaimsPanel"));
const BotDocsPage = lazy(() => import("./components/BotDocsPage"));

type PublicView = "dashboard" | "craft" | "players" | "battles" | "highscores" | "docs";
type GuildView = "config" | "management";
type View = PublicView | GuildView;

// ponytail: boundary mínimo — sem ele, qualquer throw no render de uma página
// (lazy ou não) derruba a árvore inteira e vira tela branca sem mensagem. Aqui
// ele captura, mostra o erro pra diagnosticar e um botão pra remontar (limpa o
// erro sem reload full, preservando sessão/topbar).
class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error("[ErrorBoundary]", error); }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: "var(--text)", maxWidth: 720, margin: "0 auto" }}>
          <h2 style={{ color: "var(--gold)" }}>⚠️ Algo quebrou ao renderizar</h2>
          <pre style={{ whiteSpace: "pre-wrap", color: "var(--muted)", fontSize: 12 }}>
            {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack}
          </pre>
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Tentar de novo
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

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
  // "Ver todos" no card de ranking semanal do dashboard pula direto pra
  // Highscores já com fama PvP semanal selecionada — HighscoresPage não tem
  // rota própria (view em memória, ver PublicView), então isso é passado
  // como prop em vez de query string.
  const [highscoresInitialWindow, setHighscoresInitialWindow] = useState<"alltime" | "week">("alltime");
  const [siteGuilds, setSiteGuilds] = useState<SiteGuild[]>([]);
  const [isDiscordAdmin, setIsDiscordAdmin] = useState(false);
  const [perms, setPerms] = useState<Permissions>(NO_PERMS);
  const [pickingGuild, setPickingGuild] = useState(false);
  const [guildDropOpen, setGuildDropOpen] = useState(false);
  const [userDropOpen, setUserDropOpen] = useState(false);
  const [userPanel, setUserPanel] = useState<"main" | "lang" | "server" | "claims">("main");
  const [reprocessProgress, setReprocessProgress] = useState<{ percent: number; pending: number } | null>(null);
  // Keep-alive management/config: cada uma monta na primeira visita e fica
  // montada (display:none quando inativa) pra trocar entre as duas sem
  // refazer fetch — é a área de admin onde se navega muito.
  const [visitedMc, setVisitedMc] = useState<Set<"management" | "config">>(new Set());
  // Keep-alive dos deep links escalacao/regear: guardam os últimos params da
  // rota ativa pra a instância ficar montada (display:none) ao navegar pra
  // outra view e voltar — igual management/settings, sem refetch.
  const [escState, setEscState] = useState<{ guildId: string; eventId: number } | null>(null);
  const [regearState, setRegearState] = useState<{ guildId: string; initialRequestId?: number; eventId?: number } | null>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);
  const loc = useLocation();

  const battleRoute = parseBattleRoute(loc);
  const playerRoute = parsePlayerRoute(loc);
  const guildRoute  = parseGuildRoute(loc);
  const eventRoute  = parseEventRoute(loc);
  const regearRoute = parseRegearRoute(loc);
  const regearEventFilter = parseRegearEventFilter(loc);

  useEffect(() => {
    // a página de batalha também mostra o topbar (login/idioma/servidor), então
    // precisa da sessão igual o resto do site — só não busca de novo se já tem.
    if (me !== undefined) return;
    // ponytail: timeout — se o backend pendurar em /auth/me (pool esgotado por
    // writers em busy-wait no write-lock do SQLite, ou processo stale), o fetch
    // nunca settleia e `me` ficaria undefined pra sempre → App retorna null →
    // #root vazio só com a cor de fundo (tela "branca"). Aos 8s assumimos
    // logged-out: mostra topbar + login em vez de blank eterno.
    let done = false;
    const to = setTimeout(() => { if (!done) { done = true; setMe(null); } }, 8000);
    api.me().then(m => {
      if (done) return;
      done = true;
      clearTimeout(to);
      setMe(m);
      if (m) {
        // menu do bot (docs) só aparece pra quem é admin em algum servidor Discord.
        api.myDiscordGuilds().then(gs => setIsDiscordAdmin(gs.some(g => g.is_admin))).catch(() => {});
      }
      if (m?.guild_id) {
        setGuild(m.guild_id);
        Promise.all([api.mySiteGuilds(), api.myPermissions()]).then(([gs, p]) => {
          setSiteGuilds(gs);
          setPerms(p);
        });
      }
    });
    return () => clearTimeout(to);
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

  useEffect(() => {
    if (!userDropOpen) return;
    const poll = () => fetch("/meta/reprocess-progress").then(r => r.json()).then(setReprocessProgress).catch(() => {});
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [userDropOpen]);

  const loggedIn = me !== null;
  const botGuilds     = siteGuilds.filter(g => g.bot_present);
  const currentGuild  = botGuilds.find(g => g.id === me?.guild_id) ?? null;
  const hasGuild      = !!me?.guild_id && !!currentGuild;
  const multiGuild    = botGuilds.length > 1;
  const hasAnyGuildPerm = Object.values(perms).some(Boolean);

  // Keep-alive ativo quando estamos mostrando management ou config direto
  // (sem deep link de rota, sem picker aberto, logado com guilda). Fora disso
  // as duas continuam montadas mas escondidas (display:none), preservando estado.
  const noRoute = !eventRoute && !regearRoute && !regearEventFilter && !battleRoute && !playerRoute && !guildRoute;
  const useKeepAlive = loggedIn && hasGuild && !pickingGuild && noRoute && (view === "management" || view === "config");
  // Deep links escalacao/regear ativos no momento (definem qual keep-alive show).
  const escActive = !!eventRoute;
  const regearActive = !!regearRoute || !!(regearEventFilter && me?.guild_id);
  // Params do deep link regear ATIVO agora (request OU filtro por evento). Null
  // quando nenhum dos dois tá ativo. Usado pra renderizar o keep-alive na mesma
  // passada (sem esperar o efeito setRegearState popular) — evita frame branco.
  const regearActiveParams = regearRoute
    ? { guildId: regearRoute.guildId, initialRequestId: regearRoute.requestId, eventId: undefined as number | undefined }
    : (regearEventFilter && me?.guild_id)
      ? { guildId: String(me.guild_id), initialRequestId: undefined as number | undefined, eventId: regearEventFilter }
      : null;

  useEffect(() => {
    if (useKeepAlive) setVisitedMc(prev => prev.has(view as "management" | "config") ? prev : new Set(prev).add(view as "management" | "config"));
  }, [useKeepAlive, view]);

  // Memoriza os params do deep link enquanto ele tá ativo, pra a instância
  // keep-alive continuar montada com esses params quando a rota sair.
  useEffect(() => {
    if (eventRoute) setEscState({ guildId: eventRoute.guildId, eventId: eventRoute.eventId });
  }, [eventRoute?.guildId, eventRoute?.eventId]);
  useEffect(() => {
    if (regearRoute) setRegearState({ guildId: regearRoute.guildId, initialRequestId: regearRoute.requestId });
    else if (regearEventFilter && me?.guild_id) setRegearState({ guildId: String(me.guild_id), eventId: regearEventFilter });
  }, [regearRoute?.guildId, regearRoute?.requestId, regearEventFilter, me?.guild_id]);

  // Precisa vir DEPOIS de todos os hooks acima (não antes) — um early return
  // no meio da função pula os useEffect que vêm depois dele só QUANDO `me`
  // ainda é undefined (1º render), e não pula mais assim que `me` resolve (2º
  // render): React vê uma contagem de hooks diferente entre renders ("change
  // in the order of Hooks"), o que derruba o componente inteiro sem
  // ErrorBoundary acima (LangProvider embrulha o <App/> todo em main.tsx) —
  // era a causa da tela branca eterna.
  if (me === undefined) return null;

  async function logout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    setMe(null); setSiteGuilds([]); setPerms(NO_PERMS); setIsDiscordAdmin(false);
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
    setView(_bot ? "management" : "config");
  }

  async function switchGuild(guild: SiteGuild) {
    setGuildDropOpen(false);
    const res = await api.switchGuild(guild.id);
    setGuild(guild.id);
    setMe(prev => prev ? { ...prev, guild_id: guild.id } : prev);
    const [gs, p] = await Promise.all([api.mySiteGuilds(), api.myPermissions()]);
    setSiteGuilds(gs); setPerms(p);
    setView(res.bot_present ? "management" : "config");
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
            <Suspense fallback={null}>
              <ClaimsPanel onBack={() => setUserPanel("main")} />
            </Suspense>
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
                {t("myCharacters")}
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
            {reprocessProgress && reprocessProgress.pending > 0 && <>
              <div className="user-menu-divider" />
              <div className="user-menu-progress">
                {t("reprocessProgress")}
                <div className="user-menu-progress-bar"><div style={{ width: `${reprocessProgress.percent}%` }} /></div>
                {reprocessProgress.percent.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%
              </div>
            </>}
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

  if (eventRoute) {
    content = <EscalacaoPage guildId={eventRoute.guildId} eventId={eventRoute.eventId} />;
  } else if (regearRoute) {
    content = <RegearPage guildId={regearRoute.guildId} initialRequestId={regearRoute.requestId} />;
  } else if (regearEventFilter && me?.guild_id) {
    content = <RegearPage guildId={String(me.guild_id)} eventId={regearEventFilter} />;;
  } else if (battleRoute) {
    content = battleRoute.type === "code"
      ? <BattlePage code={battleRoute.code} onBack={() => navigate("/")} />
      : <BattlePage albionIds={battleRoute.albionIds} onBack={() => navigate("/")} />;
  } else if (playerRoute) {
    content = <PlayerProfilePage region={playerRoute.region} name={playerRoute.name} onBack={() => navigate("/")} />;
  } else if (guildRoute) {
    content = <GuildProfilePage mode={guildRoute.type} albionId={guildRoute.albionId} onBack={() => navigate("/")} />;
  } else if (pickingGuild && loggedIn) {
    content = <GuildPicker onSelect={onGuildSelected} />;
  } else if (!loggedIn && (view === "management" || view === "config")) {
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
  } else if (loggedIn && view === "management" && !hasGuild) {
    content = <GuildPicker onSelect={onGuildSelected} />;
  } else if (view === "dashboard") {
    content = (
      <Dashboard
        onOpenBattles={() => { navigate("/"); setView("battles"); }}
        onOpenHighscores={() => { setHighscoresInitialWindow("week"); navigate("/"); setView("highscores"); }}
      />
    );
  }
  else if (view === "battles") { content = <BattleTracker />; }
  else if (view === "highscores") { content = <HighscoresPage initialWindow={highscoresInitialWindow} />; }
  else if (view === "players")   { content = <PlayerLookup />; }
  else if (view === "craft")     { content = <CraftCalculator />; }
  else if (view === "docs")      { content = <BotDocsPage />; }
  else if (view === "management") { content = <ManagementPage guildId={me!.guild_id!} perms={perms} />; }
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
        <i className="ti ti-lock" /> {t("guildLockedLabel")}
      </span>
    )
  );

  // abas visíveis baseadas em permissões
  const showConfig = loggedIn && hasGuild && perms["guild.admin"];
  // management engloba comps/events/regear/lootlog/reconcile como abas internas
  // (ver ManagementPage) — aparece pra quem tem acesso a qualquer uma delas.
  const showManagement = loggedIn && hasGuild
    && (perms["events.manage"] || perms["comps.view"] || perms["events.view"]);
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
          {nb("highscores", "ti-trophy",      t("highscores"))}
          {nb("craft",   "ti-hammer",         t("craft"))}
          {loggedIn && isDiscordAdmin && nb("docs", "ti-book-2", t("botDocs"))}
        </nav>

        <div className="nav-sep" />

        {showGuildBox && (
          <div className={`nav-guild-box${loggedIn && hasGuild && !hasAnyGuildPerm ? " locked" : (!loggedIn ? " locked" : "")}`}>
            {guildLabel}
            <nav className="nav">
              {(showManagement || (!loggedIn || !hasGuild)) && nb("management", "ti-adjustments-alt", t("management"))}
              {showConfig && nb("config", "ti-settings", t("config"))}
            </nav>
          </div>
        )}

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          {serverQuickSwitch}
          {userMenu}
        </div>
      </div>

      <div style={{ padding: "10px 16px 0" }}>
        <AdBanner size="leaderboard" />
      </div>

      <ErrorBoundary>
      <Suspense fallback={null}>
        {/* useKeepAlive ou deep link ativo → o conteúdo vem dos blocos
            keep-alive abaixo (não do `content`), pra não montar duplicado. */}
        {!useKeepAlive && !escActive && !regearActive && content}
        {loggedIn && hasGuild && (
          <>
            {/* ponytail: a view ativa renderiza na mesma passada
                ((useKeepAlive && view===X)) — sem depender do efeito visitedMc,
                que só roda depois do commit e deixaria um frame em branco na
                primeira visita. visitedMc só mantém a OUTRA montada após sair. */}
            {((useKeepAlive && view === "management") || visitedMc.has("management")) && (
              <div style={useKeepAlive && view === "management" ? undefined : { display: "none" }}>
                <ManagementPage guildId={me!.guild_id!} perms={perms} active={useKeepAlive && view === "management"} />
              </div>
            )}
            {((useKeepAlive && view === "config") || visitedMc.has("config")) && (
              <div style={useKeepAlive && view === "config" ? undefined : { display: "none" }}>
                <GuildConfig guildId={me!.guild_id!} onSwitch={() => setPickingGuild(true)} active={useKeepAlive && view === "config"} />
              </div>
            )}
          </>
        )}
        {/* Deep link escalacao — keep-alive. Ativo → params da rota direto
            (renderiza já, sem esperar escState); inativo mas já visitado →
            params memorizados em escState (fica display:none, sem refetch). */}
        {(escActive || escState) && (
          <div style={escActive ? undefined : { display: "none" }}>
            <EscalacaoPage
              guildId={escActive ? eventRoute!.guildId : escState!.guildId}
              eventId={escActive ? eventRoute!.eventId : escState!.eventId}
              active={escActive} />
          </div>
        )}
        {/* Deep link regear (request OU filtro por evento) — mesmo esquema. */}
        {(regearActive || regearState) && (
          <div style={regearActive ? undefined : { display: "none" }}>
            <RegearPage
              guildId={regearActive ? regearActiveParams!.guildId : regearState!.guildId}
              initialRequestId={regearActive ? regearActiveParams!.initialRequestId : regearState!.initialRequestId}
              eventId={regearActive ? regearActiveParams!.eventId : regearState!.eventId}
              active={regearActive} />
          </div>
        )}
      </Suspense>
      </ErrorBoundary>
    </>
  );
}
