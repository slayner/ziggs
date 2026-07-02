import { Fragment, useEffect, useState } from "react";
import { api, BOT_INVITE, type DiscordRole, type Permissions, type SiteGuild } from "../api";
import { useLang, useT, REGION_LABELS, LANG_FULL, type Lang, type TKey } from "../i18n";

const ALBION_REGIONS = ["americas", "europe", "asia"] as const;

interface BotCommand { name: string; description: string; category: string; enabled: boolean; allowed_roles: string[]; }
interface AllyGuild { id: string; name: string; }

const EVERYONE = "everyone";
const ADMIN = "admin";
const ALL_ALLIES = "all";
const NO_ALLIES = "none";

// @everyone é exclusivo — nenhum outro cargo (Administrators incluso) pode
// coexistir com ele: selecionar @everyone limpa a lista inteira, e
// selecionar qualquer outro cargo tira @everyone se ele estiver lá.
function toggleRoleKey(prev: string[], key: string): string[] {
  if (key === EVERYONE) return prev.includes(EVERYONE) ? [] : [EVERYONE];
  const specific = prev.filter(k => k !== EVERYONE);
  return specific.includes(key) ? specific.filter(k => k !== key) : [...specific, key];
}

// Comandos cujas configurações dependem do nome da guilda Albion. O input
// dessa configuração é renderizado dentro de cada um (em vez de um quadrante
// separado) e todos compartilham o mesmo estado/endpoint — mudar em um muda
// no resto, porque é o mesmo campo (guild.albion_guild_name) por baixo.
const NEEDS_ALBION_NAME = new Set(["register"]);

const CATEGORY_LABEL_KEYS: Record<string, "cmdCategoryManagement" | "cmdCategoryEconomy" | "cmdCategoryMiscellaneous"> = {
  management: "cmdCategoryManagement",
  economy: "cmdCategoryEconomy",
  miscellaneous: "cmdCategoryMiscellaneous",
};

// Descrição de cada comando vem crua (PT) do backend (COMMANDS_REGISTRY) — o
// site é multilíngue, então a exibição usa a tradução local por nome do
// comando em vez do texto da API, com o texto da API como fallback pra
// comando novo que ainda não ganhou uma entrada aqui.
const CMD_DESC_KEYS: Record<string, TKey> = {
  avatar: "cmdDescAvatar",
  banner: "cmdDescBanner",
  register: "cmdDescRegister",
  unregister: "cmdDescUnregister",
  balance: "cmdDescBalance",
  pay: "cmdDescPay",
  addmoney: "cmdDescAddmoney",
  removemoney: "cmdDescRemovemoney",
  leaderboard: "cmdDescLeaderboard",
  economystats: "cmdDescEconomystats",
  undo: "cmdDescUndo",
};

// Comandos que só existem como dependência de outro (ex.: /unregister só faz
// sentido se /register existir) entram aninhados dentro do pai em vez de
// aparecer como uma linha própria na lista principal.
const SUBCOMMANDS_OF: Record<string, string[]> = { register: ["unregister"] };
const CHILD_COMMANDS = new Set(Object.values(SUBCOMMANDS_OF).flat());

type CmdFilter = "all" | "enabled" | "disabled";

function usePermCols(): { key: keyof Permissions; label: string }[] {
  const t = useT();
  return [
    { key: "events.view",   label: t("permEvView")   },
    { key: "events.create", label: t("permEvCreate") },
    { key: "events.manage", label: t("permEvManage") },
    { key: "comps.view",    label: t("permCoView")   },
    { key: "comps.create",  label: t("permCoCreate") },
    { key: "comps.manage",  label: t("permCoManage") },
    { key: "guild.admin",   label: t("permAdmin")    },
  ];
}

interface Props {
  guildId: string;
  onSwitch: () => void;
}

