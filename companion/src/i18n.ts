import { createContext, createElement, useContext, useState } from "react";
import type { ReactNode } from "react";

export type Lang = "pt" | "en" | "es";
export type LangPref = Lang | "auto";
export const LANG_FULL: Record<Lang, string> = { pt: "Português", en: "English", es: "Español" };

function detectSystemLang(): Lang {
  const prefix = (navigator.language || "en").toLowerCase().split("-")[0];
  return prefix === "pt" || prefix === "es" ? prefix : "en";
}
function readPref(): LangPref {
  const value = localStorage.getItem("ziggs-companion-lang");
  return value === "pt" || value === "en" || value === "es" || value === "auto" ? value : "auto";
}
const LangCtx = createContext({ lang: detectSystemLang(), pref: readPref(), setPref: (_value: LangPref) => {} });
export function LangProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<LangPref>(readPref);
  const [lang, setLang] = useState<Lang>(pref === "auto" ? detectSystemLang() : pref);
  const setPref = (value: LangPref) => { localStorage.setItem("ziggs-companion-lang", value); setPrefState(value); setLang(value === "auto" ? detectSystemLang() : value); };
  return createElement(LangCtx.Provider, { value: { lang, pref, setPref } }, children);
}
export const useLang = () => useContext(LangCtx);

const S = {
  pt: { splashText: "Ziggs Companion", navDamage: "Damage Meter", navConfig: "Configurações", ckPackets: "pacotes", statusLoading: "carregando…", albionClosed: "Albion fechado", dmgOffHint: "O Damage Meter está desligado.", dmgEmptyHint: "Sem dados de combate ainda. Entre numa luta com o Albion aberto.", dmgVsPlayers: "Só jogadores", clearLoot: "limpar", dmgSkillN: "Habilidade {id}", lootlogOffHint: "O Lootlog está desligado.", capturedLoot: "Loot capturado", downloadCsv: "baixar .csv", lootedBy: "pegou", from: "de", language: "Idioma", langAuto: "Automático (seguir o sistema)", autostart: "Iniciar com o sistema", minimizeTray: "Minimizar para a bandeja ao fechar" },
  en: { splashText: "Ziggs Companion", navDamage: "Damage Meter", navConfig: "Settings", ckPackets: "packets", statusLoading: "loading…", albionClosed: "Albion closed", dmgOffHint: "The Damage Meter is off.", dmgEmptyHint: "No combat data yet. Get into a fight with Albion open.", dmgVsPlayers: "Players only", clearLoot: "clear", dmgSkillN: "Skill {id}", lootlogOffHint: "The Lootlog is off.", capturedLoot: "Captured loot", downloadCsv: "download .csv", lootedBy: "looted", from: "from", language: "Language", langAuto: "Automatic (follow system)", autostart: "Start with the system", minimizeTray: "Minimize to tray on close" },
  es: { splashText: "Ziggs Companion", navDamage: "Damage Meter", navConfig: "Configuración", ckPackets: "paquetes", statusLoading: "cargando…", albionClosed: "Albion cerrado", dmgOffHint: "El Damage Meter está desactivado.", dmgEmptyHint: "Sin datos de combate todavía. Entra en una pelea con Albion abierto.", dmgVsPlayers: "Solo jugadores", clearLoot: "limpiar", dmgSkillN: "Habilidad {id}", lootlogOffHint: "El Lootlog está desactivado.", capturedLoot: "Loot capturado", downloadCsv: "descargar .csv", lootedBy: "obtuvo", from: "de", language: "Idioma", langAuto: "Automático (seguir el sistema)", autostart: "Iniciar con el sistema", minimizeTray: "Minimizar a la bandeja al cerrar" },
} as const;
export type TKey = keyof typeof S.pt;
export function useT() { const { lang } = useLang(); return (key: TKey, vars?: Record<string, string | number>): string => { let text: string = S[lang][key] ?? S.en[key]; for (const [name, value] of Object.entries(vars ?? {})) text = text.replaceAll(`{${name}}`, String(value)); return text; }; }
