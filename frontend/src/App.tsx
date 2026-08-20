import { useEffect, useRef, useState, lazy, Suspense, Component, type ReactNode } from "react";
import { useLocation, navigate, navigateReplace, goBack, parseBattleRoute, parsePlayerRoute, parseGuildRoute, parseEventRoute, parsePublicEventRoute, parseRegearRoute, parseRegearEventFilter } from "./router";
import { api, setGuild, onBackendDown, setBackendDown, NO_PERMS, type Me, type Permissions, type SiteGuild } from "./api";
import { useLang, useT, useServer, LANG_LABELS, LANG_FULL, SERVER_LABELS, SERVER_FULL, REGION_LABELS, type Lang, type GameServer } from "./i18n";
import AdBanner from "./components/AdBanner";
import CookieConsent from "./components/CookieConsent";
import { TermsPage, PrivacyPage, CookiesPage, AboutPage, ContactPage } from "./components/LegalPages";

// code-split por página: cada view só baixa seu próprio JS quando é aberta
// pela primeira vez, em vez de tudo (CompBuilder ~2000 linhas incluso) no bundle inicial.
const Dashboard = lazy(() => import("./components/Dashboard"));
const CraftCalculator = lazy(() => import("./components/CraftCalculator"));
const BattleTracker = lazy(() => import("./components/BattleTracker"));
const HighscoresPage = lazy(() => import("./components/HighscoresPage"));
const BattlePage = lazy(() => import("./components/BattlePage"));
const PlayerProfilePage = lazy(() => import("./components/PlayerProfilePage"));
const GuildPicker = lazy(() => import("./components/GuildPicker"));
const GuildConfig = lazy(() => import("./components/GuildConfig"));
const GuildProfilePage = lazy(() => import("./components/GuildProfilePage"));
const EscalacaoPage = lazy(() => import("./components/EscalacaoPage"));
const RegearPage = lazy(() => import("./components/RegearPage"));
const ManagementPage = lazy(() => import("./components/ManagementPage"));
const ClaimsPanel = lazy(() => import("./components/ClaimsPanel"));
const CompanionPage = lazy(() => import("./components/CompanionPage"));
const GuildSetup = lazy(() => import("./components/GuildSetup"));

type PublicView = "dashboard" | "craft" | "battles" | "highscores" | "companion";
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
      // Chunk lazy que sumiu (deploy trocou os hashes com a aba aberta):
      // "Tentar de novo" não resolve — o import rejeitado fica cacheado.
      // Recarrega a página sozinho, com guarda de 30s contra loop de reload.
      const msg = this.state.error.message || "";
      if (/dynamically imported module|Loading chunk|module script failed/i.test(msg)) {
        const last = Number(sessionStorage.getItem("ziggs-chunk-reload") || 0);
        if (Date.now() - last > 30_000) {
          sessionStorage.setItem("ziggs-chunk-reload", String(Date.now()));
          location.reload();
          return null;
        }
      }
      return (
        <div style={{ padding: 24, color: "var(--text)", maxWidth: 720, margin: "0 auto" }}>
          <h2 style={{ color: "var(--gold)" }}>⚠️ Algo quebrou ao renderizar</h2>
          <pre style={{ whiteSpace: "pre-wrap", color: "var(--muted)", fontSize: 12 }}>
            {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack}
          </pre>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => location.reload()}>
              Recarregar página
            </button>
            <button className="btn" onClick={() => this.setState({ error: null })}>
              Tentar de novo
            </button>
          </div>
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

const SERVER_TO_REGION: Record<GameServer, string> = { west: "americas", east: "asia", europe: "europe" };

// Delay aproximado da API do Albion (segundos → texto curto). ~5min num dia
// normal; horas em dia de tráfego alto (a API sobrecarrega e demora a publicar
// batalha). Cor: verde < 15min, amarelo < 2h, vermelho ≥ 2h.
function fmtApiDelay(secs: number): string {
  if (secs < 90) return "~1min";
  if (secs < 3600) return `~${Math.round(secs / 60)}min`;
  const h = secs / 3600;
  return h < 10 ? `~${h.toFixed(1)}h` : `~${Math.round(h)}h`;
}
function apiDelayColor(secs: number): string {
  return secs < 900 ? "var(--success)" : secs < 7200 ? "#e0a23b" : "var(--alert)";
}

