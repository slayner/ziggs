import { useEffect, useState } from "react";

// ponytail: router manual (poucas rotas, todas resolvidas aqui); trocar por
// react-router se o app crescer pra ter navegação aninhada/genérica.

export function navigate(path: string) {
  if (window.location.pathname + window.location.search === path) return;
  // depth = quantas navegações in-app existem ANTES desta entrada. Vive no
  // history.state (não numa variável) pra sobreviver a back/forward do
  // navegador sem dessincronizar.
  history.pushState({ depth: (history.state?.depth ?? 0) + 1 }, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

// Troca a URL sem empilhar no histórico — usado pra "limpar" a barra de
// endereço depois de resolver um ID cru do Albion pro nosso código curto.
// Preserva o state (depth) da entrada atual.
export function navigateReplace(path: string) {
  history.replaceState(history.state, "", path);
}

// Botão "voltar" das páginas: volta pra entrada anterior quando ela é do
// próprio app (perfil aberto a partir de uma batalha volta pra batalha);
// entrada inicial (deep link, aba nova) cai pra home.
export function goBack() {
  if (history.state?.depth > 0) history.back();
  else navigate("/");
}

export function useLocation(): string {
  const [loc, setLoc] = useState(() => window.location.pathname + window.location.search);
  useEffect(() => {
    const onPop = () => setLoc(window.location.pathname + window.location.search);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return loc;
}

export type BattleRoute =
  | { type: "code"; code: string }
  | { type: "raw"; albionIds: string[] };

const CODE_RE = /^\/([a-z0-9]{7})$/;
const CSV_IDS_RE = /^\/(\d+(?:,\d+)*)$/;

// Aceita os 3 formatos de link de batalha que o site reconhece:
//   /k3j9xq2                                  -> nosso código curto
//   /1403004863  ou  /1402994899,1403004863   -> ID(s) crus do Albion
//   /multi?ids=1402994899,1403004863          -> mesma coisa, formato "multi"
export function parseBattleRoute(loc: string): BattleRoute | null {
  const [path, search] = loc.split("?");

  if (path === "/multi") {
    const ids = new URLSearchParams(search ?? "").get("ids");
    if (!ids) return null;
    const albionIds = ids.split(",").map(s => s.trim()).filter(Boolean);
    return albionIds.length ? { type: "raw", albionIds } : null;
  }

  const csv = path.match(CSV_IDS_RE);
  if (csv) return { type: "raw", albionIds: csv[1].split(",") };

  const code = path.match(CODE_RE);
  if (code) return { type: "code", code: code[1] };

  return null;
}

export interface PlayerRoute { region: string; name: string }
export interface GuildRoute { type: "guild" | "alliance"; albionId: string }

const PLAYER_PREFIXES: Record<string, string> = { am: "americas", as: "asia", eu: "europe" };
const PLAYER_RE = /^\/(am|as|eu)\/([^/]+)$/;
const GUILD_RE = /^\/(guild|alliance)\/([^/]+)$/;

export function parseGuildRoute(loc: string): GuildRoute | null {
  const [path] = loc.split("?");
  const m = path.match(GUILD_RE);
  if (!m) return null;
  return { type: m[1] as "guild" | "alliance", albionId: decodeURIComponent(m[2]) };
}

// Link de perfil de jogador, prefixado pela região (nomes não são únicos
// entre Americas/Europe/Asia — são servidores separados): /am/Slayner,
// /as/Slayner e /eu/Slayner podem ser 3 jogadores diferentes.
export function parsePlayerRoute(loc: string): PlayerRoute | null {
  const [path] = loc.split("?");
  const m = path.match(PLAYER_RE);
  if (!m) return null;
  return { region: PLAYER_PREFIXES[m[1]], name: decodeURIComponent(m[2]) };
}

// Página de escalação de um evento (deep link): /events/{guildId}/{eventId}[/escalation].
// URLs são sempre em inglês. Aceita também os caminhos antigos em PT
// (/eventos/.../escalacao) pra não quebrar embeds já enviados no Discord.
// guildId/eventId vêm na URL (não da guilda corrente) pra funcionar pra quem acabou
// de logar via OAuth e ainda não selecionou a guilda no site.
// ponytail: guildId vira string, NÃO Number — snowflake do Discord (> 2^53)
// perde precisão em float64 e manda id truncado na URL (causa 403/404 na escalação).
export interface EventRoute { guildId: string; eventId: number }
const EVENT_RE = /^\/(?:eventos|events)\/(\d+)\/(\d+)(?:\/(?:escalacao|escalation))?$/;
export function parseEventRoute(loc: string): EventRoute | null {
  const [path] = loc.split("?");
  const m = path.match(EVENT_RE);
  if (!m) return null;
  return { guildId: m[1], eventId: Number(m[2]) };
}

// Filtro de regears por evento (deep link do review): /regears?event={eventId}.
// Sem guildId na URL — usa a guilda corrente do cookie. Retorna só o eventId.
export function parseRegearEventFilter(loc: string): number | null {
  const [path, search] = loc.split("?");
  if (path !== "/regears" && path !== "/regear") return null;
  const ev = new URLSearchParams(search ?? "").get("event");
  const n = ev ? Number(ev) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

// Deep link do bot-v2 pra um pedido de regear: /regear/{guildId}/{requestId}.
// guildId vira string (snowflake do Discord, > 2^53 — ver EventRoute).
export interface RegearRoute { guildId: string; requestId: number }
const REGEAR_RE = /^\/regears?\/(\d+)\/(\d+)$/;
export function parseRegearRoute(loc: string): RegearRoute | null {
  const [path] = loc.split("?");
  const m = path.match(REGEAR_RE);
  if (!m) return null;
  return { guildId: m[1], requestId: Number(m[2]) };
}
