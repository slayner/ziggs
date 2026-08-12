import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, BOT_INVITE, type CatalogRole, type DiscordRole, type NodeDef, type NodeMaps, type Permissions, type RegearSettings, type SiteGuild } from "../api";
import { useLang, useT, REGION_LABELS, LANG_FULL, type Lang, type TKey } from "../i18n";
import { ALBION_ITEMS, itemRenderUrl } from "../data/albion-items";
import { Panel } from "./Panel";

export const ALBION_REGIONS = ["americas", "europe", "asia"] as const;
const JUICY_KILL_HARD_FLOOR = 20_000_000;

// â”€â”€ Itens desabilitados do regear: o backend guarda por BASE ID (sem tier/
// enchant â€” "HEAD_PLATE_SET1", "MOUNT_OX"). O catÃ¡logo lista variantes
// T4-T8 Ã— @0-@4 da mesma base, entÃ£o deduplicamos p/ uma entrada por base.
function itemBaseId(id: string): string {
  return id.replace(/^T\d+_/, "").replace(/@\d+$/, "");
}
// â”€â”€ CatÃ¡logo default de tipos de node (localizado por idioma do bot).
// `key` = nome guardado no banco (canÃ´nico); `name.*` Ã© a traduÃ§Ã£o exibida
// conforme o idioma do bot. node_events.node_type guarda o `key`.
// 28 nodes: 5 recursos Ã— 5 tiers (4.4-8.4) + 4 vortex por cor + 4 orbes por cor.
const DEFAULT_NODE_DEFS: {
  key: string; name: { pt: string; en: string; es: string };
  emoji: string | null; weight: number;
}[] = [
  // Wood (Madeira)
  { key: "wood_4.4", name: { pt: "Madeira 4.4", en: "Wood 4.4", es: "Madera 4.4" }, emoji: "ðŸªµ", weight: 0.0016 },
  { key: "wood_5.4", name: { pt: "Madeira 5.4", en: "Wood 5.4", es: "Madera 5.4" }, emoji: "ðŸªµ", weight: 0.008 },
  { key: "wood_6.4", name: { pt: "Madeira 6.4", en: "Wood 6.4", es: "Madera 6.4" }, emoji: "ðŸªµ", weight: 0.04 },
  { key: "wood_7.4", name: { pt: "Madeira 7.4", en: "Wood 7.4", es: "Madera 7.4" }, emoji: "ðŸªµ", weight: 0.2 },
  { key: "wood_8.4", name: { pt: "Madeira 8.4", en: "Wood 8.4", es: "Madera 8.4" }, emoji: "ðŸªµ", weight: 1.0 },
  // Ore (MinÃ©rio)
  { key: "ore_4.4", name: { pt: "MinÃ©rio 4.4", en: "Ore 4.4", es: "Mineral 4.4" }, emoji: "ðŸª¨", weight: 0.0016 },
  { key: "ore_5.4", name: { pt: "MinÃ©rio 5.4", en: "Ore 5.4", es: "Mineral 5.4" }, emoji: "ðŸª¨", weight: 0.008 },
  { key: "ore_6.4", name: { pt: "MinÃ©rio 6.4", en: "Ore 6.4", es: "Mineral 6.4" }, emoji: "ðŸª¨", weight: 0.04 },
  { key: "ore_7.4", name: { pt: "MinÃ©rio 7.4", en: "Ore 7.4", es: "Mineral 7.4" }, emoji: "ðŸª¨", weight: 0.2 },
  { key: "ore_8.4", name: { pt: "MinÃ©rio 8.4", en: "Ore 8.4", es: "Mineral 8.4" }, emoji: "ðŸª¨", weight: 1.0 },
  // Fiber (Fibra)
  { key: "fiber_4.4", name: { pt: "Fibra 4.4", en: "Fiber 4.4", es: "Fibra 4.4" }, emoji: "ðŸŒ¿", weight: 0.0016 },
  { key: "fiber_5.4", name: { pt: "Fibra 5.4", en: "Fiber 5.4", es: "Fibra 5.4" }, emoji: "ðŸŒ¿", weight: 0.008 },
  { key: "fiber_6.4", name: { pt: "Fibra 6.4", en: "Fiber 6.4", es: "Fibra 6.4" }, emoji: "ðŸŒ¿", weight: 0.04 },
  { key: "fiber_7.4", name: { pt: "Fibra 7.4", en: "Fiber 7.4", es: "Fibra 7.4" }, emoji: "ðŸŒ¿", weight: 0.2 },
  { key: "fiber_8.4", name: { pt: "Fibra 8.4", en: "Fiber 8.4", es: "Fibra 8.4" }, emoji: "ðŸŒ¿", weight: 1.0 },
  // Hide (Couro)
  { key: "hide_4.4", name: { pt: "Couro 4.4", en: "Hide 4.4", es: "Cuero 4.4" }, emoji: "ðŸ—", weight: 0.0016 },
  { key: "hide_5.4", name: { pt: "Couro 5.4", en: "Hide 5.4", es: "Cuero 5.4" }, emoji: "ðŸ—", weight: 0.008 },
  { key: "hide_6.4", name: { pt: "Couro 6.4", en: "Hide 6.4", es: "Cuero 6.4" }, emoji: "ðŸ—", weight: 0.04 },
  { key: "hide_7.4", name: { pt: "Couro 7.4", en: "Hide 7.4", es: "Cuero 7.4" }, emoji: "ðŸ—", weight: 0.2 },
  { key: "hide_8.4", name: { pt: "Couro 8.4", en: "Hide 8.4", es: "Cuero 8.4" }, emoji: "ðŸ—", weight: 1.0 },
  // Vortex (by color)
  { key: "vortex_green", name: { pt: "Vortex Verde", en: "Green Vortex", es: "Vortex Verde" }, emoji: "ðŸŸ©", weight: 0.2 },
  { key: "vortex_blue", name: { pt: "Vortex Azul", en: "Blue Vortex", es: "Vortex Azul" }, emoji: "ðŸŸ¦", weight: 0.2 },
  { key: "vortex_purple", name: { pt: "Vortex Roxo", en: "Purple Vortex", es: "Vortex Morado" }, emoji: "ðŸŸª", weight: 0.2 },
  { key: "vortex_gold", name: { pt: "Vortex Dourado", en: "Gold Vortex", es: "Vortex Dorado" }, emoji: "ðŸŸ¨", weight: 0.2 },
  // Orbs (Orbes)
  { key: "orb_green", name: { pt: "Orbe Verde", en: "Green Orb", es: "Orbe Verde" }, emoji: "ðŸŸ¢", weight: 0.04 },
  { key: "orb_blue", name: { pt: "Orbe Azul", en: "Blue Orb", es: "Orbe Azul" }, emoji: "ðŸ”µ", weight: 0.04 },
  { key: "orb_purple", name: { pt: "Orbe Roxa", en: "Purple Orb", es: "Orbe Morada" }, emoji: "ðŸŸ£", weight: 0.04 },
  { key: "orb_gold", name: { pt: "Orbe Dourada", en: "Gold Orb", es: "Orbe Dorada" }, emoji: "ðŸŸ¡", weight: 0.04 },
];

// Nome exibido de um def: se bater com uma key do catÃ¡logo default, mostra o
// nome no idioma do bot; senÃ£o mostra o nome literal guardado no banco.
function nodeDefDisplayName(name: string, botLang: Lang): string {
  const d = DEFAULT_NODE_DEFS.find(x => x.key === name);
  return d ? (d.name[botLang] ?? d.name.pt ?? name) : name;
}

interface ItemBase { baseId: string; name: string; nameEn: string; sampleId: string; slot: string }
let _bases: ItemBase[] | null = null;
function itemBases(): ItemBase[] {
  if (_bases) return _bases;
  const map = new Map<string, ItemBase>();
  for (const it of ALBION_ITEMS) {
    const baseId = itemBaseId(it.id);
    if (map.has(baseId)) continue;
    const bare = it.name.replace(/^\d+\.\d+\s+/, "");
    map.set(baseId, {
      baseId, sampleId: it.id, slot: it.slot,
      name: bare, nameEn: (it.nameEn ?? bare),
    });
  }
  _bases = [...map.values()];
  return _bases;
}

interface BotCommand { name: string; description: string; category: string; enabled: boolean; allowed_roles: string[]; }
interface AllyGuild { id: string; name: string; }

const EVERYONE = "everyone";
const ADMIN = "admin";
const ALL_ALLIES = "all";
const NO_ALLIES = "none";

// @everyone Ã© exclusivo â€” nenhum outro cargo (Administrators incluso) pode
// coexistir com ele: selecionar @everyone limpa a lista inteira, e
// selecionar qualquer outro cargo tira @everyone se ele estiver lÃ¡.
function toggleRoleKey(prev: string[], key: string): string[] {
  if (key === EVERYONE) return prev.includes(EVERYONE) ? [] : [EVERYONE];
  const specific = prev.filter(k => k !== EVERYONE);
  return specific.includes(key) ? specific.filter(k => k !== key) : [...specific, key];
}

// DescriÃ§Ã£o de cada comando vem crua (PT) do backend (COMMANDS_REGISTRY) â€” o
// site Ã© multilÃ­ngue, entÃ£o a exibiÃ§Ã£o usa a traduÃ§Ã£o local por nome do
// comando em vez do texto da API, com o texto da API como fallback pra
// comando novo que ainda nÃ£o ganhou uma entrada aqui.
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

// Comandos que sÃ³ existem como dependÃªncia de outro (ex.: /unregister sÃ³ faz
// sentido se /register existir) entram aninhados dentro do pai em vez de
// aparecer como uma linha prÃ³pria na lista principal.
const SUBCOMMANDS_OF: Record<string, string[]> = { register: ["unregister"] };
const CHILD_COMMANDS = new Set(Object.values(SUBCOMMANDS_OF).flat());

function usePermCols(): { key: keyof Permissions; label: string }[] {
  const t = useT();
  return [
    { key: "events.view",   label: t("permEvView")   },
    { key: "events.create", label: t("permEvCreate") },
    { key: "events.manage", label: t("permEvManage") },
    { key: "comps.view",    label: t("permCoView")   },
    { key: "comps.create",  label: t("permCoCreate") },
    { key: "comps.manage",  label: t("permCoManage") },
    { key: "guild.admin",      label: t("permAdmin")    },
    { key: "escalacao.manage", label: t("permEscManage") },
  ];
}

interface Props {
  guildId: string;
  onSwitch: () => void;
  // false quando a aba tÃ¡ escondida (keep-alive no App) â†’ pausa o poll de 8s
  // dos cargos/comandos do Discord pra nÃ£o chover chamada em background.
  active?: boolean;
}

