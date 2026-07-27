import { useEffect, useState, useCallback, useRef } from "react";
import type { ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useT, useLang, LANG_LABELS, LANG_FULL, type Lang, type LangPref } from "./i18n";

// Espelha o CompanionConfig do Rust (src-tauri/src/config.rs). Nome do
// personagem, região e URL do backend não estão aqui: são detectados
// automaticamente / hardcoded no binário. battles e prices são sempre ligados
// (razão de ser do companion) — sem toggle.
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
  /// Somado na leitura pelo get_sniff_stats (Rust) — alimenta o badge vivo
  /// da aba Damage sem precisar de um poll de get_damage_meter no App.
  damage_total: number;
  /// Dano causado PELO PRÓPRIO jogador (player_name) — badge da aba Damage.
  /// Somado na leitura no Rust (get_sniff_stats), igual ao damage_total.
  my_damage: number;
  /// Estimativa ILUSTRATIVA do valor em prata dos loots da sessão. Calculada
  /// por um worker de fundo no Rust (poll da rota /silver-estimate). Só
  /// badge da aba Lootlog — não é load-bearing em payout/reconcile.
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

/// Palavra de tier por idioma, índice = tier. O nome que vem do dump repete o
/// tier por extenso ("Elder's Guardian Boots") e no terminal isso é ruído —
/// o número já diz. EN põe na frente, PT/ES no fim.
///
/// Tabela na mão de propósito: dá pra DERIVAR do dump comparando os tiers de
/// cada item, mas aí "Raw Beef" (cujo nome muda inteiro por tier, não só o
/// adjetivo) virava "Raw". Vocabulário de tier do Albion não muda; nome de
/// item novo muda toda semana.
const TIER_WORDS: Record<Lang, string[]> = {
  en: ["", "Beginner's", "Novice's", "Journeyman's", "Adept's", "Expert's", "Master's", "Grandmaster's", "Elder's"],
  pt: ["", "do Calouro", "do Novato", "do Iniciante", "do Adepto", "do Perito", "do Mestre", "do Grão-mestre", "do Ancião"],
  es: ["", "del principiante", "del novato", "del obrero", "del iniciado", "del experto", "del maestro", "del gran maestro", "del anciano"],
};


// ── Brilho respirando (fase 2 do PLANO-DESIGN-COMPANION) ─────────────────────
// Espelho do Panel.tsx do site: wrapper com gate de hover (fade 0.6s) + 2
// radiais dourados respirando, um por canto, fase dessincronizada por delay
// negativo aleatório. Delays em useState de propósito: os cards re-renderizam
// a cada poll de 2s, e sortear no render faria a fase do brilho pular.
const GLOW_PERIOD_S = 7; // igual à duração de dash-glow-breathe no CSS
function CardGlow() {
  const [delays] = useState(() => [Math.random() * GLOW_PERIOD_S, Math.random() * GLOW_PERIOD_S]);
  return (
    <span className="dash-cglowwrap" aria-hidden>
      <span className="dash-cglow dash-cglow-tl" style={{ animationDelay: `-${delays[0].toFixed(2)}s` }} />
      <span className="dash-cglow dash-cglow-br" style={{ animationDelay: `-${delays[1].toFixed(2)}s` }} />
    </span>
  );
}

/// Poll periódico com limpeza — só tira a duplicação dos 3 efeitos iguais.
///
/// **Não** para quando a janela é minimizada, de propósito: o companion passa o
/// jogo inteiro na bandeja e continua trabalhando. Quem decide se é hora de
/// gastar máquina é a ZONA (ver `heavy_work_ok` no Rust), não o estado da
/// janela — dois critérios de pausa só tornariam o comportamento imprevisível.
///
/// `fn` fica num ref pra sempre chamar a versão mais nova sem recriar o timer
/// a cada render.
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