export default function GuildConfig({ guildId, onSwitch }: Props) {
  const t = useT();
  const { lang } = useLang();
  const PERM_COLS = usePermCols();
  const [guild, setGuild] = useState<(SiteGuild & { albion_alliance_id: string | null; albion_alliance_name: string | null; settings: Record<string, unknown> }) | null>(null);
  const [albionName, setAlbionName] = useState("");
  const [albionRegion, setAlbionRegion] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [albionNotFound, setAlbionNotFound] = useState(false);
  const [roles, setRoles] = useState<DiscordRole[] | null>(null);
  const [rolesErr, setRolesErr] = useState<string | null>(null);
  const [commands, setCommands] = useState<BotCommand[] | null>(null);
  const [registerRoleId, setRegisterRoleId] = useState<string>("");
  const [savingRole, setSavingRole] = useState(false);
  const [roleSaved, setRoleSaved] = useState(false);
  const [filter, setFilter] = useState<CmdFilter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [allyRoleId, setAllyRoleId] = useState("");
  const [savingAllyRole, setSavingAllyRole] = useState(false);
  const [allyRoleSaved, setAllyRoleSaved] = useState(false);
  const [allyAllowedGuilds, setAllyAllowedGuilds] = useState<string[]>([NO_ALLIES]);
  const [allyGuilds, setAllyGuilds] = useState<AllyGuild[] | null>(null);
  const [registerOthersRoles, setRegisterOthersRoles] = useState<string[]>([ADMIN]);
  const [botLanguage, setBotLanguage] = useState<Lang>("pt");

  useEffect(() => {
    api.guildInfo(guildId).then(g => {
      setGuild(g);
      setAlbionName(g.albion_guild_name ?? "");
      setAlbionRegion((g.settings.albion_guild_region as string | undefined) ?? "");
      setRegisterRoleId((g.settings.register_role_id as string | undefined) ?? "");
      setAllyRoleId((g.settings.ally_role_id as string | undefined) ?? "");
      setAllyAllowedGuilds((g.settings.ally_allowed_guilds as string[] | undefined) ?? [NO_ALLIES]);
      setBotLanguage((g.settings.bot_language as Lang | undefined) ?? "pt");
      const commandRoles = g.settings.command_roles as Record<string, string[]> | undefined;
      // Sem nada salvo ainda, o default é admin-only (ver DEFAULT_ALLOWED_ROLES
      // no backend) — mostra isso já marcado em vez de aparentar "ninguém".
      setRegisterOthersRoles(commandRoles?.register_others ?? [ADMIN]);
    });
    api.guildAllies(guildId).then(setAllyGuilds).catch(() => setAllyGuilds([]));

    // Cargos e comandos vêm direto do Discord (sem cache no backend) — então
    // novo cargo, troca de nome ou de hierarquia, e até comandos liberados por
    // outro admin aparecem aqui sem precisar recarregar a página. 8s é rápido
    // o bastante pra parecer ao vivo numa tela de config (pouco tráfego, sem
    // necessidade de WebSocket pra isso).
    let cancelled = false;
    let hasLoadedRoles = false;
    function refreshDiscordState() {
      api.guildDiscordRoles(guildId).then(r => {
        if (cancelled) return;
        setRoles(r); setRolesErr(null); hasLoadedRoles = true;
      }).catch(e => {
        // só mostra erro antes do 1º sucesso — depois disso, uma falha pontual
        // no poll (rede instável) não deve apagar a última lista boa da tela.
        if (cancelled || hasLoadedRoles) return;
        setRolesErr(e.message);
      });
      api.guildCommands(guildId).then(c => { if (!cancelled) setCommands(c); })
        .catch(() => {});
    }
    refreshDiscordState();
    const interval = setInterval(refreshDiscordState, 8000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [guildId]);

  function toggleExpanded(name: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  async function toggleCommand(name: string, enabled: boolean) {
    setCommands(prev => prev?.map(c => c.name === name ? { ...c, enabled } : c) ?? prev);
    await api.toggleGuildCommand(guildId, name, enabled).catch(() => {
      setCommands(prev => prev?.map(c => c.name === name ? { ...c, enabled: !enabled } : c) ?? prev);
    });
  }

  async function toggleCommandRole(name: string, roleKey: string) {
    const cmd = commands?.find(c => c.name === name);
    if (!cmd) return;
    const next = toggleRoleKey(cmd.allowed_roles, roleKey);
    setCommands(prev => prev?.map(c => c.name === name ? { ...c, allowed_roles: next } : c) ?? prev);
    await api.updateCommandRoles(guildId, name, next).catch(() => {
      setCommands(prev => prev?.map(c => c.name === name ? { ...c, allowed_roles: cmd.allowed_roles } : c) ?? prev);
    });
  }

  async function toggleRegisterOthersRole(roleKey: string) {
    const prev = registerOthersRoles;
    const next = toggleRoleKey(prev, roleKey);
    setRegisterOthersRoles(next);
    await api.updateCommandRoles(guildId, "register_others", next).catch(() => {
      setRegisterOthersRoles(prev);
    });
  }

  async function togglePerm(roleId: string, permKey: keyof Permissions, value: boolean) {
    const role = roles?.find(r => r.id === roleId);
    if (!role) return;
    const newPerms: Partial<Permissions> = { ...role.permissions, [permKey]: value };
    setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: newPerms }) ?? prev);
    await api.updateRolePermissions(guildId, roleId, role.name, newPerms).catch(() => {
      setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: role.permissions }) ?? prev);
    });
  }

  async function saveAlbion() {
    if (!guild) return;
    setSaving(true);
    setAlbionNotFound(false);
    try {
      const res = await api.updateGuildSettings(guildId, {
        albion_guild_name: albionName || null,
        albion_guild_region: albionRegion || null,
      });
      if (albionName && !res.albion_guild_resolved) {
        setAlbionNotFound(true);
        return;
      }
      // Resolvida (ou limpa) — refaz o fetch pra pegar id/aliança recém-descobertos,
      // e a lista de aliados junto (a aliança pode ter mudado nesse save).
      const [g, allies] = await Promise.all([api.guildInfo(guildId), api.guildAllies(guildId)]);
      setGuild(g);
      setAllyGuilds(allies);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  async function saveRegisterRole(value: string) {
    setRegisterRoleId(value);
    setSavingRole(true);
    try {
      await api.updateGuildSettings(guildId, { register_role_id: value || null });
      setRoleSaved(true);
      setTimeout(() => setRoleSaved(false), 2000);
    } finally {
      setSavingRole(false);
    }
  }

  async function saveBotLanguage(value: Lang) {
    setBotLanguage(value);
    await api.updateGuildSettings(guildId, { bot_language: value });
  }

  async function saveAllyRole(value: string) {
    setAllyRoleId(value);
    setSavingAllyRole(true);
    try {
      await api.updateGuildSettings(guildId, { ally_role_id: value || null });
      setAllyRoleSaved(true);
      setTimeout(() => setAllyRoleSaved(false), 2000);
    } finally {
      setSavingAllyRole(false);
    }
  }

  async function toggleAllyGuild(key: string) {
    const prev = allyAllowedGuilds;
    let next: string[];
    if (key === ALL_ALLIES || key === NO_ALLIES) {
      // "Todos" e "Nenhum" são exclusivos entre si e com qualquer guilda
      // específica — selecionar um deles substitui a seleção inteira.
      next = prev.includes(key) ? [] : [key];
    } else {
      // Selecionar uma guilda específica sai do modo "Todos"/"Nenhum" e
      // passa a montar uma lista própria.
      const specific = prev.filter(k => k !== ALL_ALLIES && k !== NO_ALLIES);
      next = specific.includes(key) ? specific.filter(k => k !== key) : [...specific, key];
    }
    if (next.length === 0) next = [NO_ALLIES];
    setAllyAllowedGuilds(next);
    await api.updateGuildSettings(guildId, { ally_allowed_guilds: next }).catch(() => {
      setAllyAllowedGuilds(prev);
    });
  }

  function rolePreviewLabel(cmd: BotCommand): string {
    if (cmd.allowed_roles.length === 0 || cmd.allowed_roles.includes(EVERYONE)) return t("everyonePlaceholder");
    const names: string[] = [];
    if (cmd.allowed_roles.includes(ADMIN)) names.push(t("administratorsPlaceholder"));
    for (const key of cmd.allowed_roles) {
      if (key === EVERYONE || key === ADMIN) continue;
      const r = roles?.find(rr => rr.id === key);
      if (r) names.push(r.name);
    }
    if (names.length === 0) return t("everyonePlaceholder");
    if (names.length <= 2) return names.join(", ");
    return `${names[0]}, ${names[1]} +${names.length - 2}`;
  }

  function roleChipsFor(allowedRoles: string[], toggle: (key: string) => void) {
    return (
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => toggle(EVERYONE)}
          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
            allowedRoles.length === 0 || allowedRoles.includes(EVERYONE)
              ? "border-amber-500 bg-amber-500/15 text-amber-300"
              : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
          }`}
        >
          {t("everyonePlaceholder")}
        </button>
        <button
          type="button"
          onClick={() => toggle(ADMIN)}
          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
            allowedRoles.includes(ADMIN)
              ? "border-amber-500 bg-amber-500/15 text-amber-300"
              : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
          }`}
        >
          {t("administratorsPlaceholder")}
        </button>
        {(roles ?? []).map(r => (
          <button
            key={r.id}
            type="button"
            onClick={() => toggle(r.id)}
            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
              allowedRoles.includes(r.id)
                ? "border-amber-500 bg-amber-500/15 text-amber-300"
                : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
            }`}
          >
            {r.name}
          </button>
        ))}
      </div>
    );
  }

  if (!guild) return <div className="py-16 text-center text-zinc-500">{t("loading")}</div>;

  const visibleCommands = (commands ?? [])
    .filter(c => !CHILD_COMMANDS.has(c.name))
    .filter(c => filter === "all" ? true : filter === "enabled" ? c.enabled : !c.enabled);

  // "Nenhum" sozinho (ou lista vazia) = nenhum aliado passa — não tem sentido
  // mostrar o cargo de aliados nesse caso, ele nunca seria usado.
  const anyAlliesAllowed = allyAllowedGuilds.includes(ALL_ALLIES)
    || allyAllowedGuilds.some(k => k !== NO_ALLIES && k !== ALL_ALLIES);

  const FILTERS: { key: CmdFilter; label: string }[] = [
    { key: "all",      label: t("filterAll") },
    { key: "enabled",  label: t("filterEnabled") },
    { key: "disabled", label: t("filterDisabled") },
  ];

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">{t("config")}</h1>
      <p className="text-sm text-zinc-500 mb-8">
        {t("serverLabel")} <strong className="text-zinc-300">{guild.name}</strong>
      </p>

      <div className="space-y-4">

        {/* Status do bot */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-brand-discord text-indigo-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("discordBot")}</h2>
            <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${
              guild.bot_present
                ? "bg-emerald-500/15 text-emerald-400"
                : "bg-red-500/15 text-red-400"
            }`}>
              {guild.bot_present ? t("botActive") : t("botNotFound")}
            </span>
          </div>

          <div className="flex items-center gap-2 mb-3">
            <label className="text-xs text-zinc-500">{t("botLanguageLabel")}</label>
            <select
              value={botLanguage}
              onChange={e => saveBotLanguage(e.target.value as Lang)}
              className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1 text-zinc-200"
            >
              {(Object.keys(LANG_FULL) as Lang[]).map(l => (
                <option key={l} value={l}>{LANG_FULL[l]}</option>
              ))}
            </select>
          </div>

          {guild.bot_present ? (
            <p className="text-xs text-zinc-500">
              {t("botPresentDesc")}
            </p>
          ) : (
            <>
              <p className="text-xs text-zinc-500 mb-4">
                {t("botMissingDesc")}
              </p>
              <a
                href={`${BOT_INVITE}&guild_id=${guildId}`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-discord inline-flex items-center gap-2 text-sm"
              >
                <i className="ti ti-plus" aria-hidden="true" /> {t("inviteBot")}
              </a>
            </>
          )}
        </div>

        {/* Comandos do bot — toggle + permissões de cargo juntos, expansível */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-terminal text-amber-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("commandsCardTitle")}</h2>
          </div>
          <div className="flex items-center justify-between gap-3 mb-4">
            <p className="text-xs text-zinc-500">{t("commandsDesc")}</p>
            <div className="inline-flex rounded-lg border border-zinc-700 overflow-hidden shrink-0">
              {FILTERS.map(f => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  className={`px-2.5 py-1 text-[11px] font-medium transition-colors ${
                    filter === f.key
                      ? "bg-amber-600 text-zinc-950"
                      : "bg-transparent text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {rolesErr && <p style={{ color: "var(--red)", fontSize: 12, marginBottom: 8 }}>{rolesErr}</p>}

          {commands === null ? (
            <p className="text-xs text-zinc-500">{t("loading")}</p>
          ) : visibleCommands.length === 0 ? (
            <p className="text-xs text-zinc-500">
              {commands.length === 0 ? t("noCommandsRegistered") : t("noCommandsFiltered")}
            </p>
          ) : (
            <div className="flex flex-col">
              {visibleCommands.map((cmd, i) => {
                const isExpanded = expanded.has(cmd.name);
                // Cabeçalho de categoria só quando ela muda em relação ao
                // comando anterior — lista continua uma sequência plana (sem
                // agrupar/reordenar de verdade), só ganha divisores.
                const showCategoryHeader = cmd.category !== visibleCommands[i - 1]?.category;
                return (
                  <Fragment key={cmd.name}>
                  {showCategoryHeader && (
                    <p className="mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-600 first:mt-0">
                      {t(CATEGORY_LABEL_KEYS[cmd.category] ?? CATEGORY_LABEL_KEYS.miscellaneous)}
                    </p>
                  )}
                  <div className="border-b border-zinc-800/60 last:border-0">
                    <div className="flex items-center gap-3 py-2.5">
                      <label className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={cmd.enabled}
                          onChange={e => toggleCommand(cmd.name, e.target.checked)}
                          className="peer sr-only"
                        />
                        <span className="
                          absolute inset-0 rounded-full transition-colors
                          bg-zinc-700 peer-checked:bg-amber-600
                          after:absolute after:left-0.5 after:top-0.5
                          after:h-4 after:w-4 after:rounded-full after:bg-white
                          after:transition-transform peer-checked:after:translate-x-4
                        " />
                      </label>
                      <button
                        type="button"
                        onClick={() => toggleExpanded(cmd.name)}
                        className="flex-1 flex items-center gap-3 text-left min-w-0"
                      >
                        <span className="flex-1 min-w-0">
                          <span className="text-sm text-zinc-100 font-mono">/{cmd.name}</span>
                          <span className="text-xs text-zinc-500 ml-2">
                            {CMD_DESC_KEYS[cmd.name] ? t(CMD_DESC_KEYS[cmd.name]) : cmd.description}
                          </span>
                        </span>
                        <span className={`text-[11px] shrink-0 ml-2 text-right max-w-[35%] truncate ${cmd.enabled ? "text-zinc-500" : "text-zinc-700"}`}>
                          {rolePreviewLabel(cmd)}
                        </span>
                        <i className={`ti ti-chevron-down text-zinc-600 transition-transform shrink-0 ${isExpanded ? "rotate-180" : ""}`} />
                      </button>
                    </div>

                    {isExpanded && (
                      <div className={`pb-4 pl-12 ${cmd.enabled ? "" : "opacity-40 pointer-events-none"}`}>
                        <p className="text-[11px] text-zinc-600 mb-1.5">{t("whoCanUse")}</p>
                        {roleChipsFor(cmd.allowed_roles, key => toggleCommandRole(cmd.name, key))}

                        {cmd.name === "register" && (
                          <div className="mt-3 pt-3 border-t border-zinc-800/60">
                            <p className="text-[11px] text-zinc-600 mb-1.5">{t("registerOthersTitle")}</p>
                            <p className="text-[11px] text-zinc-600 mb-2">{t("registerOthersDesc")}</p>
                            {roleChipsFor(registerOthersRoles, toggleRegisterOthersRole)}
                          </div>
                        )}

                        {NEEDS_ALBION_NAME.has(cmd.name) && (
                          <div className="mt-3 pt-3 border-t border-zinc-800/60">
                            <p className="text-[11px] text-zinc-600 mb-1.5">{t("albionGuildTitle")}</p>
                            <p className="text-[11px] text-zinc-600 mb-2">{t("albionGuildDesc")}</p>
                            <div className="flex gap-2">
                              <input
                                value={albionName}
                                onChange={e => { setAlbionName(e.target.value); setAlbionNotFound(false); }}
                                disabled={!cmd.enabled}
                                placeholder={t("guildNamePlaceholder")}
                                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500 placeholder:text-zinc-600"
                              />
                              <select
                                value={albionRegion}
                                onChange={e => { setAlbionRegion(e.target.value); setAlbionNotFound(false); }}
                                disabled={!cmd.enabled}
                                className="w-32 shrink-0 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500"
                              >
                                <option value="">{t("albionRegionAuto")}</option>
                                {ALBION_REGIONS.map(r => (
                                  <option key={r} value={r}>{REGION_LABELS[lang][r]}</option>
                                ))}
                              </select>
                              <button onClick={saveAlbion} disabled={saving || !cmd.enabled} className="btn shrink-0">
                                {saved ? <><i className="ti ti-check" /> {t("saved")}</> : saving ? t("saving") : t("save")}
                              </button>
                            </div>
                            {albionNotFound && <p className="text-xs text-red-400 mt-1.5">{t("albionGuildNotFound")}</p>}
                          </div>
                        )}

                        {cmd.name === "register" && (
                          <div className="mt-3 pt-3 border-t border-zinc-800/60">
                            <p className="text-[11px] text-zinc-600 mb-1.5">{t("registerRoleTitle")}</p>
                            <p className="text-[11px] text-zinc-600 mb-2">{t("registerRoleDesc")}</p>
                            <div className="flex gap-2">
                              <select
                                value={registerRoleId}
                                onChange={e => saveRegisterRole(e.target.value)}
                                disabled={savingRole || !cmd.enabled}
                                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500"
                              >
                                <option value="">{t("registerRoleNone")}</option>
                                {(roles ?? []).map(r => (
                                  <option key={r.id} value={r.id}>{r.name}</option>
                                ))}
                              </select>
                              {roleSaved && <span className="text-xs text-amber-400 self-center"><i className="ti ti-check" /> {t("saved")}</span>}
                            </div>
                          </div>
                        )}

                        {cmd.name === "register" && (
                          <div className="mt-3 pt-3 border-t border-zinc-800/60">
                            <p className="text-[11px] text-zinc-600 mb-1.5">{t("allianceTitle")}</p>
                            <p className="text-[11px] text-zinc-600 mb-3">
                              {guild.albion_alliance_id
                                ? (guild.albion_alliance_name
                                    ? `${t("allianceDetected")} ${guild.albion_alliance_name}`
                                    : t("allianceDetectedNoName"))
                                : t("allianceNone")}
                            </p>

                            <p className="text-[11px] text-zinc-600 mb-1.5">{t("allyGuildsTitle")}</p>
                            <p className="text-[11px] text-zinc-600 mb-2">{t("allyGuildsDesc")}</p>
                            <div className="flex flex-wrap gap-1.5">
                              <button
                                type="button"
                                onClick={() => toggleAllyGuild(ALL_ALLIES)}
                                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                                  allyAllowedGuilds.includes(ALL_ALLIES)
                                    ? "border-amber-500 bg-amber-500/15 text-amber-300"
                                    : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                                }`}
                              >
                                {t("allyAllPlaceholder")}
                              </button>
                              <button
                                type="button"
                                onClick={() => toggleAllyGuild(NO_ALLIES)}
                                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                                  allyAllowedGuilds.includes(NO_ALLIES)
                                    ? "border-amber-500 bg-amber-500/15 text-amber-300"
                                    : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                                }`}
                              >
                                {t("allyNonePlaceholder")}
                              </button>
                              {(allyGuilds ?? []).map(ag => (
                                <button
                                  key={ag.id}
                                  type="button"
                                  onClick={() => toggleAllyGuild(ag.id)}
                                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                                    allyAllowedGuilds.includes(ag.id)
                                      ? "border-amber-500 bg-amber-500/15 text-amber-300"
                                      : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                                  }`}
                                >
                                  {ag.name}
                                </button>
                              ))}
                              {allyGuilds !== null && allyGuilds.length === 0 && (
                                <span className="text-[11px] text-zinc-600 self-center">{t("noAlliesFound")}</span>
                              )}
                            </div>

                            {anyAlliesAllowed && (
                              <div className="mt-3">
                                <p className="text-[11px] text-zinc-600 mb-1.5">{t("allyRoleTitle")}</p>
                                <p className="text-[11px] text-zinc-600 mb-2">{t("allyRoleDesc")}</p>
                                <div className="flex gap-2">
                                  <select
                                    value={allyRoleId}
                                    onChange={e => saveAllyRole(e.target.value)}
                                    disabled={savingAllyRole || !cmd.enabled}
                                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500"
                                  >
                                    <option value="">{t("allyRoleSame")}</option>
                                    {(roles ?? []).map(r => (
                                      <option key={r.id} value={r.id}>{r.name}</option>
                                    ))}
                                  </select>
                                  {allyRoleSaved && <span className="text-xs text-amber-400 self-center"><i className="ti ti-check" /> {t("saved")}</span>}
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        {(SUBCOMMANDS_OF[cmd.name] ?? []).map(childName => {
                          const child = commands?.find(c => c.name === childName);
                          if (!child) return null;
                          return (
                            <div key={child.name} className="mt-3 pt-3 border-t border-zinc-800/60">
                              <div className="flex items-center gap-2 mb-2">
                                <i className="ti ti-corner-down-right text-zinc-600" aria-hidden="true" />
                                <label className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={child.enabled}
                                    onChange={e => toggleCommand(child.name, e.target.checked)}
                                    className="peer sr-only"
                                  />
                                  <span className="
                                    absolute inset-0 rounded-full transition-colors
                                    bg-zinc-700 peer-checked:bg-amber-600
                                    after:absolute after:left-0.5 after:top-0.5
                                    after:h-4 after:w-4 after:rounded-full after:bg-white
                                    after:transition-transform peer-checked:after:translate-x-4
                                  " />
                                </label>
                                <span className="text-sm text-zinc-100 font-mono">/{child.name}</span>
                                <span className="text-xs text-zinc-500">{child.description}</span>
                              </div>
                              <div className={child.enabled ? "" : "opacity-40 pointer-events-none"}>
                                <p className="text-[11px] text-zinc-600 mb-1.5">{t("whoCanUse")}</p>
                                {roleChipsFor(child.allowed_roles, key => toggleCommandRole(child.name, key))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  </Fragment>
                );
              })}
            </div>
          )}
        </div>

        {/* Permissões do site, por cargo */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-key text-violet-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("rolePermsTitle")}</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            {t("rolePermsDesc")}
          </p>
          {rolesErr && <p style={{ color: "var(--red)", fontSize: 12, marginBottom: 8 }}>{rolesErr}</p>}
          {roles === null && !rolesErr && <p className="text-xs text-zinc-500">{t("loadingRoles")}</p>}
          {roles !== null && roles.length === 0 && <p className="text-xs text-zinc-500">{t("noRolesFound")}</p>}
          {roles !== null && roles.length > 0 && (
            <div className="flex flex-col">
              {roles.map(role => (
                <div key={role.id} className="py-3 border-b border-zinc-800/60 last:border-0">
                  <div className="flex items-center gap-2 mb-2">
                    {role.color !== 0 && (
                      <span
                        className="inline-block w-2 h-2 rounded-full shrink-0"
                        style={{ background: `#${role.color.toString(16).padStart(6, "0")}` }}
                      />
                    )}
                    <span className="text-sm text-zinc-200 font-medium">{role.name}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {PERM_COLS.map(col => {
                      const active = !!role.permissions[col.key];
                      return (
                        <button
                          key={col.key}
                          type="button"
                          onClick={() => togglePerm(role.id, col.key, !active)}
                          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                            active
                              ? "border-violet-500 bg-violet-500/15 text-violet-300"
                              : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                          }`}
                        >
                          {col.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Trocar servidor */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-switch-horizontal text-zinc-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("switchServer")}</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            {t("switchServerDescConfig")}
          </p>
          <button className="btn" onClick={onSwitch}>
            <i className="ti ti-refresh" aria-hidden="true" /> {t("switchServer")}
          </button>
        </div>

      </div>
    </div>
  );
}
