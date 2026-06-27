import { createElement, createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import type { AlbionItem } from "../data/albion-items";
import ES_ITEMS from "./es-items.json";

export type Lang = "pt" | "en" | "es";

export const LANG_LABELS: Record<Lang, string>     = { pt: "PT", en: "EN", es: "ES" };
export const LANG_FULL: Record<Lang, string>        = { pt: "Português", en: "English", es: "Español" };

// ── UI strings ────────────────────────────────────────────────
const S = {
  pt: {
    search: "Buscar item…",
    noItems: "Nenhum item encontrado.",
    dashboard: "Início",
    battles: "Batalhas", players: "Jogadores", craft: "Craft",
    comps: "Comps", events: "Eventos", config: "Configurações",
    logout: "Sair", loginDiscord: "Entrar com Discord",
    selectServer: "Selecionar servidor", addServer: "Adicionar servidor",
    guildOnly: "Área exclusiva da guilda",
    loginRequired: "Faça login com o Discord para acessar esta área.",
    remove: "Remover",
    lang: "Idioma", server: "Servidor",
  },
  en: {
    search: "Search item…",
    noItems: "No items found.",
    dashboard: "Home",
    battles: "Battles", players: "Players", craft: "Craft",
    comps: "Comps", events: "Events", config: "Settings",
    logout: "Logout", loginDiscord: "Login with Discord",
    selectServer: "Select server", addServer: "Add server",
    guildOnly: "Guild exclusive area",
    loginRequired: "Login with Discord to access this area.",
    remove: "Remove",
    lang: "Language", server: "Server",
  },
  es: {
    search: "Buscar objeto…",
    noItems: "No se encontraron objetos.",
    dashboard: "Inicio",
    battles: "Batallas", players: "Jugadores", craft: "Craft",
    comps: "Comps", events: "Eventos", config: "Configuración",
    logout: "Salir", loginDiscord: "Entrar con Discord",
    selectServer: "Seleccionar servidor", addServer: "Agregar servidor",
    guildOnly: "Área exclusiva del gremio",
    loginRequired: "Inicia sesión con Discord para acceder a esta área.",
    remove: "Eliminar",
    lang: "Idioma", server: "Servidor",
  },
} as const;

export type TKey = keyof typeof S.pt;

// ── Game server ───────────────────────────────────────────────
export type GameServer = "europe" | "west" | "east";
export const SERVER_LABELS: Record<GameServer, string> = { europe: "EU", west: "AM", east: "AS" };
export const SERVER_FULL: Record<GameServer, string>   = { europe: "Europe", west: "Americas", east: "Asia" };

// ── Combined context ──────────────────────────────────────────
interface AppPrefs {
  lang: Lang; setLang: (l: Lang) => void;
  server: GameServer; setServer: (s: GameServer) => void;
  servers: GameServer[]; toggleServer: (s: GameServer) => void;
}

const LangCtx = createContext<AppPrefs>({
  lang: "pt", setLang: () => {},
  server: "west", setServer: () => {},
  servers: ["west"], toggleServer: () => {},
});

function readServers(): GameServer[] {
  try {
    const stored = JSON.parse(localStorage.getItem("servers") ?? "null");
    if (Array.isArray(stored) && stored.length > 0) return stored as GameServer[];
  } catch { /**/ }
  return ["west"];
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState]       = useState<Lang>(() => (localStorage.getItem("lang") as Lang | null) ?? "pt");
  const [server, setServerState]   = useState<GameServer>(() => (localStorage.getItem("server") as GameServer | null) ?? "west");
  const [servers, setServersState] = useState<GameServer[]>(readServers);

  function setLang(l: Lang) { localStorage.setItem("lang", l); setLangState(l); }

  function setServer(s: GameServer) { localStorage.setItem("server", s); setServerState(s); }

  function toggleServer(s: GameServer) {
    setServersState(prev => {
      const has = prev.includes(s);
      if (has && prev.length === 1) return prev; // can't deselect last
      const next = has ? prev.filter(x => x !== s) : [...prev, s];
      localStorage.setItem("servers", JSON.stringify(next));
      // if active was removed, switch to first remaining
      if (has && server === s) {
        localStorage.setItem("server", next[0]);
        setServerState(next[0]);
      }
      return next;
    });
  }

  return createElement(LangCtx.Provider, { value: { lang, setLang, server, setServer, servers, toggleServer } }, children);
}

export const useLang   = () => useContext(LangCtx);
export const useServer = () => { const { server, setServer, servers, toggleServer } = useContext(LangCtx); return { server, setServer, servers, toggleServer }; };

export function useT() {
  const { lang } = useLang();
  return (key: TKey): string => S[lang][key];
}

// ── Item name resolution ──────────────────────────────────────
function tierDotEnchant(id: string): string {
  const tier    = id.match(/^T(\d+)_/)?.[1] ?? "";
  const enchant = id.match(/@(\d+)$/)?.[1] ?? "0";
  return `${tier}.${enchant}`;
}

function baseIdOf(id: string): string {
  return id.replace(/^T\d+_/, "").replace(/@\d+$/, "");
}

// ponytail: ES item names fall back to PT; populate es-items.json to add them
export function itemLocalName(item: AlbionItem, lang: Lang): string {
  const prefix = tierDotEnchant(item.id);
  if (lang === "en" && item.nameEn) return `${prefix} ${item.nameEn}`;
  if (lang === "es") {
    const base = (ES_ITEMS as Record<string, string>)[baseIdOf(item.id)];
    if (base) return `${prefix} ${base}`;
  }
  return item.name;
}