/// Nome do item no idioma da UI, sem a palavra de tier. O backend manda os três
/// porque o idioma vive no localStorage do webview. O tier vai num span próprio.
///
/// Só a EXIBIÇÃO encurta — o CSV continua com o nome completo, que é o que o
/// ao-loot-logger e o site esperam.
function itemName(r: LootRow, lang: Lang, tier?: number): string {
  const full = lang === "pt" ? r.item_name_pt : lang === "es" ? r.item_name_es : r.item_name;
  const word = tier != null ? TIER_WORDS[lang]?.[tier] : undefined;
  if (!word) return full;
  if (full.startsWith(word + " ")) return full.slice(word.length + 1);
  if (full.endsWith(" " + word)) return full.slice(0, -(word.length + 1));
  return full;  // item sem adjetivo de tier (recurso, comida) fica inteiro
}

/// "T4_CAPEITEM_SMUGGLER@3" → `{ label: "4.3", ench: 3 }`.
///
/// Mesma notação que o site já usa nas peças de build ("8.4 Capuz do Asceta").
/// O ".0" fica visível de propósito: no terminal as linhas empilham e largura
/// constante é o que deixa a coluna legível.
function itemTierParts(itemId: string): { label: string; tier: number; ench: number } | null {
  const m = /^T(\d)_/.exec(itemId);
  if (!m) return null;  // IDX_123 (item novo, fora do dump) e afins
  const tier = Number(m[1]);
  const ench = Number(/@(\d)$/.exec(itemId)?.[1] ?? 0);
  return { label: `${tier}.${ench}`, tier, ench };
}

/// "2026-07-18T23:07:11Z" → "23:07". A data não ajuda num log de sessão, e o
/// segundo só polui. UTC, igual ao CSV — o horário tem que casar quando alguém
/// cruza o terminal com o arquivo.
function shortTime(ts: string | null): string {
  return ts?.slice(11, 16) || "";
}

/// Item numa linha do terminal: `8.4 Guardian Boots`.
///
/// Duas dimensões, duas pistas visuais, porque as duas importam e competiriam
/// se usassem o mesmo canal: a COR do texto é o tier, o SUBLINHADO é o
/// encantamento. Item sem encantamento não ganha sublinhado — assim o .1+ pula
/// aos olhos em vez de todo mundo ficar riscado.
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

/// Nome do feitiço no idioma da UI. Metade do dump não tem tradução (são
/// sub-feitiços internos tipo AIR_RAID_BOLTS_DAMAGE), então cai no inglês.
function skillName(sk: SkillRow, lang: Lang): string | null {
  if (lang === "pt") return sk.name_pt ?? sk.name;
  if (lang === "es") return sk.name_es ?? sk.name;
  return sk.name;
}

/// Ataque básico não tem arte própria no jogo, então reaproveitamos ícone de
/// habilidade — um por tipo de ataque, pra dar pra distinguir de relance.
/// Os ids vêm do dump; as URLs por nome localizado (`?locale=en`) devolvem
/// exatamente as mesmas imagens, mas quebrariam se a Albion renomeasse.
const AUTO_ATTACK_ICON = {
  melee: "PASSIVE_KNOCKBACKCHANCE",          // "Forceful Bolts"
  ranged: "SPEEDSHOT2",                      // "Speed Shot"
  magic: "PASSIVE_ATTACKBUFF_ARCANESTAFF",   // "Lingering Power"
};

const MELEE_FAMS = new Set([
  "sword", "axe", "mace", "hammer", "quarterstaff", "spear", "dagger", "knuckles",
]);
const RANGED_FAMS = new Set(["bow", "crossbow"]);

/// Tipo de ataque básico a partir da família da arma. O resto das famílias é
/// cajado (fogo/gelo/arcano/maldito/sagrado/natureza/shapeshifter), todas
/// mágicas — inclusive o de shapeshifter, que ataca à distância na forma base.
/// Família nova cai em "mágico" pelo fallback: se for corpo a corpo ou à
/// distância física, acrescente no set certo.
function autoAttackKind(weapon: string | null): keyof typeof AUTO_ATTACK_ICON | null {
  if (!weapon) return null;
  if (MELEE_FAMS.has(weapon)) return "melee";
  if (RANGED_FAMS.has(weapon)) return "ranged";
  return "magic";
}

