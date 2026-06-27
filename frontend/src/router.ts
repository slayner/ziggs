import { useEffect, useState } from "react";

// ponytail: router manual (poucas rotas, todas resolvidas aqui); trocar por
// react-router se o app crescer pra ter navegação aninhada/genérica.

export function navigate(path: string) {
  if (window.location.pathname + window.location.search === path) return;
  history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

// Troca a URL sem empilhar no histórico — usado pra "limpar" a barra de
// endereço depois de resolver um ID cru do Albion pro nosso código curto.
export function navigateReplace(path: string) {
  history.replaceState({}, "", path);
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