export default function GuildConfig({ guildId, onSwitch, active = true }: Props) {
  const t = useT();
  const { lang } = useLang();
  const PERM_COLS = usePermCols();
  const [guild, setGuild] = useState<(SiteGuild & { albion_alliance_id: string | null; albion_alliance_name: string | null; settings: Record<string, unknown> }) | null>(null);
  const [albionLinks, setAlbionLinks] = useState<{ albion_guild_id: string; albion_guild_name: string; region: string; alliance_name: string | null; is_primary?: boolean; verified?: boolean }[]>([]);
  const hasGuild = albionLinks.length > 0;
  const [newLinkName, setNewLinkName] = useState("");
  const [newLinkRegion, setNewLinkRegion] = useState("");
  const [linkErr, setLinkErr] = useState<string | null>(null);
  const [roles, setRoles] = useState<DiscordRole[] | null>(null);
  const [rolesErr, setRolesErr] = useState<string | null>(null);
  // Roles pinned in the permissions quadrant â€” initialized from any role that
  // already has a permission enabled, then managed manually by the user.
  const [permVisibleRoles, setPermVisibleRoles] = useState<Set<string>>(new Set());
  const [commands, setCommands] = useState<BotCommand[] | null>(null);
  const [registerRoleId, setRegisterRoleId] = useState<string>("");
  const [savingRole, setSavingRole] = useState(false);
  const [roleSaved, setRoleSaved] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [featOpen, setFeatOpen] = useState<Set<string>>(new Set());
  const [allyRoleId, setAllyRoleId] = useState("");
  const [savingAllyRole, setSavingAllyRole] = useState(false);
  const [allyRoleSaved, setAllyRoleSaved] = useState(false);
  const [allyAllowedGuilds, setAllyAllowedGuilds] = useState<string[]>([NO_ALLIES]);
  const [allyGuilds, setAllyGuilds] = useState<AllyGuild[] | null>(null);
  const [registerOthersRoles, setRegisterOthersRoles] = useState<string[]>([ADMIN]);
  const [botLanguage, setBotLanguage] = useState<Lang>("pt");
  // Eventos: canal do mass-info + gates de funÃ§Ã£o por cargo (ver backend
  // app/services/event_gates.py). gates = {nome_funÃ§Ã£o_minusculo: [role_id, ...]}.
  // `enabled` Ã© um estado Ã  parte do channelId: desligar a feature NÃƒO apaga
  // o canal escolhido localmente, sÃ³ manda null pro servidor â€” religar
  // reenvia o mesmo canal sem precisar escolher de novo (era o bug reportado).
  const [eventsChannelId, setEventsChannelId] = useState<string>("");
  const [eventsEnabled, setEventsEnabled] = useState(false);
  const [reviewChannelId, setReviewChannelId] = useState<string>("");
  const [regearThreadChannelId, setRegearThreadChannelId] = useState<string>("");
  const [lootlogThreadChannelId, setLootlogThreadChannelId] = useState<string>("");
  const [voiceCtaChannelId, setVoiceCtaChannelId] = useState<string>("");
  const [trialPercent, setTrialPercent] = useState<string>("20");
  const [trialRoleId, setTrialRoleId] = useState<string>("");
  const [nodesCalendarChannelId, setNodesCalendarChannelId] = useState<string>("");
  const [nodesEnabled, setNodesEnabled] = useState(false);
  // Battle feed â€” mensageiro de batalhas (link + imagem de resumo no canal)
  const [battleFeedChannelId, setBattleFeedChannelId] = useState<string>("");
  const [battleFeedMinPlayers, setBattleFeedMinPlayers] = useState<string>("10");
  const [battleFeedEnabled, setBattleFeedEnabled] = useState(false);
  const [juicyKillChannelId, setJuicyKillChannelId] = useState("");
  const [juicyKillMinSilver, setJuicyKillMinSilver] = useState("50000000");
  const [juicyKillMinFame, setJuicyKillMinFame] = useState("0");
  const [juicyKillRegions, setJuicyKillRegions] = useState<string[]>([]);
  const [juicyKillEnabled, setJuicyKillEnabled] = useState(false);
  // Canal de logs do bot (retransmissÃ£o do AuditLog) â€” o bot cria e mantÃ©m
  // sozinho quando ligado (ver cogs/audit_log.py); aqui Ã© sÃ³ leitura pro admin
  // saber onde olhar. Toggle mestre em botLogsEnabled (default true).
  const [logsChannelId, setLogsChannelId] = useState<string>("");
  const [botLogsEnabled, setBotLogsEnabled] = useState(true);
  // Tipos de node + mapas (gerenciados aqui no lugar da antiga aba Nodes; adicionar
  // node em si Ã© pelo Discord). Form de def e de mapa extra:
  const [nodeDefs, setNodeDefs] = useState<NodeDef[]>([]);
  const [nodeDefsLoaded, setNodeDefsLoaded] = useState(false);
  const [nodeMaps, setNodeMaps] = useState<NodeMaps | null>(null);
  const [mapsOpen, setMapsOpen] = useState(false);
  const [ndName, setNdName] = useState("");
  const [ndEmoji, setNdEmoji] = useState("");
  // Exibido como % (0-100); o backend guarda fraÃ§Ã£o em NodeDef.weight.
  const [ndWeight, setNdWeight] = useState("100");
  // EdiÃ§Ã£o inline de um def jÃ¡ criado (por id â€” permite renomear sem criar
  // linha nova, via PATCH /defs/{id}). SÃ³ um row editÃ¡vel por vez.
  const [ndEditingId, setNdEditingId] = useState<number | null>(null);
  const [ndEditName, setNdEditName] = useState("");
  const [ndEditEmoji, setNdEditEmoji] = useState("");
  const [ndEditWeight, setNdEditWeight] = useState("100");
  // Auto-seed do catÃ¡logo default sÃ³ uma vez por mount da guilda, e sÃ³ se a
  // guilda ainda nÃ£o tem nenhum def. ponytail: no-op enquanto DEFAULT_NODE_DEFS
  // estiver vazio (lista exata do usuÃ¡rio pendente).
  const nodeSeedRef = useRef(false);
  const [nmName, setNmName] = useState("");
  const [nodesErr, setNodesErr] = useState<string | null>(null);
  const [channels, setChannels] = useState<{ id: string; name: string }[] | null>(null);
  const [voiceChannels, setVoiceChannels] = useState<{ id: string; name: string }[] | null>(null);
  const [channelsErr, setChannelsErr] = useState<string | null>(null);
  const [gameRoles, setGameRoles] = useState<CatalogRole[]>([]);
  const [eventRoleGates, setEventRoleGates] = useState<Record<string, string[]>>({});
  // MÃ­nimo de builds (funÃ§Ãµes) ao se inscrever. NÃ£o existe limite mÃ¡ximo.
  const [signupMinBuilds, setSignupMinBuilds] = useState<string>("");
  // Regear: canal de screenshots, % de cobertura, categorias cobertas, override
  // de itens e cargos aprovadores. Salvo em Guild.settings.regear (JSONB).
  const [regear, setRegear] = useState<RegearSettings | null>(null);
  const [regearEnabled, setRegearEnabled] = useState(false);
  // Lootlog: % dos loggers (tambÃ©m editÃ¡vel na prÃ³pria pÃ¡gina de lootlog, mas
  // fica nas settings normais p/ o admin configurar sem abrir um CTA).
  const [llPct, setLlPct] = useState(5);
  const [lootlogEnabled, setLootlogEnabled] = useState(true);
  // Lootsplit mode: regear Ã© sempre calculado; isto sÃ³ decide como a tab do
  // evento vira split. Guild.settings.lootsplit_mode (ver events.get_lootsplit_mode).
  const [lootsplitMode, setLootsplitMode] = useState<string>("full");
  // Taxa da guilda: % da tab debitada pro banco ANTES do pool de participantes
  // (0-100, default 0). SÃ³ em modos com split. Ver events.get_guild_tax_percent.
  const [guildTaxPct, setGuildTaxPct] = useState<string>("0");
  // Scout bonus source: de onde vem o bÃ´nus do scout (NodeDef.weight). "node"
  // (default) = peso Ã— sold_value, pool separado. "tab" = peso Ã— tab_value,
  // deduzido da participant pool. Ver events.get_scout_bonus_source.
  const [scoutBonusSource, setScoutBonusSource] = useState<string>("node");
  // Scout percent global (0-100): multiplica o weight de cada node. Se 5% e
  // node weight=50%, bÃ´nus real = 50% de 5% = 2.5% do sold value.
  const [scoutPercent, setScoutPercent] = useState<string>("0");
  // Pings de @everyone do mass-info: momentos em que o bot deleta a embed e
  // reenvia com @everyone. Subconjunto de PING_TRIGGERS; default = os 3 primeiros
  // (review off). [] = tudo off (status triggers ainda bumpam silenciosamente).
  const [pingTriggers, setPingTriggers] = useState<string[]>(["created", "t10min", "in_progress"]);
  const [pingTriggersSaved, setPingTriggersSaved] = useState(false);

  const REGEAR_CATS: { key: string; labelKey: TKey }[] = [
    { key: "weapon", labelKey: "regcfgCatWeapon" },
    { key: "offhand", labelKey: "regcfgCatOffhand" },
    { key: "helmet", labelKey: "regcfgCatHelmet" },
    { key: "armor", labelKey: "regcfgCatArmor" },
    { key: "boots", labelKey: "regcfgCatBoots" },
    { key: "cape", labelKey: "regcfgCatCape" },
    { key: "mount", labelKey: "regcfgCatMount" },
    { key: "food", labelKey: "regcfgCatFood" },
    { key: "potion", labelKey: "regcfgCatPotion" },
  ];

  const LOOTSPLIT_MODES: { mode: string; labelKey: TKey; descKey: TKey }[] = [
    { mode: "none", labelKey: "lootsplitModeNone", descKey: "lootsplitModeNoneDesc" },
    { mode: "leftover", labelKey: "lootsplitModeLeftover", descKey: "lootsplitModeLeftoverDesc" },
    { mode: "full", labelKey: "lootsplitModeFull", descKey: "lootsplitModeFullDesc" },
    { mode: "guild_backed", labelKey: "lootsplitModeGuildBacked", descKey: "lootsplitModeGuildBackedDesc" },
  ];

  // Scout bonus source â€” de onde vem o bÃ´nus do scout (NodeDef.weight). "node"
  // (default) = peso Ã— sold_value (pool separado). "tab" = peso Ã— tab_value,
  // deduzido da participant pool. Ver events.get_scout_bonus_source no backend.
  const SCOUT_BONUS_SOURCES: { src: string; labelKey: TKey; descKey: TKey }[] = [
    { src: "node", labelKey: "scoutBonusSourceNode", descKey: "scoutBonusSourceNodeDesc" },
    { src: "tab", labelKey: "scoutBonusSourceTab", descKey: "scoutBonusSourceTabDesc" },
  ];

  // Momentos configurÃ¡veis de ping @everyone do mass-info (ver event_signups.py
  // no backend). default ligado: created/t10min/in_progress; review off.
  const PING_TRIGGERS: { key: string; labelKey: TKey; descKey: TKey; defaultOn: boolean }[] = [
    { key: "created", labelKey: "pingTrgCreated", descKey: "pingTrgCreatedDesc", defaultOn: true },
    { key: "t10min", labelKey: "pingTrgT10min", descKey: "pingTrgT10minDesc", defaultOn: true },
    { key: "in_progress", labelKey: "pingTrgInProgress", descKey: "pingTrgInProgressDesc", defaultOn: true },
    { key: "review", labelKey: "pingTrgReview", descKey: "pingTrgReviewDesc", defaultOn: false },
  ];

  // Auto-seed do catÃ¡logo default: se a guilda ainda nÃ£o tem nenhum def e o
  // catÃ¡logo nÃ£o estÃ¡ vazio, posta cada default via upsertNodeDef. SÃ³ roda uma
  // vez por mount (nodeSeedRef) â€” nÃ£o repopula se o usuÃ¡rio apagar tudo depois.
  useEffect(() => {
    if (nodeSeedRef.current) return;
    if (DEFAULT_NODE_DEFS.length === 0) return;
    if (!nodeDefsLoaded) return;       // espera o primeiro GET resolver
    if (nodeDefs.length > 0) return;    // jÃ¡ tem defs â€” nÃ£o semea
    nodeSeedRef.current = true;
    (async () => {
      for (const d of DEFAULT_NODE_DEFS) {
        try { await api.upsertNodeDef({ name: d.key, emoji: d.emoji, weight: d.weight, sort: 0 }); }
        catch (e: any) { setNodesErr(String(e?.message ?? e)); }
      }
      await refreshNodeDefs();
    })();
  }, [nodeDefsLoaded, nodeDefs.length]);

  useEffect(() => {
    api.guildInfo(guildId).then(g => {
      setGuild(g);
      setRegisterRoleId((g.settings.register_role_id as string | undefined) ?? "");
      setAllyRoleId((g.settings.ally_role_id as string | undefined) ?? "");
      setAllyAllowedGuilds((g.settings.ally_allowed_guilds as string[] | undefined) ?? [NO_ALLIES]);
      setBotLanguage((g.settings.bot_language as Lang | undefined) ?? "pt");
      const evCh = (g.settings.events_channel_id as string | undefined) ?? "";
      setEventsChannelId(evCh);
      setEventsEnabled(!!evCh);
      setReviewChannelId((g.settings.event_review_channel_id as string | undefined) ?? "");
      setRegearThreadChannelId((g.settings.regear_thread_channel_id as string | undefined) ?? "");
      setLootlogThreadChannelId((g.settings.lootlog_thread_channel_id as string | undefined) ?? "");
      setVoiceCtaChannelId((g.settings.voice_cta_channel_id as string | undefined) ?? "");
      setTrialPercent(String(g.settings.trial_percent ?? 20));
      setTrialRoleId((g.settings.trial_role_id as string | undefined) ?? "");
      setLootsplitMode((g.settings.lootsplit_mode as string | undefined) ?? "full");
      setGuildTaxPct(String(g.settings.guild_tax_percent ?? 0));
      setScoutBonusSource((g.settings.scout_bonus_source as string | undefined) ?? "node");
      setScoutPercent(String(g.settings.scout_percent ?? 0));
      const pt = g.settings.events_ping_triggers as string[] | undefined;
      setPingTriggers(Array.isArray(pt) ? pt : ["created", "t10min", "in_progress"]);
      const nodesCh = (g.settings.nodes_calendar_channel_id as string | undefined) ?? "";
      setNodesCalendarChannelId(nodesCh);
      setNodesEnabled(!!nodesCh);
      setLogsChannelId((g.settings.logs_channel_id as string | undefined) ?? "");
      setBotLogsEnabled((g.settings.bot_logs_enabled as boolean | undefined) ?? true);
      const bfCh = (g.settings.battle_feed_channel_id as string | undefined) ?? "";
      setBattleFeedChannelId(bfCh);
      setBattleFeedEnabled(!!bfCh);
      setBattleFeedMinPlayers(String(g.settings.battle_feed_min_players ?? 10));
      const jkCh = (g.settings.juicy_kill_channel_id as string | undefined) ?? "";
      setJuicyKillChannelId(jkCh);
      setJuicyKillEnabled(!!jkCh);
      setJuicyKillMinSilver(String(g.settings.juicy_kill_min_silver ?? 50_000_000));
      setJuicyKillMinFame(String(g.settings.juicy_kill_min_fame ?? 0));
      setJuicyKillRegions((g.settings.juicy_kill_regions as string[] | undefined) ?? []);
      setEventRoleGates((g.settings.event_role_gates as Record<string, string[]> | undefined) ?? {});
      setSignupMinBuilds(String(g.settings.signup_min_builds ?? ""));
      const commandRoles = g.settings.command_roles as Record<string, string[]> | undefined;
      // Sem nada salvo ainda, o default Ã© admin-only (ver DEFAULT_ALLOWED_ROLES
      // no backend) â€” mostra isso jÃ¡ marcado em vez de aparentar "ninguÃ©m".
      setRegisterOthersRoles(commandRoles?.register_others ?? [ADMIN]);
    });
    api.guildAllies(guildId).then(setAllyGuilds).catch(() => setAllyGuilds([]));
    setLinkErr(null);
    refreshAlbionLinks();
    api.guildDiscordChannels(guildId)
      .then(r => { setChannels(r); setChannelsErr(null); })
      .catch(e => setChannelsErr(e.message));
    api.guildDiscordChannels(guildId, true)
      .then(setVoiceChannels)
      .catch(() => setVoiceChannels([]));
    api.listRoles().then(setGameRoles).catch(() => setGameRoles([]));
    api.getRegearSettings()
      .then(s => { setRegear(s); setRegearEnabled(s.enabled); })
      .catch(() => setRegear(null));
    api.getLootLogSettings()
      .then(s => { setLlPct(s.logger_percent); setLootlogEnabled(s.enabled); })
      .catch(() => {});
    api.listNodeDefs().then(d => { setNodeDefs(d); setNodeDefsLoaded(true); }).catch(() => setNodeDefsLoaded(true));
    api.listNodeMaps().then(setNodeMaps).catch(() => {});
  }, [guildId]);

  // Cargos e comandos vÃªm direto do Discord (sem cache no backend) â€” entÃ£o
  // novo cargo, troca de nome ou de hierarquia, e atÃ© comandos liberados por
  // outro admin aparecem aqui sem precisar recarregar a pÃ¡gina. 8s Ã© rÃ¡pido
  // o bastante pra parecer ao vivo numa tela de config (pouco trÃ¡fego, sem
  // necessidade de WebSocket pra isso).
  // ponytail: effect separado do load inicial, deps [guildId, active] â€”
  // `active` pausa o poll quando a aba tÃ¡ escondida (keep-alive no App) sem
  // refazer os loads iniciais (guildInfo/channels) a cada toggle de visibilidade.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let hasLoadedRoles = false;
    function refreshDiscordState() {
      api.guildDiscordRoles(guildId).then(r => {
        if (cancelled) return;
        setRoles(r); setRolesErr(null); hasLoadedRoles = true;
      }).catch(e => {
        // sÃ³ mostra erro antes do 1Âº sucesso â€” depois disso, uma falha pontual
        // no poll (rede instÃ¡vel) nÃ£o deve apagar a Ãºltima lista boa da tela.
        if (cancelled || hasLoadedRoles) return;
        setRolesErr(e.message);
      });
      api.guildCommands(guildId).then(c => { if (!cancelled) setCommands(c); })
        .catch(() => {});
    }
    refreshDiscordState();
    const interval = setInterval(refreshDiscordState, 8000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [guildId, active]);

  function toggleExpanded(name: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  function toggleFeat(id: string) {
    setFeatOpen(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function openFeat(id: string) {
    setFeatOpen(prev => new Set(prev).add(id));
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

  // Whether a role has at least one permission toggled on.
  function roleHasAnyPerm(roleId: string): boolean {
    const role = roles?.find(r => r.id === roleId);
    if (!role?.permissions) return false;
    return PERM_COLS.some(c => !!role.permissions[c.key]);
  }

  // Roles currently pinned in the quadrant (including any with perms that
  // haven't been explicitly removed).
  const permQuadrantIds = useMemo(() => {
    if (!roles) return permVisibleRoles;
    // Merge explicit pins with roles that currently have perms
    const ids = new Set(permVisibleRoles);
    for (const r of roles) {
      if (roleHasAnyPerm(r.id)) ids.add(r.id);
    }
    return ids;
  }, [roles, permVisibleRoles]);

  // Roles available in the dropdown (not yet in quadrant)
  const permDropdownRoles = useMemo(() => {
    if (!roles) return [];
    return roles.filter(r => !permQuadrantIds.has(r.id));
  }, [roles, permQuadrantIds]);

  function addRoleToPermQuadrant(roleId: string) {
    setPermVisibleRoles(prev => new Set(prev).add(roleId));
  }

  function removeRoleFromPermQuadrant(roleId: string) {
    setPermVisibleRoles(prev => {
      const next = new Set(prev);
      next.delete(roleId);
      return next;
    });
  }

  // Auto-pin roles that gain their first permission via toggle.
  async function togglePerm(roleId: string, permKey: keyof Permissions, value: boolean) {
    if (value) addRoleToPermQuadrant(roleId);
    const role = roles?.find(r => r.id === roleId);
    if (!role) return;
    const newPerms: Partial<Permissions> = { ...role.permissions, [permKey]: value };
    setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: newPerms }) ?? prev);
    await api.updateRolePermissions(guildId, roleId, role.name, newPerms).catch(() => {
      setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: role.permissions }) ?? prev);
    });
  }

  function refreshAlbionLinks() {
    api.listAlbionLinks(guildId).then(r => {
      setAlbionLinks(r.links);
    }).catch(() => {});
  }

  async function addAlbionLink() {
    const name = newLinkName.trim();
    if (!name || !newLinkRegion) return;
    setLinkErr(null);
    try {
      await api.addAlbionLink(guildId, name, newLinkRegion);
      setNewLinkName("");
      const [g, allies] = await Promise.all([api.guildInfo(guildId), api.guildAllies(guildId)]);
      setGuild(g);
      setAllyGuilds(allies);
      refreshAlbionLinks();
    } catch (e: any) {
      const msg = String((e as Error)?.message ?? e);
      setLinkErr(msg.includes("404") ? t("albionGuildNotFound") : msg);
    }
  }

  async function removeAlbionLink(albionGid: string) {
    setLinkErr(null);
    try {
      await api.removeAlbionLink(guildId, albionGid);
      const [g, allies] = await Promise.all([api.guildInfo(guildId), api.guildAllies(guildId)]);
      setGuild(g);
      setAllyGuilds(allies);
      refreshAlbionLinks();
    } catch {
      refreshAlbionLinks();
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

  async function saveEventsChannel(value: string) {
    setEventsChannelId(value);
    setEventsEnabled(!!value);
    await api.updateGuildSettings(guildId, { events_channel_id: value || null });
  }

  // Sala de revisÃ£o: ao entrar em review, o bot abre uma thread aqui e posta o
  // embed ðŸ“‘ dentro. Vazio = embed fica no canal de eventos como mensagem simples.
  async function saveReviewChannel(value: string) {
    setReviewChannelId(value);
    await api.updateGuildSettings(guildId, { event_review_channel_id: value || null });
  }

  // Canal dedicado de threads de regear: ao entrar em IN_PROGRESS o bot cria
  // uma thread aqui; prints postadas nela viram regears atrelados ao evento.
  // Vazio = sem thread automÃ¡tica (regears soltos caem na fila geral sem tag).
  async function saveRegearThreadChannel(value: string) {
    setRegearThreadChannelId(value);
    await api.updateGuildSettings(guildId, { regear_thread_channel_id: value || null });
  }

  // Canal dedicado de threads de lootlog por evento: o bot cria uma thread aqui
  // ao entrar em IN_PROGRESS; .csv do lootlogger postado nela vira submissÃ£o
  // atrelada ao evento. Vazio = sem thread automÃ¡tica.
  async function saveLootlogThreadChannel(value: string) {
    setLootlogThreadChannelId(value);
    await api.updateGuildSettings(guildId, { lootlog_thread_channel_id: value || null });
  }

  // Toggle mestre de Eventos: liga reenviando o canal jÃ¡ escolhido (sem
  // precisar reselecionar); desliga mandando null pro servidor SEM apagar
  // `eventsChannelId` local, que fica de memÃ³ria pra prÃ³xima vez que ligar.
  function toggleEventsFeature(v: boolean) {
    setEventsEnabled(v);
    if (v) {
      if (eventsChannelId) api.updateGuildSettings(guildId, { events_channel_id: eventsChannelId });
      else if (!eventsChannelId) openFeat("events")
    } else {
      api.updateGuildSettings(guildId, { events_channel_id: null });
    }
  }

  async function saveVoiceChannel(value: string) {
    setVoiceCtaChannelId(value);
    await api.updateGuildSettings(guildId, { voice_cta_channel_id: value || null });
  }

  async function saveNodesCalendar(value: string) {
    setNodesCalendarChannelId(value);
    setNodesEnabled(!!value);
    await api.updateGuildSettings(guildId, { nodes_calendar_channel_id: value || null });
  }

  // Canal de logs do bot. null/vazio = o bot cria e mantÃ©m um canal prÃ³prio
  // "logs-bot" admin-only; escolher um canal aqui faz o bot usar esse. Cursor
  // de histÃ³rico Ã© inicializado no backend sÃ³ na 1Âª vez (nÃ£o despeja histÃ³rico
  // ao trocar de canal).
  async function saveLogsChannel(value: string) {
    setLogsChannelId(value);
    await api.updateGuildSettings(guildId, { logs_channel_id: value || null });
  }

  function toggleNodesFeature(v: boolean) {
    setNodesEnabled(v);
    if (v) {
      if (nodesCalendarChannelId) api.updateGuildSettings(guildId, { nodes_calendar_channel_id: nodesCalendarChannelId });
      else if (!nodesCalendarChannelId) openFeat("nodes")
    } else {
      api.updateGuildSettings(guildId, { nodes_calendar_channel_id: null });
    }
  }

  // â”€â”€ Battle feed: mensageiro de batalhas â”€â”€
  async function saveBattleFeedChannel(value: string) {
    setBattleFeedChannelId(value);
    setBattleFeedEnabled(!!value);
    await api.updateGuildSettings(guildId, { battle_feed_channel_id: value || null });
  }

  async function saveBattleFeedMinPlayers(value: string) {
    setBattleFeedMinPlayers(value);
    const n = parseInt(value, 10);
    if (!isNaN(n) && n > 0) {
      await api.updateGuildSettings(guildId, { battle_feed_min_players: n });
    }
  }

  function toggleBattleFeedFeature(v: boolean) {
    setBattleFeedEnabled(v);
    if (v) {
      if (battleFeedChannelId) api.updateGuildSettings(guildId, { battle_feed_channel_id: battleFeedChannelId });
      else if (!battleFeedChannelId) openFeat("battlefeed")
    } else {
      api.updateGuildSettings(guildId, { battle_feed_channel_id: null });
    }
  }

  // â”€â”€ Juicy Kills: kills lethais acima dos filtros configurados â”€â”€
  async function saveJuicyKillChannel(value: string) {
    setJuicyKillChannelId(value);
    setJuicyKillEnabled(!!value);
    await api.updateGuildSettings(guildId, { juicy_kill_channel_id: value || null });
  }

  function toggleJuicyKillFeature(v: boolean) {
    setJuicyKillEnabled(v);
    if (v) {
      if (juicyKillChannelId) api.updateGuildSettings(guildId, { juicy_kill_channel_id: juicyKillChannelId });
      else if (!juicyKillChannelId) openFeat("juicykills")
    } else {
      api.updateGuildSettings(guildId, { juicy_kill_channel_id: null });
    }
  }

async function saveJuicyKillMinSilver() {
    const n = Math.max(JUICY_KILL_HARD_FLOOR, Number(juicyKillMinSilver) || 0);
    setJuicyKillMinSilver(String(n));
    await api.updateGuildSettings(guildId, { juicy_kill_min_silver: n });
  }

  async function saveJuicyKillMinFame() {
    const n = Math.max(0, Number(juicyKillMinFame) || 0);
    setJuicyKillMinFame(String(n));
    await api.updateGuildSettings(guildId, { juicy_kill_min_fame: n || null });
  }

  async function saveJuicyKillRegions(regions: string[]) {
    setJuicyKillRegions(regions);
    await api.updateGuildSettings(guildId, { juicy_kill_regions: regions });
  }

  // â”€â”€ Nodes: tipos de node + mapas (a antiga aba Nodes virou esta seÃ§Ã£o) â”€â”€
  async function refreshNodeDefs() {
    try {
      const [d, m] = await Promise.all([api.listNodeDefs(), api.listNodeMaps()]);
      setNodeDefs(d); setNodeDefsLoaded(true); setNodeMaps(m); setNodesErr(null);
    } catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  async function addNodeDef() {
    if (!ndName.trim()) return;
    try {
      await api.upsertNodeDef({
        name: ndName.trim(),
        emoji: ndEmoji.trim() || null,
        weight: (Number(ndWeight) || 100) / 100,
        sort: 0,
      });
      setNdName(""); setNdEmoji(""); setNdWeight("100");
      await refreshNodeDefs();
    } catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  function startEditNodeDef(d: NodeDef) {
    setNdEditingId(d.id);
    setNdEditName(d.name);
    setNdEditEmoji(d.emoji ?? "");
    setNdEditWeight(String(Math.round(d.weight * 100)));
  }

  async function saveNodeDef(id: number) {
    try {
      await api.updateNodeDef(id, {
        // name vazio â†’ null (backend: null = nÃ£o mexer) â€” mantÃ©m o nome atual.
        name: ndEditName.trim() || null,
        emoji: ndEditEmoji.trim() || null,
        weight: (Number(ndEditWeight) || 100) / 100,
      });
      setNdEditingId(null);
      await refreshNodeDefs();
    } catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  async function removeNodeDef(name: string) {
    try { await api.removeNodeDef(name); await refreshNodeDefs(); }
    catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  async function addNodeMapExtra() {
    if (!nmName.trim()) return;
    try { await api.addNodeMap(nmName.trim()); setNmName(""); await refreshNodeDefs(); }
    catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  async function removeNodeMapExtra(name: string) {
    try { await api.removeNodeMap(name); await refreshNodeDefs(); }
    catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  async function toggleNodeExclusion(name: string) {
    if (!nodeMaps) return;
    const isExcl = nodeMaps.exclusions.includes(name);
    try {
      // reverte exclusÃ£o = re-adiciona como extra (backend dedupe por nome);
      // ocultar = remove (cai em exclusions).
      await (isExcl ? api.addNodeMap(name) : api.removeNodeMap(name));
      await refreshNodeDefs();
    } catch (e: any) { setNodesErr(String(e?.message ?? e)); }
  }

  // Auto-save: trial role/percent aplicados na hora (role no onChange, % no
  // onBlur). Params explÃ­citos porque o onChange do role precisa do percent
  // atual antes do setTrialRoleId commitar no estado.
  async function saveTrialSettings(roleId: string, percent: string) {
    const tp = Math.max(0, Math.min(100, parseInt(percent, 10) || 0));
    setTrialPercent(String(tp));
    await api.updateGuildSettings(guildId, {
      trial_role_id: roleId || null,
      trial_percent: tp,
    });
  }

  async function saveLootsplitMode(value: string) {
    setLootsplitMode(value);
    await api.updateGuildSettings(guildId, { lootsplit_mode: value });
  }

  // Auto-save no blur: clamp 0-100. 0 = remove a chave (default = sem taxa).
  async function saveGuildTax() {
    const n = Math.max(0, Math.min(100, parseInt(guildTaxPct, 10) || 0));
    setGuildTaxPct(String(n));
    await api.updateGuildSettings(guildId, { guild_tax_percent: n > 0 ? n : null });
  }

  // Auto-save do scout bonus source ("node" | "tab"). Ver
  // events.get_scout_bonus_source no backend.
  async function saveScoutBonusSource(value: string) {
    setScoutBonusSource(value);
    await api.updateGuildSettings(guildId, { scout_bonus_source: value });
  }

  // Auto-save no blur: clamp 0-100. 0 = sem scout percent (default).
  async function saveScoutPercent() {
    const n = Math.max(0, Math.min(100, parseInt(scoutPercent, 10) || 0));
    setScoutPercent(String(n));
    await api.updateGuildSettings(guildId, { scout_percent: n > 0 ? n : null });
  }

  // Toggle de um gatilho de ping @everyone. Salva a lista inteira (a chave no
  // backend Ã© a lista completa, nÃ£o um toggle individual) â€” [] explÃ­cito = tudo
  // off, distinto de "nunca configurado" (default). Status triggers (created/
  // in_progress/review) continuam bumpando o embed silenciosamente mesmo off;
  // sÃ³ o @evenne desliga. t10min off = sem bump (pure ping).
  async function togglePingTrigger(key: string) {
    const next = pingTriggers.includes(key)
      ? pingTriggers.filter(k => k !== key)
      : [...pingTriggers, key];
    setPingTriggers(next);
    await api.updateGuildSettings(guildId, { events_ping_triggers: next })
      .then(() => { setPingTriggersSaved(true); setTimeout(() => setPingTriggersSaved(false), 1500); });
  }

  // Auto-save: mÃ­nimo aplicado no blur. A quantidade mÃ¡xima Ã© irrestrita.
  async function saveSignupMinimum() {
    const minN = signupMinBuilds === "" ? null : Math.max(1, parseInt(signupMinBuilds, 10) || 0);
    await api.updateGuildSettings(guildId, { signup_min_builds: minN });
  }

  // Auto-save de regear: aplica local (otimista) + manda sÃ³ o campo mudado pro
  // servidor. O snapshot saneado que volta corrige normalizaÃ§Ãµes (dedup de
  // canal, clamp de %, validaÃ§Ã£o de categoria). Sem botÃ£o Salvar â€” toda
  // mudanÃ§a na bracket persiste na hora.
  function pushRegear(patch: Partial<RegearSettings>) {
    setRegear(r => (r ? { ...r, ...patch } : r));
    api.setRegearSettings(patch)
      .then(setRegear)
      .catch(e => alert(String((e as Error)?.message ?? e)));
  }

  function toggleRegearCat(cat: string) {
    if (!regear) return;
    const has = regear.enabled_categories.includes(cat);
    const next = has ? regear.enabled_categories.filter(c => c !== cat) : [...regear.enabled_categories, cat];
    // "bag" saiu das categorias cobertas â€” limpa se ainda houver de dados antigos.
    pushRegear({ enabled_categories: next.filter(c => c !== "bag") });
  }
  function toggleRegearApprover(roleId: string) {
    if (!regear) return;
    const id = Number(roleId);
    const has = regear.approver_role_ids.includes(id);
    pushRegear({ approver_role_ids: has ? regear.approver_role_ids.filter(x => x !== id) : [...regear.approver_role_ids, id] });
  }

  function addRegearChannel(channelId: string) {
    if (!regear) return;
    pushRegear({ channels: [...regear.channels, { channel_id: channelId, coverage_pct: 100 }] });
  }
  function removeRegearChannel(channelId: string) {
    if (!regear) return;
    pushRegear({ channels: regear.channels.filter(c => c.channel_id !== channelId) });
  }
  // % de cobertura: edita local sÃ³ no input (evita corrida de keystroke); commit
  // no blur manda o estado final dos canais pro servidor.
  function setRegearChannelPct(channelId: string, pct: number) {
    setRegear(r => r ? { ...r, channels: r.channels.map(c => c.channel_id === channelId ? { ...c, coverage_pct: pct } : c) } : r);
  }
  function commitRegearChannelPct() {
    if (regear) pushRegear({ channels: regear.channels });
  }

  // Toggle mestre de Regear: liga/desliga o flag persistido `enabled`. Canais
  // locais ficam de memÃ³ria (nÃ£o apagam ao desligar) â€” religar reusa sem
  // reescolher. Sem canal de thread ainda â†’ abre a seÃ§Ã£o pra escolher um.
  function toggleRegearFeature(v: boolean) {
    setRegearEnabled(v);
    if (!regear) { openFeat("regear"); return; }
    pushRegear({ enabled: v });
    if (v && !regearThreadChannelId) openFeat("regear")
  }

  async function saveLootLog() {
    try {
      const cfg = await api.setLootLogSettings({ logger_percent: llPct });
      setLlPct(cfg.logger_percent);
    } catch (e) { alert(String((e as Error)?.message ?? e)); }
  }

  async function toggleLootlogFeature(v: boolean) {
    setLootlogEnabled(v);
    try {
      await api.setLootLogSettings({ enabled: v });
    } catch (e) { alert(String((e as Error)?.message ?? e)); }
  }

  function toggleBotLogsFeature(v: boolean) {
    setBotLogsEnabled(v);
    api.updateGuildSettings(guildId, { bot_logs_enabled: v });
  }

  function saveGates(next: Record<string, string[]>) {
    setEventRoleGates(next);
    api.updateGuildSettings(guildId, { event_role_gates: next }).catch(() => setEventRoleGates(eventRoleGates));
  }

  function toggleGateRole(fnLower: string, roleId: string) {
    const cur = eventRoleGates[fnLower] ?? [];
    const next = cur.includes(roleId) ? cur.filter(r => r !== roleId) : [...cur, roleId];
    saveGates({ ...eventRoleGates, [fnLower]: next });
  }

  function addGate(fnName: string) {
    const fnLower = fnName.toLowerCase();
    if (eventRoleGates[fnLower]) return;
    saveGates({ ...eventRoleGates, [fnLower]: [] });
  }

  function removeGate(fnLower: string) {
    const next = { ...eventRoleGates };
    delete next[fnLower];
    saveGates(next);
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
      // "Todos" e "Nenhum" sÃ£o exclusivos entre si e com qualquer guilda
      // especÃ­fica â€” selecionar um deles substitui a seleÃ§Ã£o inteira.
      next = prev.includes(key) ? [] : [key];
    } else {
      // Selecionar uma guilda especÃ­fica sai do modo "Todos"/"Nenhum" e
      // passa a montar uma lista prÃ³pria.
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

  // Agrupamento por dependÃªncia: cada comando vive dentro da feature da qual
  // depende (balance nÃ£o existe sem economia, unregister sem register, etc.).
  const registerCmd = commands?.find(c => c.name === "register") ?? null;
  const economyCmds = (commands ?? []).filter(c => c.category === "economy");
  const miscCmds = (commands ?? []).filter(c => c.category === "miscellaneous" && !CHILD_COMMANDS.has(c.name));
  const economyOnCount = economyCmds.filter(c => c.enabled).length;

  // Master toggle da economia â€” liga/desliga o grupo inteiro de comandos
  // (o estado "on" da feature = pelo menos um comando ativo).
  async function toggleEconomyAll(enabled: boolean) {
    for (const c of economyCmds) {
      if (c.enabled !== enabled) await toggleCommand(c.name, enabled);
    }
  }

  const chName = (id: string) => {
    const c = channels?.find(ch => ch.id === id);
    return c ? `#${c.name}` : "â€¦";
  };

  // "Nenhum" sozinho (ou lista vazia) = nenhum aliado passa â€” nÃ£o tem sentido
  // mostrar o cargo de aliados nesse caso, ele nunca seria usado.
  const anyAlliesAllowed = allyAllowedGuilds.includes(ALL_ALLIES)
    || allyAllowedGuilds.some(k => k !== NO_ALLIES && k !== ALL_ALLIES);

  const channelSelect = (
    value: string,
    onChange: (v: string) => void,
    extraClass = "",
    list: { id: string; name: string }[] | null = channels,
  ) => (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={!list && !channelsErr}
      className={`w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200 ${extraClass}`}
    >
      <option value="">{t("eventsChannelNone")}</option>
      {(list ?? []).sort((a, b) => a.name.localeCompare(b.name)).map(c => (
        <option key={c.id} value={c.id}>{c.name}</option>
      ))}
    </select>
  );

  // Linha de comando genÃ©rica (toggle + expandir p/ cargos) â€” usada dentro
  // das features Economia e Diversos.
  const cmdRow = (cmd: BotCommand) => {
    const isExpanded = expanded.has(cmd.name);
    return (
      <div key={cmd.name} className="border-b border-zinc-800/60 last:border-0">
        <div className="flex items-center gap-3 py-2.5">
          <Switch checked={cmd.enabled} onChange={v => toggleCommand(cmd.name, v)} />
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
          </div>
        )}
      </div>
    );
  };

  const unregisterCmd = commands?.find(c => c.name === "unregister") ?? null;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">{t("config")}</h1>
      <p className="text-sm text-zinc-500 mb-8">
        {t("serverLabel")} <strong className="text-zinc-300">{guild.name}</strong>
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Status do bot â€” full width */}
        <Panel className="p-5 lg:col-span-2">
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

          {/* Guildas de Albion â€” uma Ãºnica lista (primÃ¡ria + adicionais).
              AlianÃ§as com 300+ membros operam em vÃ¡rias guildas sob o mesmo
              Discord. A primeira guilda adicionada vira a primÃ¡ria. */}
          <div className="mt-3 mb-2">
            <label className="block text-xs text-zinc-500 mb-1">{t("albionGuildTitle")}</label>
            {albionLinks.length === 0 ? (
              <p className="text-[11px] text-zinc-600">{t("albionLinkedEmpty")}</p>
            ) : (
              <ul className="space-y-1.5">
                {albionLinks.map(l => (
                  <li key={l.albion_guild_id} className="flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5">
                    <span className="text-xs text-zinc-200 flex-1 truncate">
                      {l.verified && l.alliance_name ? `[${l.alliance_name}] ` : ""}{l.albion_guild_name}
                    </span>
                    {l.verified === false && (
                      <span
                        className="text-amber-400 text-xs"
                        title={t("albionLinkUnverified")}
                        aria-label={t("albionLinkUnverified")}
                      >
                        <i className="ti ti-alert-triangle" aria-hidden="true" />
                      </span>
                    )}
                    <span className="text-[10px] uppercase tracking-wide text-zinc-500">{REGION_LABELS[lang][l.region as keyof typeof REGION_LABELS[typeof lang]] ?? l.region}</span>
                    <button
                      type="button"
                      onClick={() => removeAlbionLink(l.albion_guild_id)}
                      className="text-xs text-zinc-500 hover:text-red-400"
                      title={t("remove")}
                    >
                      <i className="ti ti-x" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="flex gap-2 mb-1">
            <input
              value={newLinkName}
              onChange={e => { setNewLinkName(e.target.value); setLinkErr(null); }}
              onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addAlbionLink(); } }}
              placeholder={t("guildNamePlaceholder")}
              className="flex-1 rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-200 outline-none focus:border-amber-500 placeholder:text-zinc-600"
            />
            <select
              value={newLinkRegion}
              onChange={e => setNewLinkRegion(e.target.value)}
              className="w-28 shrink-0 rounded-md border border-zinc-700 bg-zinc-800 px-1 py-1 text-xs text-zinc-200"
            >
              <option value="">{t("albionRegionPlaceholder")}</option>
              {ALBION_REGIONS.map(r => (
                <option key={r} value={r}>{REGION_LABELS[lang][r]}</option>
              ))}
            </select>
            <button type="button" onClick={addAlbionLink} disabled={!newLinkName.trim() || !newLinkRegion} className="btn btn-primary text-xs px-3 disabled:opacity-40">
              <i className="ti ti-plus" aria-hidden="true" />
            </button>
          </div>
          {linkErr && <p className="text-xs text-red-400 mb-2">{linkErr}</p>}

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
        </Panel>

        {/* Eventos â€” full width (seÃ§Ã£o grande) */}
            <FeatureRow
              icon="ti-calendar-event" iconColor="text-indigo-400"
              title={t("events")} desc={t("featEventsDesc")}
              on={eventsEnabled}
              onToggle={toggleEventsFeature}
              disabled={!hasGuild}
              fullWidth
              statusHint={!hasGuild ? t("needsGuildFirst") : (eventsEnabled && eventsChannelId ? chName(eventsChannelId) : t("featNeedsSetup"))}
              open={featOpen.has("events")} onOpen={() => toggleFeat("events")}
            >
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">{t("eventsPostingChannelLabel")}</label>
                  <p className="text-[11px] text-zinc-600 mb-2">{t("eventsChannelDesc")}</p>
                  {channelSelect(eventsChannelId, saveEventsChannel)}
                </div>
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">{t("reviewChannelLabel")}</label>
                  <p className="text-[11px] text-zinc-600 mb-2">{t("reviewChannelDesc")}</p>
                  {channelSelect(reviewChannelId, saveReviewChannel)}
                </div>
                <div>
                  <label className="block text-xs text-zinc-400 mb-1">{t("voiceCtaTitle")}</label>
                  <p className="text-[11px] text-zinc-600 mb-2">{t("voiceCtaDesc")}</p>
                  {channelSelect(voiceCtaChannelId, saveVoiceChannel, "", voiceChannels)}
                </div>
              </div>
              {channelsErr && <p className="text-xs text-red-400 mb-2">{t("eventsChannelLoadError")}</p>}

              {/* InscriÃ§Ãµes & Gates â€” dependem de eventos existirem */}
              <div className="mt-4 pt-4 border-t border-zinc-800/60">
                <div className="flex items-center gap-2 mb-2">
                  <i className="ti ti-shield-lock text-amber-400 text-base" aria-hidden="true" />
                  <h3 className="text-sm font-semibold text-zinc-100">{t("signupSettingsTitle")}</h3>
                </div>
                <p className="text-xs text-zinc-500 mb-3">{t("roleGatesDesc")}</p>

                {Object.keys(eventRoleGates).length === 0 && (
                  <p className="text-xs text-zinc-600 italic mb-3">{t("roleGatesEmpty")}</p>
                )}

                <div className="flex flex-wrap gap-2 mb-4">
                  {Object.entries(eventRoleGates).map(([fnLower, roleIds]) => {
                    const display = gameRoles.find(gr => gr.name.toLowerCase() === fnLower)?.name ?? fnLower;
                    return (
                      <div key={fnLower} className="inline-flex flex-col w-fit max-w-[260px] rounded-lg border border-zinc-800 bg-zinc-900/80 p-3">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-xs font-semibold text-zinc-200">{display}</span>
                          <button
                            type="button" onClick={() => removeGate(fnLower)}
                            className="text-xs text-zinc-500 hover:text-red-400"
                            title={t("remove")}
                          >
                            <i className="ti ti-x" aria-hidden="true" />
                          </button>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {(roles ?? []).map(r => (
                            <button
                              key={r.id} type="button" onClick={() => toggleGateRole(fnLower, r.id)}
                              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                                roleIds.includes(r.id)
                                  ? "border-amber-500 bg-amber-500/15 text-amber-300"
                                  : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                              }`}
                            >
                              {r.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>

                <select
                  value="" onChange={e => e.target.value && addGate(e.target.value)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-400"
                >
                  <option value="">{t("roleGatesAddFunction")}</option>
                  {gameRoles
                    .filter(gr => !eventRoleGates[gr.name.toLowerCase()])
                    .map(gr => <option key={gr.id} value={gr.name}>{gr.name}</option>)}
                </select>

                <div className="flex items-center gap-3 mt-5 mb-2">
                  <i className="ti ti-target text-emerald-400 text-base" aria-hidden="true" />
                  <h3 className="text-sm font-semibold text-zinc-100">{t("cfgSignupMin")}</h3>
                </div>
                <p className="text-xs text-zinc-500 mb-3">{t("cfgSignupMinHint")}</p>
                <div className="flex items-end gap-2 max-w-xs">
                  <label className="w-full">
                    <span className="block text-xs text-zinc-500 mb-1">{t("cfgSignupMin")}</span>
                    <input
                      type="number" min={1} value={signupMinBuilds}
                      onChange={e => setSignupMinBuilds(e.target.value)}
                      onBlur={() => void saveSignupMinimum()}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
                    />
                  </label>
                </div>
              </div>

              {/* Trial â€” desconto aplicado no freeze dos eventos por voz */}
              <div className="mt-4 pt-4 border-t border-zinc-800/60">
                <div className="flex items-center gap-2 mb-2">
                  <i className="ti ti-egg text-emerald-400 text-base" aria-hidden="true" />
                  <h3 className="text-sm font-semibold text-zinc-100">{t("trialRoleLabel")}</h3>
                </div>
                <p className="text-xs text-zinc-500 mb-2">{t("trialSettingsHint")}</p>
                <select
                  value={trialRoleId}
                  onChange={e => { setTrialRoleId(e.target.value); void saveTrialSettings(e.target.value, trialPercent); }}
                  disabled={!roles}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200 mb-3"
                >
                  <option value="">{t("trialRoleNone")}</option>
                  {(roles ?? []).map(r => (
                    <option key={r.id} value={r.id}>{r.name}</option>
                  ))}
                </select>
                <label className="block text-xs text-zinc-400 mb-1">{t("trialPercentLabel")}</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number" min={0} max={100} value={trialPercent}
                    onChange={e => setTrialPercent(e.target.value)}
                    onBlur={() => void saveTrialSettings(trialRoleId, trialPercent)}
                    className="w-24 bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
                  />
                  <span className="text-xs text-zinc-500">%</span>
                </div>
              </div>

              {/* Lootsplit mode â€” regear Ã© sempre calculado; isto sÃ³ decide como
                  a tab vira split (ver events.get_lootsplit_mode no backend). */}
              <div className="mt-4 pt-4 border-t border-zinc-800/60">
                <div className="flex items-center gap-2 mb-2">
                  <i className="ti ti-coins text-emerald-400 text-base" aria-hidden="true" />
                  <h3 className="text-sm font-semibold text-zinc-100">{t("lootsplitModeTitle")}</h3>
                </div>
                <p className="text-xs text-zinc-500 mb-3">{t("lootsplitModeDesc")}</p>
                <div className="grid grid-cols-2 gap-2">
                  {LOOTSPLIT_MODES.map(({ mode, labelKey, descKey }) => (
                    <button
                      key={mode} type="button"
                      onClick={() => { void saveLootsplitMode(mode); }}
                      className={`text-left text-xs px-3 py-2 rounded-md border transition-colors ${
                        lootsplitMode === mode
                          ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                          : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                      }`}
                    >
                      <span className="block font-semibold">{t(labelKey)}</span>
                      <span className="block text-[10px] opacity-80 mt-0.5">{t(descKey)}</span>
                    </button>
                  ))}
                </div>

                {/* Taxa da guilda â€” % da tab debitada pro banco antes do pool de
                    participantes. SÃ³ vale em modos com split; "none" ignora. */}
                <div className={`mt-3 ${lootsplitMode === "none" ? "opacity-40 pointer-events-none" : ""}`}>
                  <label className="block text-xs text-zinc-400 mb-1">{t("guildTaxLabel")}</label>
                  <p className="text-[11px] text-zinc-600 mb-2">{t("guildTaxHint")}</p>
                  <div className="flex items-center gap-2">
                    <input
                      type="number" min={0} max={100} value={guildTaxPct}
                      onChange={e => setGuildTaxPct(e.target.value)}
                      onBlur={() => void saveGuildTax()}
                      className="w-24 bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
                    />
                    <span className="text-xs text-zinc-500">%</span>
                  </div>
                </div>
              </div>

              {/* Pings @everyone â€” quando o mass-info deleta a embed e reenvia
                  pingando @everyone. Plataforma configurÃ¡vel: 4 gatilhos, default
                  ligado nos 3 primeiros (review off). O bot sÃ³ executa o que o
                  site decide aqui (ver cogs/events.py + event_signups.py). */}
              <div className="mt-4 pt-4 border-t border-zinc-800/60">
                <div className="flex items-center gap-2 mb-2">
                  <i className="ti ti-bell text-amber-400 text-base" aria-hidden="true" />
                  <h3 className="text-sm font-semibold text-zinc-100">{t("pingTriggersTitle")}</h3>
                  {pingTriggersSaved && <i className="ti ti-check text-emerald-400 text-xs" aria-hidden="true" />}
                </div>
                <p className="text-xs text-zinc-500 mb-3">{t("pingTriggersDesc")}</p>
                <div className="grid grid-cols-2 gap-2">
                  {PING_TRIGGERS.map(({ key, labelKey, descKey }) => {
                    const active = pingTriggers.includes(key);
                    return (
                      <button
                        key={key} type="button"
                        onClick={() => { void togglePingTrigger(key); }}
                        className={`flex items-center gap-2 text-left text-xs px-3 py-2 rounded-md border transition-colors ${
                          active
                            ? "border-amber-500 bg-amber-500/15 text-amber-300"
                            : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                        }`}
                      >
                        <span className={`shrink-0 ${active ? "ti ti-bell-filled" : "ti ti-bell-off"} text-sm`} aria-hidden="true" />
                        <span className="flex-1 min-w-0">
                          <span className="block font-semibold">{t(labelKey)}</span>
                          <span className="block text-[11px] opacity-80 mt-0.5">{t(descKey)}</span>
                        </span>
                      </button>
                    );
                    })}
                  </div>
                </div>
            </FeatureRow>

            {/* â”€â”€ Registro: /register + tudo que depende dele â”€â”€ */}
            <FeatureRow
              icon="ti-user-check" iconColor="text-emerald-400"
              title={t("featRegisterTitle")} desc={t("featRegisterDesc")}
              on={registerCmd?.enabled ?? false}
              onToggle={v => { if (registerCmd) toggleCommand("register", v); }}
              disabled={!hasGuild}
              statusHint={hasGuild ? (registerCmd ? rolePreviewLabel(registerCmd) : undefined) : t("needsGuildFirst")}
              open={featOpen.has("register")} onOpen={() => toggleFeat("register")}
            >
              {!registerCmd ? (
                <p className="text-xs text-zinc-500">{t("loading")}</p>
              ) : (
                <div className={registerCmd.enabled ? "" : "opacity-40 pointer-events-none"}>
                  <p className="text-[11px] text-zinc-600 mb-1.5">{t("whoCanUse")}</p>
                  {roleChipsFor(registerCmd.allowed_roles, key => toggleCommandRole("register", key))}

                  <div className="mt-3 pt-3 border-t border-zinc-800/60">
                    <p className="text-[11px] text-zinc-600 mb-1.5">{t("registerOthersTitle")}</p>
                    <p className="text-[11px] text-zinc-600 mb-2">{t("registerOthersDesc")}</p>
                    {roleChipsFor(registerOthersRoles, toggleRegisterOthersRole)}
                  </div>

                  <div className="mt-3 pt-3 border-t border-zinc-800/60">
                    <p className="text-[11px] text-zinc-600 mb-1.5">{t("registerRoleTitle")}</p>
                    <p className="text-[11px] text-zinc-600 mb-2">{t("registerRoleDesc")}</p>
                    <div className="flex gap-2">
                      <select
                        value={registerRoleId}
                        onChange={e => saveRegisterRole(e.target.value)}
                        disabled={savingRole || !registerCmd.enabled}
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
                            disabled={savingAllyRole || !registerCmd.enabled}
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

                  {unregisterCmd && (
                    <div className="mt-3 pt-3 border-t border-zinc-800/60">
                      <div className="flex items-center gap-2 mb-2">
                        <i className="ti ti-corner-down-right text-zinc-600" aria-hidden="true" />
                        <Switch checked={unregisterCmd.enabled} onChange={v => toggleCommand("unregister", v)} />
                        <span className="text-sm text-zinc-100 font-mono">/{unregisterCmd.name}</span>
                        <span className="text-xs text-zinc-500">
                          {CMD_DESC_KEYS[unregisterCmd.name] ? t(CMD_DESC_KEYS[unregisterCmd.name]) : unregisterCmd.description}
                        </span>
                      </div>
                      <div className={unregisterCmd.enabled ? "" : "opacity-40 pointer-events-none"}>
                        <p className="text-[11px] text-zinc-600 mb-1.5">{t("whoCanUse")}</p>
                        {roleChipsFor(unregisterCmd.allowed_roles, key => toggleCommandRole("unregister", key))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </FeatureRow>

            {/* â”€â”€ Economia: master toggle + comandos do grupo â”€â”€ */}
            <FeatureRow
              icon="ti-coin" iconColor="text-amber-400"
              title={t("cmdCategoryEconomy")} desc={t("featEconomyDesc")}
              on={economyOnCount > 0}
              onToggle={v => { void toggleEconomyAll(v); }}
              statusHint={economyCmds.length ? `${economyOnCount}/${economyCmds.length}` : undefined}
              fullWidth
              open={featOpen.has("economy")} onOpen={() => toggleFeat("economy")}
            >
              {commands === null ? (
                <p className="text-xs text-zinc-500">{t("loading")}</p>
              ) : (
                <div className="flex flex-col">
                  {economyCmds.map(cmdRow)}
                </div>
              )}
            </FeatureRow>

            {/* â”€â”€ Regear â”€â”€ */}
            <FeatureRow
              icon="ti-receipt-refund" iconColor="text-emerald-400"
              title={t("regcfgTitle")} desc={t("featRegearDesc")}
              on={regearEnabled}
              onToggle={v => { void toggleRegearFeature(v); }}
              disabled={!hasGuild}
              statusHint={regearEnabled && regear?.channels.length
                ? `${regear.channels.length} ${regear.channels.length === 1 ? t("regcfgChannelSingular") : t("regcfgChannelPlural")}`
                : t("featNeedsSetup")}
              fullWidth
              open={featOpen.has("regear")} onOpen={() => toggleFeat("regear")}
            >
              <p className="text-xs text-zinc-500 mb-4">{t("regcfgDesc")}</p>
              {!regear ? (
                <p className="text-xs text-zinc-500">{t("loading")}</p>
              ) : (
                <div className="space-y-4">
                  {/* Canal dedicado de threads de regear por evento (landmark) â€”
                      primÃ¡rio: Ã© ele que libera os canais extras abaixo. Setting
                      top-level da guilda, nÃ£o da blob regear.channels, entÃ£o fica
                      sempre interativo (nÃ£o depende do master toggle). */}
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">{t("regearThreadChannelLabel")}</label>
                    {channelSelect(regearThreadChannelId, saveRegearThreadChannel, "mb-1")}
                    <p className="text-[11px] text-zinc-600">{t("regearThreadChannelDesc")}</p>
                  </div>

                  {/* Canais extras monitorados â€” cada um com sua prÃ³pria % de
                      cobertura. SÃ³ libera depois do canal de threads acima
                      definido (Ã© ele que atrela os regears aos eventos; sem ele
                      nÃ£o faz sentido configurar canais extras soltos). Desligado
                      no master toggle = dimmed tambÃ©m (evita re-ligar via Salvar
                      sem querer, ver saveRegear/toggleRegearFeature). */}
                  <div className={regearEnabled && regearThreadChannelId ? "" : "opacity-40 pointer-events-none"}>
                    <label className="block text-xs text-zinc-500 mb-2">{t("regcfgChannel")}</label>
                    {!regearThreadChannelId && (
                      <p className="text-[11px] text-amber-500 mb-2">{t("regcfgExtraNeedsThread")}</p>
                    )}
                    <div className="space-y-2 mb-2">
                      {regear.channels.map(c => (
                        <div key={c.channel_id} className="flex items-center gap-2">
                          <span className="flex-1 text-xs text-zinc-300 bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1.5 truncate">
                            {chName(c.channel_id)}
                          </span>
                          <input
                            type="number" min={0} max={100}
                            value={c.coverage_pct}
                            onChange={e => setRegearChannelPct(c.channel_id, Math.max(0, Math.min(100, parseInt(e.target.value, 10) || 0)))}
                            onBlur={commitRegearChannelPct}
                            className="w-16 bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
                          />
                          <span className="text-xs text-zinc-500">%</span>
                          <button type="button" onClick={() => removeRegearChannel(c.channel_id)} className="text-zinc-500 hover:text-red-400">
                            <i className="ti ti-x" aria-hidden="true" />
                          </button>
                        </div>
                      ))}
                    </div>
                    <select
                      value="" onChange={e => { if (e.target.value) addRegearChannel(e.target.value); }}
                      disabled={!channels && !channelsErr}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-400"
                    >
                      <option value="">{t("regcfgAddChannel")}</option>
                      {(channels ?? [])
                        .filter(ch => !regear.channels.some(c => c.channel_id === ch.id))
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                    <p className="text-[11px] text-zinc-600 mt-1">{t("regcfgCoverageHint")}</p>
                  </div>

                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">{t("regcfgCategories")}</label>
                    <div className="flex flex-wrap gap-1.5">
                      {REGEAR_CATS.map(c => {
                        const active = regear.enabled_categories.includes(c.key);
                        return (
                          <button
                            key={c.key} type="button"
                            onClick={() => toggleRegearCat(c.key)}
                            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                              active
                                ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                                : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                            }`}
                          >
                            {t(c.labelKey)}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-zinc-500 mb-1">{t("regcfgDisabledItems")}</label>
                    <ItemMultiSelect
                      selected={regear.disabled_items}
                      onChange={ids => pushRegear({ disabled_items: ids })}
                    />
                    <p className="text-[11px] text-zinc-600 mt-1">{t("regcfgDisabledItemsHint")}</p>
                  </div>

                  <div>
                    <label className="block text-xs text-zinc-500 mb-1">{t("regcfgApprovers")}</label>
                    <p className="text-[11px] text-zinc-600 mb-2">{t("regcfgApproversHint")}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {(roles ?? []).map(r => {
                        const active = regear.approver_role_ids.includes(Number(r.id));
                        return (
                          <button
                            key={r.id} type="button"
                            onClick={() => toggleRegearApprover(r.id)}
                            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                              active
                                ? "border-amber-500 bg-amber-500/15 text-amber-300"
                                : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                            }`}
                          >
                            {r.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </FeatureRow>

            {/* â”€â”€ Lootlog â”€â”€ */}
            <FeatureRow
              icon="ti-clipboard-list" iconColor="text-amber-400"
              title={t("ll")} desc={t("featLootlogDesc")}
              on={lootlogEnabled} onToggle={v => void toggleLootlogFeature(v)}
              disabled={!hasGuild}
              statusHint={`${llPct}%`}
              open={featOpen.has("lootlog")} onOpen={() => toggleFeat("lootlog")}
            >
              <div className="mb-4">
                <label className="block text-xs text-zinc-500 mb-2">{t("lootlogThreadChannelLabel")}</label>
                {channelSelect(lootlogThreadChannelId, saveLootlogThreadChannel, "mb-1")}
                <p className="text-[11px] text-zinc-600">{t("lootlogThreadChannelDesc")}</p>
              </div>
              <p className="text-xs text-zinc-500 mb-4">{t("llLoggerPercentHint")}</p>
              <div className="flex items-center gap-3 flex-wrap">
                <label className="text-xs text-zinc-500">{t("llLoggerPercent")}</label>
                <input
                  type="range" min={0} max={100} value={llPct}
                  onChange={e => setLlPct(Number(e.target.value))}
                  onPointerUp={() => void saveLootLog()}
                  className="flex-1 accent-amber-500"
                />
                <input
                  type="number" min={0} max={100} value={llPct}
                  onChange={e => setLlPct(Math.max(0, Math.min(100, Number(e.target.value))))}
                  onBlur={() => void saveLootLog()}
                  className="w-16 bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
                />
                <span className="text-xs text-zinc-500">%</span>
              </div>
            </FeatureRow>

            {/* â”€â”€ Logs do bot â€” canal criado e mantido pelo prÃ³prio bot quando
                ligado (ver cogs/audit_log.py), sem nada pra configurar aqui. â”€â”€ */}
            <FeatureRow
              icon="ti-file-text" iconColor="text-sky-400"
              title={t("botLogsTitle")} desc={t("featBotLogsDesc")}
              on={botLogsEnabled} onToggle={toggleBotLogsFeature}
              statusHint={logsChannelId ? chName(logsChannelId) : t("botLogsPending")}
              open={featOpen.has("botlogs")} onOpen={() => toggleFeat("botlogs")}
            >
              <p className="text-xs text-zinc-500 mb-2">{t("botLogsDesc")}</p>
              <label className="block text-xs text-zinc-400 mb-1">{t("botLogsChannelLabel")}</label>
              <p className="text-[11px] text-zinc-600 mb-2">{t("botLogsChannelDesc")}</p>
              {channelSelect(logsChannelId, saveLogsChannel)}
              {channelsErr && <p className="text-xs text-red-400 mt-2">{t("eventsChannelLoadError")}</p>}
            </FeatureRow>

            {/* â”€â”€ Nodes â”€â”€ */}
            <FeatureRow
              icon="ti-map-pin" iconColor="text-emerald-400"
              title={t("nodes")} desc={t("featNodesDesc")}
              on={nodesEnabled}
              onToggle={toggleNodesFeature}
              disabled={!hasGuild}
              statusHint={nodesEnabled && nodesCalendarChannelId ? chName(nodesCalendarChannelId) : t("featNeedsSetup")}
              fullWidth
              open={featOpen.has("nodes")} onOpen={() => toggleFeat("nodes")}
            >
              <label className="block text-xs text-zinc-400 mb-1">{t("nodesCalendarTitle")}</label>
              <p className="text-[11px] text-zinc-600 mb-2">{t("nodesCalendarDesc")}</p>
              {channelSelect(nodesCalendarChannelId, saveNodesCalendar)}

              {/* Scout bonus source â€” de onde vem o bÃ´nus do scout. "node" =
                  pool separado do valor vendido; "tab" = deduzido da tab do
                  evento. Ver events.get_scout_bonus_source no backend. */}
              <div className="mt-4">
                <div className="flex items-center gap-2 mb-1">
                  <i className="ti ti-compass text-emerald-400 text-sm" aria-hidden="true" />
                  <label className="text-xs text-zinc-400">{t("scoutBonusSourceTitle")}</label>
                </div>
                <p className="text-[11px] text-zinc-600 mb-2">{t("scoutBonusSourceDesc")}</p>
                <div className="grid grid-cols-2 gap-2">
                  {SCOUT_BONUS_SOURCES.map(({ src, labelKey, descKey }) => (
                    <button
                      key={src} type="button"
                      onClick={() => { void saveScoutBonusSource(src); }}
                      className={`text-left text-xs px-3 py-2 rounded-md border transition-colors ${
                        scoutBonusSource === src
                          ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                          : "border-zinc-700 text-zinc-500 hover:border-zinc-600"
                      }`}
                    >
                      <span className="block font-semibold">{t(labelKey)}</span>
                      <span className="block text-[10px] opacity-80 mt-0.5">{t(descKey)}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Scout percent global â€” multiplica o weight de cada node. */}
              <div className="mt-4">
                <div className="flex items-center gap-2 mb-1">
                  <i className="ti ti-percentage text-emerald-400 text-sm" aria-hidden="true" />
                  <label className="text-xs text-zinc-400">{t("scoutPercentTitle")}</label>
                </div>
                <p className="text-[11px] text-zinc-600 mb-2">{t("scoutPercentDesc")}</p>
                <div className="flex items-center gap-1">
                  <input type="number" min={0} max={100} value={scoutPercent}
                    onChange={e => setScoutPercent(e.target.value)}
                    onBlur={() => void saveScoutPercent()}
                    className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" style={{ width: 80 }} />
                  <span className="text-[11px] text-zinc-500">%</span>
                </div>
              </div>

              {/* Tipos de node â€” adicionar node em si Ã© pelo Discord. */}
              <div className="mt-4 border-t border-zinc-800 pt-3">
                <h4 className="text-xs font-semibold text-zinc-200 mb-1">{t("nodesDefsTitle")}</h4>
                <p className="text-[11px] text-zinc-600 mb-2">{t("nodesDefsHint")}</p>
                {nodesErr && <p className="text-[11px] mb-2" style={{ color: "var(--gold)" }}>{nodesErr}</p>}
                {nodeDefs.length === 0 ? (
                  <p className="text-[11px] text-zinc-500 mb-2">{t("nodesNoDefs")}</p>
                ) : (
                  <table className="w-full mb-3" style={{ borderCollapse: "collapse" }}>
                    <thead>
                      <tr className="text-[11px] text-zinc-500" style={{ textAlign: "left" }}>
                        <th className="py-1 px-2">{t("nodesDefName")}</th>
                        <th className="py-1 px-2">{t("nodesDefEmoji")}</th>
                        <th className="py-1 px-2">{t("nodesDefWeight")}</th>
                        <th className="py-1 px-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {[...nodeDefs]
                        .sort((a, b) =>
                          nodeDefDisplayName(a.name, botLanguage)
                            .localeCompare(nodeDefDisplayName(b.name, botLanguage), undefined, { sensitivity: "base" }))
                        .map(d => ndEditingId === d.id ? (
                        <tr key={d.id} style={{ borderTop: "1px solid var(--border)" }}>
                          <td className="py-1.5 px-2">
                            <input value={ndEditName} onChange={e => setNdEditName(e.target.value)}
                              className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1 text-zinc-200 w-full" />
                          </td>
                          <td className="py-1.5 px-2">
                            <input value={ndEditEmoji} onChange={e => setNdEditEmoji(e.target.value)}
                              className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1 text-zinc-200" style={{ width: 60 }} />
                          </td>
                          <td className="py-1.5 px-2">
                            <div className="flex items-center gap-1">
                              <input type="number" min={0} value={ndEditWeight}
                                onChange={e => setNdEditWeight(e.target.value)}
                                className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1 text-zinc-200" style={{ width: 70 }} />
                              <span className="text-[11px] text-zinc-500">%</span>
                            </div>
                          </td>
                          <td className="py-1.5 px-2 text-right whitespace-nowrap">
                            <button className="btn" style={{ padding: "2px 8px" }} onClick={() => saveNodeDef(d.id)}>{t("save")}</button>{" "}
                            <button className="btn" style={{ padding: "2px 8px" }} onClick={() => setNdEditingId(null)}>{t("cancel")}</button>
                          </td>
                        </tr>
                      ) : (
                        <tr key={d.id} style={{ borderTop: "1px solid var(--border)" }}>
                          <td className="py-1.5 px-2 text-xs text-zinc-200">{nodeDefDisplayName(d.name, botLanguage)}</td>
                          <td className="py-1.5 px-2 text-xs text-zinc-200">{d.emoji ?? "â€”"}</td>
                          <td className="py-1.5 px-2 text-xs text-zinc-200">
                            {Math.round(d.weight * 100)}%
                            {(() => {
                              const sp = Math.max(0, Math.min(100, parseInt(scoutPercent, 10) || 0));
                              if (sp > 0) {
                                const compound = (d.weight * sp).toFixed(d.weight * sp < 1 ? 2 : 1);
                                return (
                                  <div className="text-[10px] text-emerald-400/70" style={{ lineHeight: 1.3 }}>
                                    = {compound}% do split
                                  </div>
                                );
                              }
                              return (
                                <div className="text-[10px] text-zinc-600" style={{ lineHeight: 1.3 }}>
                                  {t(scoutBonusSource === "tab" ? "nodesDefWeightHintTab" : "nodesDefWeightHint")}
                                </div>
                              );
                            })()}
                          </td>
                          <td className="py-1.5 px-2 text-right whitespace-nowrap">
                            <button className="btn" style={{ padding: "2px 8px" }} onClick={() => startEditNodeDef(d)}>{t("nodesDefEdit")}</button>{" "}
                            <button className="btn" style={{ padding: "2px 8px" }} onClick={() => removeNodeDef(d.name)}>
                              {t("nodesDelete")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <div className="flex items-end gap-2 flex-wrap">
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-zinc-500">{t("nodesDefName")}</span>
                    <input value={ndName} onChange={e => setNdName(e.target.value)} placeholder="ex: Drake"
                      className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-zinc-500">{t("nodesDefEmoji")}</span>
                    <input value={ndEmoji} onChange={e => setNdEmoji(e.target.value)} placeholder="ðŸ‰"
                      className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" style={{ width: 60 }} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-zinc-500">{t("nodesDefWeight")}</span>
                    <div className="flex items-center gap-1">
                      <input type="number" min={0} value={ndWeight} onChange={e => setNdWeight(e.target.value)}
                        className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" style={{ width: 70 }} />
                      <span className="text-[11px] text-zinc-500">%</span>
                    </div>
                  </label>
                  <button className="btn" onClick={addNodeDef} disabled={!ndName.trim()}>{t("nodesDefAdd")}</button>
                </div>
              </div>

              {/* Mapas extras / exclusÃµes â€” collapsable (collapsed by default) */}
              {nodeMaps && (
                <div className="mt-4 border-t border-zinc-800 pt-3">
                  <button onClick={() => setMapsOpen(o => !o)} className="flex w-full items-center justify-between text-xs font-semibold text-zinc-200 mb-1">
                    <span>{t("nodesMapsTitle")}</span>
                    <span className="text-zinc-400">{mapsOpen ? "â–¾" : "â–¸"}</span>
                  </button>
                  {mapsOpen && (
                    <div className="space-y-2">
                  <div className="flex items-end gap-2 flex-wrap mb-3">
                    <label className="flex flex-col gap-1">
                      <span className="text-[11px] text-zinc-500">{t("nodesMapAdd")}</span>
                      <input value={nmName} onChange={e => setNmName(e.target.value)} placeholder={t("nodesMapNamePh")}
                        className="bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" />
                    </label>
                    <button className="btn" onClick={addNodeMapExtra} disabled={!nmName.trim()}>{t("nodesMapAdd")}</button>
                  </div>
                  {nodeMaps.extras.length > 0 && (
                    <div className="mb-2">
                      <span className="text-[11px] text-zinc-500">{t("nodesExtras")}:</span>{" "}
                      {nodeMaps.extras.map(m => (
                        <span key={m} className="badge" style={{ margin: "2px", cursor: "pointer" }}
                          onClick={() => removeNodeMapExtra(m)} title={t("nodesDelete")}>
                          {m} âœ•
                        </span>
                      ))}
                    </div>
                  )}
                  <div>
                    <span className="text-[11px] text-zinc-500">{t("nodesExclusions")}:</span>{" "}
                    {nodeMaps.builtin.length === 0 ? (
                      <span className="text-[11px] text-zinc-500">{t("nodesNoMaps")}</span>
                    ) : nodeMaps.builtin.map(m => {
                      const isExcl = nodeMaps.exclusions.includes(m);
                      return (
                        <span key={m} className="badge"
                          style={{ margin: "2px", cursor: "pointer", opacity: isExcl ? 0.4 : 1, textDecoration: isExcl ? "line-through" : "none" }}
                          onClick={() => toggleNodeExclusion(m)} title={isExcl ? "Reverter" : "Ocultar"}>
                          {m}
                        </span>
                      );
                    })}
                  </div>
                    </div>
                  )}
                </div>
              )}
            </FeatureRow>

            {/* â”€â”€ Battle Feed â€” mensageiro de batalhas â”€â”€ */}
            <FeatureRow
              icon="ti-swords" iconColor="text-rose-400"
              title={t("battleFeedTitle")} desc={t("featBattleFeedDesc")}
              on={battleFeedEnabled}
              onToggle={toggleBattleFeedFeature}
              disabled={!hasGuild}
              statusHint={battleFeedEnabled && battleFeedChannelId ? chName(battleFeedChannelId) : t("featNeedsSetup")}
              open={featOpen.has("battlefeed")} onOpen={() => toggleFeat("battlefeed")}
            >
              <label className="block text-xs text-zinc-400 mb-1">{t("battleFeedChannelLabel")}</label>
              <p className="text-[11px] text-zinc-600 mb-2">{t("battleFeedChannelDesc")}</p>
              {channelSelect(battleFeedChannelId, saveBattleFeedChannel, "mb-3")}

              <label className="block text-xs text-zinc-400 mb-1">{t("battleFeedMinPlayersLabel")}</label>
              <p className="text-[11px] text-zinc-600 mb-2">{t("battleFeedMinPlayersDesc")}</p>
              <input
                type="number" min={1} max={500} value={battleFeedMinPlayers}
                onChange={e => saveBattleFeedMinPlayers(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200"
              />
            </FeatureRow>

            {/* â”€â”€ Juicy Kills â€” loot valioso em kills lethais â”€â”€ */}
            <FeatureRow
              icon="ti-coin" iconColor="text-amber-400"
              title={t("juicyKillsTitle")} desc={t("featJuicyKillsDesc")}
              on={juicyKillEnabled}
              onToggle={toggleJuicyKillFeature}
              disabled={!hasGuild}
              statusHint={juicyKillEnabled && juicyKillChannelId ? chName(juicyKillChannelId) : t("featNeedsSetup")}
              open={featOpen.has("juicykills")} onOpen={() => toggleFeat("juicykills")}
            >
              <label className="block text-xs text-zinc-400 mb-1">{t("juicyKillsChannelLabel")}</label>
              <p className="text-[11px] text-zinc-600 mb-2">{t("juicyKillsChannelDesc")}</p>
              {channelSelect(juicyKillChannelId, saveJuicyKillChannel, "mb-3")}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                <label className="block text-xs text-zinc-400">
                  {t("juicyKillsMinSilverLabel")}
                  <span className="block text-[11px] text-zinc-600 my-1">{t("juicyKillsMinSilverDesc")}</span>
<input type="number" min={20000000} value={juicyKillMinSilver}
                    onChange={e => setJuicyKillMinSilver(e.target.value)} onBlur={saveJuicyKillMinSilver}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" />
                </label>
                <label className="block text-xs text-zinc-400">
                  {t("juicyKillsMinFameLabel")}
                  <span className="block text-[11px] text-zinc-600 my-1">{t("juicyKillsMinFameDesc")}</span>
                  <input type="number" min={0} value={juicyKillMinFame}
                    onChange={e => setJuicyKillMinFame(e.target.value)} onBlur={saveJuicyKillMinFame}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200" />
                </label>
              </div>

<label className="block text-xs text-zinc-400 mb-2">{t("juicyKillsRegionsLabel")}</label>
              <div className="flex flex-wrap gap-2">
                {ALBION_REGIONS.map(region => {
                  const selected = juicyKillRegions.includes(region);
                  return <button type="button" key={region}
                    onClick={() => saveJuicyKillRegions(selected ? juicyKillRegions.filter(r => r !== region) : [...juicyKillRegions, region])}
                    className={`badge border ${selected ? "border-amber-500 text-amber-300 bg-amber-500/15" : "border-zinc-700 bg-zinc-800 text-zinc-500"}`}>
                    {REGION_LABELS[lang][region]}
                  </button>;
                })}
              </div>
            </FeatureRow>

            {/* Comandos avulsos â€” half width */}
            <FeatureRow
              icon="ti-apps" iconColor="text-zinc-400"
              title={t("cmdCategoryMiscellaneous")} desc=""
              open={true} onOpen={() => {}}
            >
              {commands === null ? (
                <p className="text-xs text-zinc-500">{t("loading")}</p>
              ) : (
                <div className="flex flex-col">
                  {miscCmds.map(cmdRow)}
                </div>
              )}
            </FeatureRow>

        {/* PermissÃµes do site, por cargo â€” full width */}
        <Panel className="p-5 lg:col-span-2">
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
            <>
              {/* Dropdown: pick a role to add to the quadrant */}
              {permDropdownRoles.length > 0 && (
                <select
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-200 mb-4"
                  value=""
                  onChange={e => { if (e.target.value) addRoleToPermQuadrant(e.target.value); }}
                >
                  <option value="" disabled>{t("rolePermsAddRole")}</option>
                  {permDropdownRoles.map(r => (
                    <option key={r.id} value={r.id}>{r.name}</option>
                  ))}
                </select>
              )}
              {/* Quadrant: only roles with perms or explicitly pinned */}
              {[...permQuadrantIds].map(roleId => {
                const role = roles?.find(r => r.id === roleId);
                if (!role) return null;
                return (
                  <div key={role.id} className="py-3 border-b border-zinc-800/60 last:border-0">
                    <div className="flex items-center gap-2 mb-2">
                      {role.color !== 0 && (
                        <span
                          className="inline-block w-2 h-2 rounded-full shrink-0"
                          style={{ background: `#${role.color.toString(16).padStart(6, "0")}` }}
                        />
                      )}
                      <span className="text-sm text-zinc-200 font-medium flex-1">{role.name}</span>
                      <button
                        type="button"
                        className="text-[11px] text-zinc-500 hover:text-red-400 transition-colors px-1"
                        onClick={() => {
                          // Clear all perms for this role and remove from quadrant
                          for (const col of PERM_COLS) {
                            if (role.permissions?.[col.key]) togglePerm(role.id, col.key, false);
                          }
                          removeRoleFromPermQuadrant(role.id);
                        }}
                        title={t("rolePermsRemove")}
                      >
                        <i className="ti ti-x" aria-hidden="true" />
                      </button>
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
                );
              })}
              {permQuadrantIds.size === 0 && (
                <p className="text-xs text-zinc-500">{t("rolePermsEmpty")}</p>
              )}
            </>
          )}
        </Panel>

        {/* Trocar servidor â€” half width */}
        <Panel className="p-5">
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
        </Panel>

      </div>
    </div>
  );
}

// â”€â”€ Switch â€” o mesmo toggle visual da lista de comandos, deduplicado â”€â”€â”€â”€â”€â”€â”€â”€
function Switch({ checked, onChange, disabled }: { checked: boolean; onChange?: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={`relative inline-flex h-5 w-9 shrink-0 ${disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"}`} onClick={e => e.stopPropagation()}>
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange?.(e.target.checked)}
        disabled={disabled}
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
  );
}

// â”€â”€ FeatureRow â€” painel sempre-aberto com toggle mestre no header. Sem
// colapso: toda config fica visÃ­vel o tempo todo (pÃ¡gina densa, nÃ£o comercial).
function FeatureRow({ icon, iconColor, title, desc, on, onToggle, statusHint, open, onOpen, disabled, fullWidth, children }: {
  icon: string;
  iconColor: string;
  title: string;
  desc: string;
  on?: boolean;
  onToggle?: (v: boolean) => void;
  statusHint?: string;
  open: boolean;
  onOpen: () => void;
  disabled?: boolean;
  fullWidth?: boolean;
  children: ReactNode;
}) {
  return (
    <Panel className={`p-5 ${fullWidth ? "lg:col-span-2" : ""}`}>
      <div className="flex items-center gap-3">
        {onToggle
          ? <Switch checked={!!on} onChange={disabled ? undefined : onToggle} disabled={disabled} />
          : <span className="w-9 shrink-0" aria-hidden="true" />}
        <button
          type="button"
          onClick={onOpen}
          className="flex-1 flex items-center gap-3 text-left min-w-0"
        >
          <i className={`ti ${icon} ${iconColor} text-lg shrink-0`} aria-hidden="true" />
          <div className="flex-1 min-w-0">
            <span className="text-sm font-semibold text-zinc-100">{title}</span>
            <span className="text-xs text-zinc-500 ml-2">{desc}</span>
          </div>
          {statusHint && (
            <span className="text-[11px] shrink-0 ml-2 text-right max-w-[30%] truncate text-zinc-500">
              {statusHint}
            </span>
          )}
          <i className={`ti ti-chevron-down text-zinc-600 transition-transform shrink-0 ${open ? "rotate-180" : ""}`} />
        </button>
      </div>
      {open && (
        <div className={`mt-4 ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
          {children}
        </div>
      )}
    </Panel>
  );
}

// â”€â”€ Multi-select pesquisÃ¡vel de itens (base) p/ o override de itens desabilitados
// do regear. Lista todos os itens traduzidos do catÃ¡logo, aceita vÃ¡rios e guarda
// base IDs â€” o formato que o backend compara (item_base_id).
function ItemMultiSelect({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const t = useT();
  const { lang } = useLang();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);

  const bases = useMemo(() => itemBases(), []);
  const byBase = useMemo(() => new Map(bases.map(b => [b.baseId, b])), [bases]);
  const disp = (b: ItemBase) => lang === "en" ? (b.nameEn || b.name) : b.name;

  const matches = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const avail = bases.filter(b => !selected.includes(b.baseId));
    const filtered = ql === ""
      ? avail
      : avail.filter(b => b.baseId.toLowerCase().includes(ql) || disp(b).toLowerCase().includes(ql));
    // ponytail: cap em 150 p/ nÃ£o inflar o DOM â€” o catÃ¡logo tem milhares de bases.
    return filtered.slice(0, 150);
  }, [bases, q, selected, lang]);

  function add(baseId: string) {
    if (!selected.includes(baseId)) onChange([...selected, baseId]);
  }
  function remove(baseId: string) {
    onChange(selected.filter(x => x !== baseId));
  }

  return (
    <div className="relative">
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selected.map(id => {
            const b = byBase.get(id);
            return (
              <span key={id} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border border-red-500/40 bg-red-500/10 text-red-300">
                {b && <img src={itemRenderUrl(b.sampleId)} alt="" width={16} height={16} />}
                {b ? disp(b) : id}
                <button type="button" onClick={() => remove(id)} className="hover:text-red-200" aria-label="remove">
                  <i className="ti ti-x" />
                </button>
              </span>
            );
          })}
        </div>
      )}
      <button
        type="button" onClick={() => setOpen(o => !o)}
        className="w-full text-left bg-zinc-800 border border-zinc-700 rounded-md text-xs px-2 py-1.5 text-zinc-400 hover:border-zinc-600"
      >
        <i className="ti ti-plus mr-1" />{t("regcfgDisabledItemsAdd")}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute z-20 mt-1 w-full max-h-72 flex flex-col bg-zinc-900 border border-zinc-700 rounded-md shadow-lg">
            <input
              autoFocus value={q} onChange={e => setQ(e.target.value)}
              placeholder={t("regcfgDisabledItemsSearch")}
              className="w-full bg-zinc-800 border-b border-zinc-700 px-2 py-1.5 text-xs text-zinc-200 outline-none"
            />
            <div className="overflow-y-auto">
              {matches.length === 0 ? (
                <p className="px-2 py-2 text-xs text-zinc-600">{t("regcfgDisabledItemsEmpty")}</p>
              ) : matches.map(b => (
                <button
                  key={b.baseId} type="button"
                  onClick={() => { add(b.baseId); setQ(""); }}
                  className="flex items-center gap-2 w-full text-left px-2 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800"
                >
                  <img src={itemRenderUrl(b.sampleId)} alt="" width={20} height={20} />
                  <span className="flex-1 truncate">{disp(b)}</span>
                  <span className="font-mono text-[10px] text-zinc-600">{b.baseId}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}