/// Ícone do feitiço — servido pelo NOSSO backend, não pela CDN da Albion.
/// `/render/spell/{id}` baixa da Albion na primeira vez, salva em disco e
/// depois serve local: carrega mais rápido e para de martelar a CDN deles.
/// Mesmo esquema que `/render/item/` já usa no site.
function SkillIcon({ uniqueName, apiBase, gray }: {
  uniqueName: string | null; apiBase: string; gray?: boolean;
}) {
  if (!uniqueName) return null;
  return (
    <img
      className={`dmg-skill-icon${gray ? " gray" : ""}`}
      // `v=2` fura o cache do webview: o render é servido com max-age de um
      // ano + immutable, então quem já baixou a moldura branca antes da
      // correção do proxy nunca mais pediria de novo. Bump se acontecer outra
      // vez — é só um cache-buster, o backend ignora.
      src={`${apiBase}/render/spell/${encodeURIComponent(uniqueName)}?v=2`}
      alt=""
      loading="lazy"
      // Sem rede (ou feitiço sem arte) some em vez de deixar ícone quebrado.
      onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
    />
  );
}
type DamageRow = {
  name: string; weapon: string | null;
  damage: number; dps: number; skills: SkillRow[]; timeline: number[];
};

/// Famílias de arma do Albion (`@shopsubcategory1` do dump). O rótulo é curto
/// de propósito: fica antes do nome numa linha já apertada.
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
  // ── Abas VIVAS (jul/2026, PLANO-ABAS-VIVAS.md): Rota/Túnel é o foco e a
  // default; Damage e Lootlog completos têm abas próprias. Cada aba carrega
  // seu número ao vivo MESMO SEM FOCO — por isso os dados dos badges vêm todos
  // de estado do App() (tunnelStatus, sniffStats.damage_total/loot_count),
  // nunca de componente de aba, que desmonta. Config continua modal no ⚙.
  const [gearOpen, setGearOpen] = useState(false);
  // Detalhes das conexões locais analisadas pelo failover do túnel.
  const [routesOpen, setRoutesOpen] = useState(false);
  const [tab, setTab] = useState<"route" | "damage" | "loot">("route");
  const [pending, setPending] = useState(0);
  // Filtros do Damage Meter vivem AQUI, não dentro de DamageTab: a aba
  // desmonta ao perder o foco (mesmo motivo dos badges acima), e um botão
  // "ligado" que volta desligado ao trocar de aba e voltar é exatamente esse
  // bug — useState local nunca sobrevive ao unmount.
  const [dmgPartyOnly, setDmgPartyOnly] = useState(false);
  const [dmgVsPlayers, setDmgVsPlayers] = useState(false);
  // Tutorial do Npcap: reaparece a cada sessão enquanto o Npcap seguir
  // ausente (não é "só na primeira vez de verdade" — é "toda vez que o app
  // abre e ainda falta o Npcap"), mas só uma vez por sessão depois de
  // dispensado. O banner compacto na sidebar continua como lembrete.
  const [npcapTutorialDismissed, setNpcapTutorialDismissed] = useState(false);
  // Histórico do gráfico túnel×direto — vive AQUI (não no TunnelHero) pra
  // sobreviver à troca de aba. 120 amostras × 5s = 10 min.
  const [hist, setHist] = useState<{ d: number | null; tn: number | null }[]>([]);
  // Relógio de sessão (tick de 1s) + taxa de pacotes derivada do delta entre
  // polls — o Rust não expõe taxa, só o acumulado.
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
    return () => { unlisten?.(); unlistenUpd?.(); };
  }, [refreshConfig]);

  const updateConfig = async (key: keyof CompanionConfig, value: unknown) => {
    await invoke("set_config", { key, value: value as never });
    await refreshConfig();
  };

  // Poll: sniffer stats + tunnel status. Scanner/stats de coleta não são
  // exibidos — operam em background sem notificar o usuário.
  usePoll(async () => {
    try {
      const st = await invoke<SniffStats>("get_sniff_stats");
      setSniffStats(st);
      const now = Date.now();
      if (prevPkt.current && now > prevPkt.current.t) {
        setPktRate(Math.max(0, (st.packets_captured - prevPkt.current.n) / ((now - prevPkt.current.t) / 1000)));
      }
      prevPkt.current = { n: st.packets_captured, t: now };
    } catch { /* sniffer indisponível */ }
    try {
      const ts = await invoke<TunnelStatus>("tunnel_status");
      setTunnelStatus(ts);
      setHist(h => [...h.slice(-119), { d: ts.direct_latency_ms, tn: ts.tunnel_latency_ms }]);
    } catch { /* tunnel indisponível */ }
  }, 5000);

  usePoll(async () => {
    try { setPending(await invoke<number>("pending_count")); } catch { /* sem fila */ }
  }, 15000);

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

  const npcapMissing = !!(sniffStats?.error && /npcap/i.test(sniffStats.error));
  const playerName = sniffStats?.player_name || "";
  const zone = sniffStats?.last_zone || "unknown";
  const up = Math.max(0, Math.floor((nowTs - sessionStart.current) / 1000));
  const uptime = `${String(Math.floor(up / 3600)).padStart(2, "0")}:${String(Math.floor((up % 3600) / 60)).padStart(2, "0")}:${String(up % 60).padStart(2, "0")}`;

  // Feature da aba ativa desligada → volta pra Route. Impede de ficar preso
  // numa aba que o usuário não pode inspecionar (SideTab desabilitada).
  if (tab === "damage" && !config.collect_damage_meter) setTab("route");
  if (tab === "loot" && !config.collect_auto_lootlog) setTab("route");

  return (
    <div className="ck-root">
      <header
        className="ck-bar"
        onMouseDown={(e) => {
          // Só inicia drag em clique primário (botão esquerdo) e não em
          // elementos interativos (botões de janela). Tauri 2: a API
          // programática é mais confiável que data-tauri-drag-region em React
          // (que renderiza como data-tauri-drag-region="true", não vazio).
          if (e.button !== 0 || (e.target as HTMLElement).closest("button") != null) return;
          // Duplo-clique manual: `startDragging()` faz ReleaseCapture() e
          // entrega o mouse pro window manager do SO — o browser nunca vê o
          // mouseup/click completo, então o evento `dblclick` do DOM não
          // dispara de forma confiável depois disso. Detecta o 2º clique
          // pelo tempo entre mousedowns em vez de depender do dblclick.
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

      {/* Shell: sidebar vertical à esquerda + conteúdo à direita. As abas
          vivem na sidebar, com toggle on/off e detalhe expandido quando a
          feature está ativa — independente de qual aba está selecionada.
          O rodapé da sidebar carrega o indicador de jogador/mapa/Albion e
          o botão de config. */}
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

          {/* 2 ads verticais abaixo das abas — área monetizada da sidebar.
              Cobrem o custo da VPS do túnel junto com o ad strip de cada aba.
              Largura da sidebar já acomoda 300px sem mexer no grid (220px de
              sidebar + 300px de ad = overflow lateral visível só quando o
              criativo carrega; o placeholder cabe colado na borda). */}
          <div className="ck-side-ads">
            <AdSlot variant="side" />
            <AdSlot variant="side" />
          </div>

          {/* Banner Npcap ausente — dentro da sidebar, entre as abas e o
              rodapé. Instalação manual de propósito (ver CLAUDE.md). */}
          {npcapMissing && (
            <div className="ck-npcap ck-side-npcap">
              <span>{t("npcapNeeded")}</span>
              <button className="btn small" onClick={() => invoke("open_npcap_download")}>
                {t("npcapInstall")}
              </button>
              <span className="ck-npcap-hint">{t("npcapHint")}</span>
            </div>
          )}

          {/* Rodapé da sidebar: indicador de jogador/mapa/Albion + config.
              Sempre visível, independente da aba selecionada. */}
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

      {/* Tutorial do Npcap: aparece toda vez que o app abre e o Npcap ainda
          não está instalado (não é um flag "primeira vez" persistido — é
          reavaliado a cada sessão a partir do erro real do sniffer). Some ao
          dispensar; o banner compacto na sidebar (acima) continua lembrando. */}
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


// ─── Sidebar vertical ──────────────────────────────────────────────────────

/// Aba da sidebar vertical. Quando a feature está `on` (toggle ligado ou, no
/// caso da Rota, túnel configurado), a aba cresce e mostra `expandedContent`
/// com detalhes pertinentes — **independente de qual aba está selecionada**.
/// O badge `value` atualiza ao vivo (dado vem do estado do App, nunca daqui).
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
      // Não usa `disabled` no <button>: um button disabled não propaga cliques
      // pros filhos, e o toggle PRECISA ser clicável mesmo com a feature off
      // (senão liga mas não desliga). Em vez disso, o onClick do botão ignora
      // quando não inspectable — só o toggle (div filho) age.
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
/// Número curto: `850`, `1.2K`, `830K`, `1.2M`, `12M`.
///
/// A casa decimal só aparece enquanto vale alguma coisa. Com mantissa < 10 ela
/// informa (1.2K é 20% mais que 1K); a partir de 10 vira ruído (12.3K é 2,5%
/// mais que 12K) e custa 2 caracteres.
///
/// Caractere importa porque o copy do ranking vai pro chat da party, que tem
/// limite. Era `830.0K`/`1.24M` em tudo — só de `.0` eram 2 chars por linha.
function fmtC(n: number): string {
  const curto = (v: number, sufixo: string) => {
    // Arredonda ANTES de decidir a casa decimal. Decidir olhando o valor cru
    // fazia 9.99K sair como "10.0K" — justamente o `.0` que queremos sumir.
    const umaCasa = Math.round(v * 10) / 10;
    const txt = umaCasa >= 10 ? String(Math.round(v)) : umaCasa.toFixed(1);
    // ".0" não informa nada em escala nenhuma: 1.0K é 1K, 1.0M é 1M. Sai
    // sempre, não só acima de 10K.
    return `${txt.replace(/\.0$/, "")}${sufixo}`;
  };
  // 999_500 já arredondaria pra "1000K": sobe pra M antes de chegar nisso.
  if (n >= 999_500) return curto(n / 1e6, "M");
  if (n >= 1e3) return curto(n / 1e3, "K");
  return String(Math.round(n));
}

/// Número por extenso com separador de milhar (1.234.567). Usado nos badges
/// onde o valor EXATO importa mais que a compactação — dano total da sessão
/// e contagem de loots. `fmtC` esconde ordem de grandeza; aqui o usuário quer
/// conferir o número cheio contra o site.
function fmtFull(n: number): string {
  return Math.round(n).toLocaleString("pt-BR");
}

/// Timeline dos últimos 3 min de um jogador: uma barra por segundo, altura
/// proporcional ao pico dele. preserveAspectRatio="none" deixa o SVG esticar
/// na largura da linha sem deformar a altura das barras.
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
        {/* Key = SEGUNDO ABSOLUTO da barra, não a posição. O backend alinha
            data[last] = agora, então o segundo de i é nowSec-(last-i). Keyar
            pelo tempo faz a mesma barra sobreviver ao poll e DESLIZAR pra
            esquerda (transition em x) em vez de reusar o rect da posição i e
            animar a altura no lugar (o "crescimento" indesejado). */}
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
  // `vsPlayers` NÃO é um filtro de linha como os outros: o backend mantém um
  // acumulador separado, porque o alvo do golpe não existe mais depois que o
  // dano é somado por causer. Por isso entra na dependência do poll.
  usePoll(async () => {
    if (!on) return;
    try {
      setRows(await invoke<DamageRow[]>("get_damage_meter", { vsPlayers }));
    } catch { /* sniffer indisponível */ }
  }, 2000, [on, vsPlayers]);

  // Filtro por party: membros do grupo + o próprio jogador.
  const partySet = new Set([
    ...(sniffStats?.party_members ?? []),
    ...(sniffStats?.player_name ? [sniffStats.player_name] : []),
  ]);

  // FONTE ÚNICA da lista: a tela e o copy leem daqui, e é isso que garante que
  // colar reproduza exatamente o que está aparecendo. Se algum dos dois voltar
  // a ler `rows`, o copy passa a vazar linha filtrada sem ninguém perceber.
  //
  // `vsPlayers` não entra aqui de propósito — ele age antes, escolhendo qual
  // acumulador o backend lê, então `rows` já chega restrito.
  const filtered = rows.filter(r => !partyOnly || partySet.has(r.name));

  const max = filtered.reduce((m, r) => Math.max(m, r.damage), 0) || 1;
  const totalDmg = filtered.reduce((s, r) => s + r.damage, 0) || 1;

  // Export em texto: jogador, dano e %. Colar no Discord tem que ser legível
  // no celular — mapa, cura e DPS ficam de fora. Copia `filtered`, ou seja
  // exatamente as linhas visíveis, com a mesma numeração da tela.
  const copyMeter = async () => {
    // Sem '#': no chat do Albion ele é caractere de comando e engolia a linha.
    // '%' do total FILTRADO — bate com o que está na tela, não com a sessão toda.
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
          {/* Herói do cockpit: o número que se olha no meio do ZvZ. */}
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
                        {/* classe INFERIDA pelas skills (ver get_damage_meter);
                            nbsp segura a altura de quem ainda não tem arma */}
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
                                {/* Barra de fundo = fatia desta skill no dano do jogador. */}
                                <span className="dmg-skill-bar" style={{ width: `${sk.pct}%` }} />
                                <span className="dmg-skill-label">
                                  {sk.id >= 0 ? (
                                    <SkillIcon uniqueName={sk.icon ?? sk.unique_name} apiBase={config.api_base_url} />
                                  ) : (
                                    // Auto attack: o ícone sai do tipo de arma
                                    // da LINHA, já que a skill não tem entrada
                                    // no dump. Sem arma inferida, sem ícone.
                                    (() => {
                                      const kind = autoAttackKind(r.weapon);
                                      // Todos em preto e branco: são ícones de
                                      // habilidade reaproveitados, e a falta de
                                      // cor é o que separa ataque básico de
                                      // skill de verdade na lista.
                                      return kind && (
                                        <SkillIcon uniqueName={AUTO_ATTACK_ICON[kind]} apiBase={config.api_base_url} gray />
                                      );
                                    })()
                                  )}
                                  {sk.id < 0
                                    ? t("dmgAutoAttack")
                                    : skillName(sk, lang) || t("dmgSkillN", { id: sk.id })}
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
    } catch { /* sniffer não rodando */ }
  }, 2000);

  // Auto-scroll pro fim do terminal quando chega conteúdo novo.
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

  // O auto-submit vive no worker do Rust (auto_lootlog_worker), que dispara
  // quando o evento entra em REVISÃO. Antes existia aqui um efeito que
  // reenviava a cada loot novo — mandava log pela metade dezenas de vezes por
  // CTA e sobrescrevia a submissão anterior a cada vez.

  const onTerminalScroll = () => {
    if (!terminalRef.current) return;
    const el = terminalRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    setAutoScroll(atBottom);
  };

  // Lootlog desligado (toggle na sidebar): não captura nada. O usuário liga
  // pela aba vertical da sidebar — aqui só mostramos o estado.
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
          {/* A explicação longa virou tooltip — ver o comentário do toolbar. */}
          <h2 title={t("lootlogDesc")}>{t("capturedLoot")}</h2>
        </div>

        {/* Tudo num quadrante só: os três cards antigos gastavam metade da
            aba com texto que se lê uma vez. O que sobrou de explicação está
            no `title` de cada controle. */}
        <div className="loot-toolbar">
          {/* Mesmo padrão dos chips do Damage Meter: um botão-toggle só,
              sem par (botão de ação + switch separado) fazendo a mesma
              coisa duas vezes. */}
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
              {/* 1× é o caso comum e não informa nada — só polui a linha. */}
              {r.quantity > 1 && <span className="t-qty">{r.quantity}× </span>}
              <LootItem row={r} lang={lang} />{" "}
              <span className="t-from">{t("from")}</span>{" "}
              <span className="t-source">{r.looted_from}</span>
            </div>
          ))}
          {/* Resultado do download também vira linha de log: sem os cards, não
              há mais onde pendurar um aviso solto. */}
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