// Footer global — links pra páginas legais + aviso de não-afiliação.
function SiteFooter({ t }: { t: (k: import("./i18n").TKey) => string }) {
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <nav className="site-footer-nav">
          <a href="/terms">{t("footerTerms")}</a>
          <span className="site-footer-sep">·</span>
          <a href="/privacy">{t("footerPrivacy")}</a>
          <span className="site-footer-sep">·</span>
          <a href="/cookies">{t("footerCookies")}</a>
          <span className="site-footer-sep">·</span>
          <a href="/about">{t("footerAbout")}</a>
          <span className="site-footer-sep">·</span>
          <a href="/contact">{t("footerContact")}</a>
        </nav>
        <p className="site-footer-disclaimer">{t("footerNotAffiliated")}</p>
      </div>
    </footer>
  );
}

export default function App() {
  const { lang, setLang } = useLang();
  const { server, setServer, servers, toggleServer } = useServer();
  const t = useT();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [view, setView] = useState<View>("dashboard");

  // Deep link pro Highscores a partir do perfil de um jogador: /highscores?
  // kind=gather_wood&player=ID&rank=481&regions=americas. Abre na kind certa,
  // na página certa (calculada do rank) e destaca a linha do jogador. Sem
  // rota própria (view em memória), então os params vivem em state em vez de
  // serem lidos toda vez que o componente re-renderiza.
  const [hsParams, setHsParams] = useState<{ kind: string; player: string; rank: number; regions: string } | null>(null);
  // "Ver todos" no card de ranking semanal do dashboard pula direto pra
  // Highscores já com fama PvP semanal selecionada — HighscoresPage não tem
  // rota própria (view em memória, ver PublicView), então isso é passado
  // como prop em vez de query string.
  const [highscoresInitialWindow, setHighscoresInitialWindow] = useState<"alltime" | "week">("alltime");
  const [siteGuilds, setSiteGuilds] = useState<SiteGuild[]>([]);
  const [perms, setPerms] = useState<Permissions>(NO_PERMS);
  const [pickingGuild, setPickingGuild] = useState(false);
  const [guildDropOpen, setGuildDropOpen] = useState(false);
  const [userDropOpen, setUserDropOpen] = useState(false);
  const [instabilityOpen, setInstabilityOpen] = useState(false);
  // Delay de publicação da API do Albion por região (pro dropdown de servidor).
  const [apiDelay, setApiDelay] = useState<Record<string, { delay_secs: number }>>({});
  useEffect(() => {
    const load = () => fetch("/meta/battle-delay").then(r => (r.ok ? r.json() : {})).then(setApiDelay).catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);
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
  const instabilityRef = useRef<HTMLDivElement>(null);
  const loc = useLocation();

  const battleRoute = parseBattleRoute(loc);
  const playerRoute = parsePlayerRoute(loc);
  const guildRoute  = parseGuildRoute(loc);
  const eventRoute  = parseEventRoute(loc);
  const publicEventRoute = parsePublicEventRoute(loc);
  const regearRoute = parseRegearRoute(loc);
  const regearEventFilter = parseRegearEventFilter(loc);
  // /download (não /companion — esse prefixo é da API do backend em prod).
  const companionActive = loc.split("?")[0] === "/download";
  // só com o footer. Detectadas aqui, não no router.ts, porque são estáticas.
  const legalPath = loc.split("?")[0];
  const legalPage: "terms" | "privacy" | "cookies" | "about" | "contact" | null =
    legalPath === "/terms" ? "terms" :
    legalPath === "/privacy" ? "privacy" :
    legalPath === "/cookies" ? "cookies" :
    legalPath === "/about" ? "about" :
    legalPath === "/contact" ? "contact" : null;
  // /highscores?kind=&player=&rank=&regions= — deep link do perfil. Detecta
  // aqui (não no router.ts) porque highscores é view em memória, não rota de
  // URL própria; o App seta a view e os params, e limpa a URL em seguida pra
  // não ficar presa na barra de endereço.
  const hsDeep = loc.split("?")[0] === "/highscores";

  // ?view=highscores|battles|craft&... — deep links shareable dos filtros.
  // Parseados aqui (durante o render, não em efeito) pra chegarem frescos nos
  // componentes no render em que eles montam (evita lag de 1 render que um
  // useEffect introducing setView+params causaria). O próprio componente
  // reescreve a URL via replaceState quando o filtro muda — replaceState não
  // dispara popstate, então `loc` (e estes params) só mudam em navegação real.
  const _sp = new URLSearchParams(loc.split("?")[1] ?? "");
  const _urlView = _sp.get("view");
  // Narrowing explícito (sempre "guild"|"player"|undefined) — sem isso, o
  // literal do objeto alarga pra string e quebra o prop tipado do componente.
  const _scopeRaw = _sp.get("scope");
  const hsScopeParam: "guild" | "player" | undefined =
    _scopeRaw === "player" ? "player" : _scopeRaw === "guild" ? "guild" : undefined;
  const hsUrlParams = _urlView === "highscores" ? {
    kind: _sp.get("kind") ?? undefined,
    window: _sp.get("window") ?? undefined,
    scope: hsScopeParam,
    page: _sp.get("page") ? Number(_sp.get("page")) : undefined,
    search: _sp.get("search") ?? undefined,
  } : undefined;
  const battlesUrlParams = _urlView === "battles" ? {
    page: _sp.get("page") ? Number(_sp.get("page")) : undefined,
    search: _sp.get("search") ?? undefined,
    minPlayers: _sp.get("min_players") ?? undefined,
    minKills: _sp.get("min_kills") ?? undefined,
    dateFrom: _sp.get("date_from") ?? undefined,
    dateTo: _sp.get("date_to") ?? undefined,
  } : undefined;
  const craftCartCode = _urlView === "craft" ? (_sp.get("cart") ?? undefined) : undefined;

  useEffect(() => {
    const query = loc.split("?")[1];
    if (loc.split("?")[0] !== "/" || !query) return;
    const returnView = new URLSearchParams(query).get("view");
    if (returnView === "management" || returnView === "config") {
      setView(returnView);
      navigateReplace("/");
      return;
    }
    // Views públicas com estado na URL: só setam a view (NÃO limpam a URL — o
    // próprio componente gerencia os query params via replaceState). Os params
    // já foram parseados durante o render (hsUrlParams/battlesUrlParams/...).
    if (returnView === "highscores") setView("highscores");
    else if (returnView === "battles") setView("battles");
    else if (returnView === "craft") setView("craft");
  }, [loc]);

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
      if (m?.guild_id) {
        setGuild(m.guild_id);
      }
      // Sempre carrega guildas e permissões quando logado — mesmo sem
      // guild_id ativa, o usuário pode ser membro de guildas com o bot.
      Promise.all([api.mySiteGuilds(), api.myPermissions()]).then(([gs, p]) => {
        setSiteGuilds(gs);
        setPerms(p);
      });
    });
    return () => clearTimeout(to);
  }, [loc, me]);

  // Deep link /highscores?... vindo do perfil de um jogador — extrai params,
  // seta view highscores e limpa a URL (a view é em memória, não rota).
  useEffect(() => {
    if (!hsDeep) return;
    const sp = new URLSearchParams(loc.split("?")[1] ?? "");
    const kind = sp.get("kind");
    const player = sp.get("player");
    const rank = Number(sp.get("rank"));
    const regions = sp.get("regions");
    if (kind && player && Number.isFinite(rank) && rank > 0) {
      setHsParams({ kind, player, rank, regions: regions ?? "" });
      setView("highscores");
    }
    // Limpa a URL pra não ficar presa (back volta pra home, não re-dispara).
    navigateReplace("/");
  }, [hsDeep, loc]);

  useEffect(() => {
    if (view === "highscores" && hsParams) setHsParams(null);
  }, [view, hsParams]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const node = e.target as Node;
      if (dropRef.current && !dropRef.current.contains(node)) setGuildDropOpen(false);
      if (userRef.current && !userRef.current.contains(node)) {
        setUserDropOpen(false);
        setUserPanel("main");
      }
      if (instabilityRef.current && !instabilityRef.current.contains(node)) setInstabilityOpen(false);
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
  const userGuild     = siteGuilds.find(g => g.id === me?.guild_id) ?? null;
  const hasGuild      = !!me?.guild_id && !!currentGuild;
  const needsSetup    = !!userGuild && (!userGuild.bot_present || !userGuild.albion_guild_name);
  const multiGuild    = botGuilds.length > 1;
  const hasAnyGuildPerm = Object.values(perms).some(Boolean);

  // Keep-alive ativo quando estamos mostrando management ou config direto
  // (sem deep link de rota, sem picker aberto, logado com guilda). Fora disso
  // as duas continuam montadas mas escondidas (display:none), preservando estado.
  const noRoute = !eventRoute && !publicEventRoute && !regearRoute && !regearEventFilter && !battleRoute && !playerRoute && !guildRoute && !companionActive && !legalPage;
  const useKeepAlive = loggedIn && hasGuild && !needsSetup && !pickingGuild && noRoute && (view === "management" || view === "config");
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

  // Título da aba do navegador por view/rota — sem isto, toda aba, favorito e
  // entrada de histórico vira o mesmo "Ziggs — Controle de guildas…".
  useEffect(() => {
    let label = "";
    if (playerRoute) label = playerRoute.name || "";
    else if (battleRoute) label = ""; // BattlePage sets its own title
    document.title = label || "Ziggs";
  }, [battleRoute, playerRoute]);

  // Banner "backend fora do ar": api.ts marca down em falha de rede; aqui
  // mostramos o aviso e fazemos poll de /health até voltar.
  const [backendDown, setBackendDownState] = useState(false);
  useEffect(() => onBackendDown(setBackendDownState), []);
  useEffect(() => {
    if (!backendDown) return;
    const id = setInterval(async () => {
      try {
        const r = await fetch("/health");
        if (r.ok) setBackendDown(false);
      } catch { /* ainda fora — continua tentando */ }
    }, 10_000);
    return () => clearInterval(id);
  }, [backendDown]);

  // Precisa vir DEPOIS de todos os hooks acima (não antes) — um early return
  // no meio da função pula os useEffect que vêm depois dele só QUANDO `me`
  // ainda é undefined (1º render), e não pula mais assim que `me` resolve (2º
  // render): React vê uma contagem de hooks diferente entre renders ("change
  // in the order of Hooks"), o que derruba o componente inteiro sem
  // ErrorBoundary acima (LangProvider embrulha o <App/> todo em main.tsx) —
  // era a causa da tela branca eterna.
  if (me === undefined) return null;

  // Páginas legais/institucionais — standalone, sem topbar/nav. Renderizam
  // antes do fluxo principal (não precisam de login nem guilda).
  if (legalPage) {
    const page = legalPage === "terms" ? <TermsPage /> :
      legalPage === "privacy" ? <PrivacyPage /> :
      legalPage === "cookies" ? <CookiesPage /> :
      legalPage === "about" ? <AboutPage /> : <ContactPage />;
    return (
      <div className="app-shell">
        <div className="dash-root">
          <div className="legal-back-bar">
            <button className="btn" onClick={() => navigate("/")}>
              <i className="ti ti-arrow-left" /> {t("back")}
            </button>
          </div>
          {page}
        </div>
      {!eventRoute && !publicEventRoute && <SiteFooter t={t} />}
      </div>
    );
  }

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

  const URL_VIEWS: Set<string> = new Set(["battles", "highscores", "craft"]);
  const nb = (v: View, icon: string, label: string) => (
    <button className={!battleRoute && !playerRoute && !guildRoute && !eventRoute && !publicEventRoute && !regearRoute && view === v ? "active" : ""} onClick={() => { navigate("/"); setView(v); if (URL_VIEWS.has(v)) navigateReplace(`/?view=${v}`); }}>
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

  const unstableApiRegions = Object.entries(apiDelay).filter(([, status]) => status.delay_secs > 15 * 60);
  const selectedApiUnstable = unstableApiRegions.some(([region]) => region === SERVER_TO_REGION[server]);
  const instabilityHint = unstableApiRegions.length
    ? `${t("siteInstabilityApiDelay")}: ${unstableApiRegions.map(([region, status]) =>
        `${REGION_LABELS[lang][region] ?? region} ${fmtApiDelay(status.delay_secs)}`
      ).join(" · ")}. ${t("apiDelayHint")}`
    : "";

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
                  {(() => {
                    const dd = apiDelay[SERVER_TO_REGION[sv]];
                    return dd ? (
                      <span
                        style={{ marginLeft: "auto", fontSize: 11, color: apiDelayColor(dd.delay_secs), fontVariantNumeric: "tabular-nums" }}
                        title={t("apiDelayHint")}
                      >
                        {fmtApiDelay(dd.delay_secs)}
                      </span>
                    ) : null;
                  })()}
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

  const loginGate = (
    <div className="login-gate">
      <i className="ti ti-lock login-gate-icon" />
      <p className="login-gate-title">{t("guildOnly")}</p>
      <p className="login-gate-sub">{t("loginRequired")}</p>
      <a className="btn btn-discord" href="/auth/discord/login">
        <i className="ti ti-brand-discord" /> {t("loginDiscord")}
      </a>
    </div>
  );

  let content: React.ReactNode;

  if (publicEventRoute) {
    content = <EscalacaoPage token={publicEventRoute.token} />;
  } else if (eventRoute) {
    content = <EscalacaoPage guildId={eventRoute.guildId} eventId={eventRoute.eventId} />;
  } else if (regearRoute) {
    content = <RegearPage guildId={regearRoute.guildId} initialRequestId={regearRoute.requestId} />;
  } else if (regearEventFilter && me?.guild_id) {
    content = <RegearPage guildId={String(me.guild_id)} eventId={regearEventFilter} />;;
  } else if (companionActive || view === "companion") {
    content = <CompanionPage />;
  } else if (battleRoute) {
    content = battleRoute.type === "code"
      ? <BattlePage code={battleRoute.code} onBack={() => navigate("/")} />
      : <BattlePage albionIds={battleRoute.albionIds} onBack={() => navigate("/")} />;
  } else if (playerRoute) {
    content = <PlayerProfilePage region={playerRoute.region} name={playerRoute.name} activityId={playerRoute.activityId} onBack={goBack} />;
  } else if (guildRoute) {
    content = <GuildProfilePage mode={guildRoute.type} albionId={guildRoute.albionId} onBack={goBack} />;
  } else if (pickingGuild && loggedIn) {
    content = view === "management"
      ? <ManagementPage perms={NO_PERMS} empty={<GuildPicker onSelect={onGuildSelected} />} />
      : <GuildPicker onSelect={onGuildSelected} />;
  } else if (!loggedIn && (view === "management" || view === "config")) {
    content = loginGate;
  } else if (loggedIn && needsSetup && (view === "management" || view === "config")) {
    content = (
      <Suspense fallback={null}>
        <GuildSetup
          guildId={me!.guild_id!}
          guildName={userGuild!.name}
          botPresent={userGuild!.bot_present}
          hasAlbionName={!!userGuild!.albion_guild_name}
          onSwitch={() => setPickingGuild(true)}
          onComplete={() => {
            api.mySiteGuilds().then(gs => { setSiteGuilds(gs); setView("config"); });
          }}
        />
      </Suspense>
    );
  } else if (loggedIn && view === "management" && !hasGuild) {
    content = <ManagementPage perms={NO_PERMS} empty={<GuildPicker onSelect={onGuildSelected} />} />;
  } else if (view === "dashboard") {
    content = (
      <Dashboard
        onOpenBattles={() => { setView("battles"); navigateReplace("/?view=battles"); }}
        onOpenHighscores={() => { setHighscoresInitialWindow("week"); setView("highscores"); navigateReplace("/?view=highscores"); }}
      />
    );
  }
  else if (view === "battles") { content = <BattleTracker
    initialPage={battlesUrlParams?.page}
    initialSearch={battlesUrlParams?.search}
    initialMinPlayers={battlesUrlParams?.minPlayers}
    initialMinKills={battlesUrlParams?.minKills}
    initialDateFrom={battlesUrlParams?.dateFrom}
    initialDateTo={battlesUrlParams?.dateTo}
  />; }
  else if (view === "highscores") { content = <HighscoresPage
    initialWindow={(hsUrlParams?.window ?? highscoresInitialWindow) as never}
    initialKind={(hsParams?.kind ?? hsUrlParams?.kind) as never}
    initialScope={hsUrlParams?.scope}
    highlightPlayer={hsParams?.player}
    initialRank={hsParams?.rank}
    initialRegions={hsParams?.regions || undefined}
    initialSearch={hsUrlParams?.search}
    initialPage={hsUrlParams?.page}
  />; }
  else if (view === "craft")     { content = <CraftCalculator initialCartCode={craftCartCode} />; }
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
  // Management aparece pra usuários logados que são membros de pelo menos
  // uma guilda onde o bot tá presente (botGuilds vem de siteGuilds).
  const showManagement = loggedIn && (botGuilds.length > 0 || hasAnyGuildPerm);
  const showGuildBox = showManagement;

  return (
    <div className="app-shell">
      {backendDown && (
        <div className="backend-down-banner" role="alert">
          <i className="ti ti-plug-connected-x" aria-hidden="true" /> {t("backendDown")}
        </div>
      )}
      <div className="topbar">
        <button className="brand" onClick={() => { navigate("/"); setView("dashboard"); }} title={t("dashboard")}>
          <img className="logo" src="/logo.png" alt="Ziggs" />
          Ziggs
        </button>

        <nav className="nav nav-public">
          {nb("battles", "ti-shield-bolt",    t("battles"))}
          {nb("highscores", "ti-trophy",      t("highscores"))}
          {nb("craft", "ti-hammer",          t("craft"))}
          <button className={companionActive ? "active" : ""} onClick={() => navigate("/download")}>
            <i className="ti ti-device-desktop" aria-hidden="true" /> {t("companionNav")}
          </button>
        </nav>

        <div className="nav-sep" />

        {showGuildBox && (
          <div className={`nav-guild-box${loggedIn && hasGuild && !hasAnyGuildPerm ? " locked" : ""}`}>
            {guildLabel}
            <nav className="nav">
              {nb("management", "ti-adjustments-alt", t("management"))}
              {showConfig && nb("config", "ti-settings", t("config"))}
            </nav>
          </div>
        )}

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          {selectedApiUnstable && (
            <div className="topbar-status" ref={instabilityRef}>
              <button type="button" className={`topbar-instability${instabilityOpen ? " active" : ""}`}
                onClick={() => {
                  setInstabilityOpen(open => !open);
                  setUserDropOpen(false); setGuildDropOpen(false);
                }}
                aria-label={instabilityHint} aria-expanded={instabilityOpen} aria-haspopup="menu">
                <i className="ti ti-alert-triangle" aria-hidden="true" />
              </button>
              {instabilityOpen && (
                <div className="status-dropdown" role="menu">
                  <div className="status-dropdown-head">
                    <span className="status-dropdown-icon"><i className="ti ti-alert-triangle" aria-hidden="true" /></span>
                    <span>
                      <strong>{t("systemStatus")}</strong>
                      <small>{t("systemStatusDegraded")}</small>
                    </span>
                  </div>
                  <div className="status-dropdown-list">
                    {unstableApiRegions.map(([region, status]) => (
                      <div className="status-dropdown-row" key={region}>
                        <span className="status-pulse" />
                        <span className="status-region">
                          <strong>{REGION_LABELS[lang][region] ?? region}</strong>
                          <small>{t("apiPublishingDelay")}</small>
                        </span>
                        <span className="status-delay" style={{ color: apiDelayColor(status.delay_secs) }}>
                          {fmtApiDelay(status.delay_secs)}
                        </span>
                      </div>
                    ))}
                  </div>
                  <p className="status-dropdown-note">{t("apiDelayHint")}</p>
                </div>
              )}
            </div>
          )}
          {serverQuickSwitch}
          {publicEventRoute && !loggedIn ? (
            <a className="btn btn-discord" href={`/auth/discord/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`}>
              <i className="ti ti-brand-discord" /> {t("loginDiscord")}
            </a>
          ) : userMenu}
        </div>
      </div>

      {/* WS5: dash-root (grid técnico de fundo do design v2) vive no wrapper
          de conteúdo — TODAS as páginas herdam, só o topbar fica de fora. O ad
          banner entrou pra dentro (era o único elemento entre topbar e
          dash-root, então o quadriculado só começava depois dele, deixando
          --bg liso atrás do próprio ad). O esquadramento em si é global no
          styles.css (não depende disso). */}
      <div className="dash-root">
      {/* Política do AdSense: sem anúncio em telas de navegação/fluxo
          comportamental (seleção de guilda, login, setup inicial) — só em
          páginas de conteúdo. "craft" tem o próprio banner na rail. */}
      {view !== "craft" && !pickingGuild && !(view === "management" && (!loggedIn || needsSetup)) && !(view === "config" && (!loggedIn || needsSetup)) && !eventRoute && !publicEventRoute && (
        <div style={{ padding: "10px 16px 0" }}>
          <AdBanner key={`top-${view}`} slot={`top-${view}`} variant="leaderboard" mobileVariant="mobileBanner" />
        </div>
      )}
      <ErrorBoundary>
      {/* Fallback visível: em conexão lenta, trocar de view carrega um chunk
          novo — com fallback null a tela ficava em branco e parecia travada. */}
      <Suspense fallback={
        <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
          <i className="ti ti-loader-2 spin" style={{ fontSize: 22, color: "var(--muted)" }} aria-hidden="true" />
        </div>
      }>
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
      </div>
      {!eventRoute && !publicEventRoute && <SiteFooter t={t} />}
      <CookieConsent />
    </div>
  );
}