// ─── Hero: Rota/Túnel (tela principal do cockpit) ────────────────────────────

/// Slot de anúncio — placeholder hachurado, mesma linguagem do site. Fica
/// pronto pro criativo; nada de rede de ads embutida por enquanto.
function AdSlot({ variant = "strip" }: { variant?: "strip" | "side" } = {}) {
  const t = useT();
  // `side`: ad vertical pra sidebar (abaixo das abas). 300×250 ou 160×600.
  // `strip`: ad horizontal 728×90 no rodapé das abas.
  return (
    <div className={`ck-ad ck-ad-${variant}`}>
      <span className="ck-ad-tag">{t("ckAd")}</span>
      <span className="ck-ad-ph">{variant === "side" ? "300 × 250" : "728 × 90"}</span>
    </div>
  );
}

/// Painel "Conexão" da aba Rota — detalhe operacional do túnel (VPS, tráfego,
/// split, fallback, erro). Os campos de configuração da VPS (endpoint, keys)
/// são definidos por hardcode no binário, não expostos ao usuário.
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

/* DamageRail (mini-damage do rail) morreu com as abas vivas: o badge da aba
   Damage mostra o total ao vivo, e o meter completo é a própria aba. */

// ─── Multi-internet: conexões locais que alimentam a mesma VPS ──────────────

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

/// A tela principal: rota/túnel estilo ExitLag. SEM número inventado — tudo
/// vem do tunnel_status real; o que não medimos (per-leg, jitter) não aparece.
/// Sem VPS configurada, renderiza o MESMO esqueleto em modo "aguardando",
/// com o botão levando pro modal de config.
function TunnelHero({ config, tunnelStatus, hist, onOpenRoutes }: {
  config: CompanionConfig;
  tunnelStatus: TunnelStatus | null;
  // Acumulado no App (poll de 5s), não aqui: estado local zerava a cada troca
  // de aba, e 10 min de histórico é justamente o valor do gráfico.
  hist: { d: number | null; tn: number | null }[];
  // Abre o modal de multi-rotas. Vive no App() pra sobreviver à troca de aba.
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
    if (!configured) return; // config mora no ConnPanel abaixo
    if (running) { await invoke("tunnel_stop"); setRunning(false); }
    else { await invoke("tunnel_start"); setRunning(true); }
  };

  // Polylines do gráfico: escala pelo maior valor visto (mín. 60ms pra não
  // ampliar ruído de rede boa).
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
        {/* height 140: com o rail limpo o gráfico é o corpo do hero.
            preserveAspectRatio="none" estica o viewBox de 90 sem mexer
            na matemática do line().
            hasData: sem VPS o hist enche de amostras nulas — só contar
            length deixava uma caixa vazia no lugar do estado "medindo". */}
        {hist.length < 2 || !hist.some(h => h.d != null || h.tn != null) ? (
          <div className="empty-inline">{t("ckChartEmpty")}</div>
        ) : (
          <svg viewBox="0 0 600 90" width="100%" height="140" preserveAspectRatio="none">
            <polyline fill="none" stroke="#3a3f4d" strokeWidth="1.5" points={line(h => h.d)} />
            <polyline fill="none" stroke="var(--green)" strokeWidth="2" points={line(h => h.tn)} />
          </svg>
        )}
      </div>

      {/* Métricas (tráfego/split/fallback/erro) moram no ConnPanel abaixo —
          o hero é só o que se olha: latência, hops, gráfico. O ad fica no
          fim da .ck-route-col (um só, igual às outras abas). */}
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

      {/* Calibração do índice de feitiço saiu da UI de propósito: é ajuste
          NOSSO (feito num patch do jogo, via config.json), não do usuário —
          exposto, virava campo misterioso que quebrava todos os nomes de
          skill com um typo. O campo continua no config e no set_config. */}

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

// ─── Discord (botão no topo da sidebar, ao lado da logo) ─────────────────────

/// Marca oficial do Discord (path do simple-icons, viewBox 24×24).
///
/// O anterior era uma reconstrução à mão, com arco malformado (`0 0 0-11-.0`)
/// e proporções erradas. Logo de marca não se desenha de memória: se precisar
/// mexer, troque pelo asset oficial inteiro em vez de ajustar coordenada.
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
