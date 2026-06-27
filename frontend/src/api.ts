// Cliente da API do backend. Em dev, o Vite faz proxy de /auth, /guilds e /meta
// para http://localhost:8000, então usamos caminhos relativos. `credentials:
// include` manda o cookie de sessão.

let _guild = "";
export const setGuild = (id: string) => { _guild = id; };
const g = () => _guild;

export interface ApiRole {
  id: number;
  name: string;
  weapon_id: number | null;
  invisible_function: string | null;
  offhand: string | null;
  helmet: string | null;
  armor: string | null;
  boots: string | null;
  cape: string | null;
  food: string | null;
  abilities: string | null;
  play_style: string | null;
  obs: string | null;
  color?: string | null;
  q_spell?: string | null;
  w_spell?: string | null;
  passive_spell?: string | null;
  gear_spells?: Record<string, string | null> | null;
  build_items?: RegearItem[];
}
export interface ApiSlot {
  id: number;
  position: number;
  label: string | null;
  notes: string | null;
  fn: string | null;
  roles: ApiRole[];
}
export interface ApiParty {
  id: number;
  position: number;
  name: string | null;
  slots: ApiSlot[];
}
export interface ApiComp {
  id: number;
  name: string;
  description: string | null;
  archived: boolean;
  parties: ApiParty[];
}
export interface CatalogRole {
  id: number;
  name: string;
  invisible_function: string | null;
  color?: string | null;
}

export interface WeaponSpell {
  id: number;
  spell_id: string;
  slot: string;  // Q | W | passive
  order_idx: number;
  name: string;
  description: string | null;
  uisprite: string | null;
}

export interface WeaponOut {
  id: number;
  item_id: string;
  name: string;
  invisible_function: string | null;
  category: string | null;
}

export interface RegearItem {
  slot: string;       // offhand | helmet | armor | boots | cape | food
  item_id: string;    // ID canônico do Albion (ex: "T8_HEAD_CLOTH_MORGANA@4")
  name: string;
  quality: number;    // 1=Normal
  quantity: number;
}

export interface GameRoleDetail {
  id: number;
  name: string;
  weapon_id: number | null;
  weapon_name: string | null;
  invisible_function: string | null;
  offhand: string | null;
  helmet: string | null;
  armor: string | null;
  boots: string | null;
  cape: string | null;
  food: string | null;
  abilities: string | null;
  play_style: string | null;
  obs: string | null;
  build_items: RegearItem[];
  color: string | null;
  q_spell: string | null;
  w_spell: string | null;
  passive_spell: string | null;
  gear_spells: Record<string, string | null> | null;
}

export interface RegearItemEstimate extends RegearItem {
  unit_price: number;
  total_price: number;
}

export interface RegearEstimate {
  participant_id: number;
  user_name: string | null;
  game_role_id: number | null;
  game_role_name: string | null;
  items: RegearItemEstimate[];
  total: number;
  price_basis: string;
  calculated_at: string;
}

export interface BuildFieldSuggestion {
  value: string;
  votes: number;
  total: number;
}

export interface BuildSuggestion {
  target_function: string;
  sample_size: number;
  fields: Record<string, BuildFieldSuggestion>;
}

export interface CompUpdatePayload {
  name?: string;
  parties?: { name: string | null; slots: { label: string | null; fn: string | null; role_ids: number[] }[] }[];
}

export interface Suggestion {
  target_function: string;
  sample_size: number;
  fields: Record<string, { value: string; votes: number; total: number }>;
}
export interface Me {
  id: string;
  username: string;
  global_name: string | null;
  avatar: string | null;
  guild_id: string | null;
}

export interface DiscordGuild {
  id: string;
  name: string;
  icon: string | null;
  is_admin: boolean;
  bot_present: boolean;
}

export interface Permissions {
  "events.view": boolean;
  "events.create": boolean;
  "events.manage": boolean;
  "comps.view": boolean;
  "comps.create": boolean;
  "comps.manage": boolean;
  "guild.admin": boolean;
}

export const NO_PERMS: Permissions = {
  "events.view": false, "events.create": false, "events.manage": false,
  "comps.view": false,  "comps.create": false,  "comps.manage": false,
  "guild.admin": false,
};

export interface DiscordRole {
  id: string;
  name: string;
  color: number;
  permissions: Partial<Permissions>;
}

export interface SiteGuild {
  id: string;
  name: string;
  icon: string | null;
  bot_present: boolean;
  albion_guild_name: string | null;
}
export interface EventSummary {
  id: number;
  state: string;
  type: string | null;
  title: string | null;
  caller_name: string | null;
  scheduled_at: string | null;
  created_at: string;
}
export interface VerificationStep {
  step: string;
  completed: boolean;
  data: Record<string, unknown>;
}

export interface Participant {
  id: number;
  user_id: number;
  user_name: string | null;
  percent: number;
  base_percent: number;
  is_trial: boolean;
  silver_received: number;
  game_role_id: number | null;
  game_role_name: string | null;
}

export interface Death {
  id: number;
  user_id: number | null;
  display_name: string;
  silver_value: number;
  notes: string | null;
  approved: boolean;
}

export interface PayoutRow {
  user_id: number | null;
  display_name: string;
  percent: number;
  lootsplit: number;
  regear: number;
  total: number;
}

export interface PayoutPreview {
  tab_value: number;
  payouts: PayoutRow[];
  total_lootsplit: number;
  total_regear: number;
}

export interface LootEntry {
  id: number;
  looted_by_name: string;
  looted_by_user_id: number | null;
  item_type: string;
  item_name: string;
  quantity: number;
  silver_value: number;
  in_chest: boolean;
}

export interface ChestEntry {
  id: number;
  item_type: string;
  item_name: string;
  quantity: number;
  silver_value: number;
  deposited_by_name: string | null;
  snapshot_at: string;
}

export interface MissingItem {
  item_type: string;
  item_name: string;
  looted_qty: number;
  chest_qty: number;
  missing_qty: number;
  silver_value: number;
  missing_value: number;
}

export interface LootReconcile {
  looted: LootEntry[];
  chest: ChestEntry[];
  missing: MissingItem[];
  total_looted_value: number;
  total_chest_value: number;
  missing_value: number;
  has_loot_log: boolean;
  has_chest_log: boolean;
}

export interface EventDetail {
  id: number;
  state: string;
  type: string | null;
  title: string | null;
  message: string | null;
  comp_id: number | null;
  scheduled_at: string | null;
  started_at: string | null;
  callout_at: string | null;
  ended_at: string | null;
  is_loss: boolean;
  tab_value: number;
  tab_image_url: string | null;
  allowed_transitions: string[];
  verification: VerificationStep[];
  participants: Participant[];
  deaths: Death[];
  payout: PayoutPreview | null;
}

export function imgRetry(onFail?: (img: HTMLImageElement) => void) {
  return (e: { currentTarget: HTMLImageElement }) => {
    const img = e.currentTarget;
    const n = +(img.dataset.retries ?? 0);
    if (n < 3) {
      img.dataset.retries = String(n + 1);
      setTimeout(() => { img.src = img.src; }, 800 * (n + 1));
    } else {
      onFail?.(img);
    }
  };
}

export async function fetchRetry(input: RequestInfo, retries = 3): Promise<Response> {
  let res: Response = await fetch(input);
  for (let i = 1; i < retries && !res.ok && res.status >= 500; i++) {
    await new Promise(r => setTimeout(r, 800 * i));
    res = await fetch(input);
  }
  return res;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      // sem corpo JSON
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : await res.json();
}

export const BOT_INVITE = `https://discord.com/oauth2/authorize?client_id=1518276093294153859&permissions=8&scope=bot+applications.commands`;

export const api = {
  me: () => req<Me | null>("/auth/me").catch(() => null),
  myDiscordGuilds: () => req<DiscordGuild[]>("/auth/guilds"),
  selectGuild: (guild_id: string, guild_name: string, icon: string | null, is_admin = false) =>
    req<{ guild_id: string; bot_present: boolean }>("/auth/select-guild", {
      method: "POST",
      body: JSON.stringify({ guild_id, guild_name, icon, is_admin }),
    }),
  switchGuild: (guild_id: string) =>
    req<{ guild_id: string; bot_present: boolean }>(`/auth/switch-guild/${guild_id}`, { method: "POST" }),
  mySiteGuilds: () => req<SiteGuild[]>("/auth/my-site-guilds"),
  guildInfo: (guild_id: string) => req<SiteGuild & { settings: Record<string, unknown> }>(`/auth/guild-info/${guild_id}`),
  updateGuildSettings: (guild_id: string, payload: { albion_guild_name?: string | null }) =>
    req<{ ok: boolean }>(`/auth/guild-settings/${guild_id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  myPermissions: () => req<Permissions>("/auth/my-permissions"),
  guildDiscordRoles: (guild_id: string) => req<DiscordRole[]>(`/auth/guild-discord-roles/${guild_id}`),
  updateRolePermissions: (guild_id: string, role_id: string, role_name: string, permissions: Partial<Permissions>) =>
    req<{ ok: boolean }>(`/auth/guild-discord-roles/${guild_id}/${role_id}`, {
      method: "PATCH",
      body: JSON.stringify({ role_name, permissions }),
    }),
  guildCommands: (guild_id: string) =>
    req<{ name: string; description: string; enabled: boolean }[]>(`/auth/guild-commands/${guild_id}`),
  toggleGuildCommand: (guild_id: string, name: string, enabled: boolean) =>
    req<{ ok: boolean }>(`/auth/guild-commands/${guild_id}/${name}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),

  getComp: (c: number) => req<ApiComp>(`/guilds/${g()}/comps/${c}`),
  listComps: () => req<{ id: number; name: string }[]>(`/guilds/${g()}/comps`),
  createComp: (payload: { name: string; description?: string }) =>
    req<ApiComp>(`/guilds/${g()}/comps`, { method: "POST", body: JSON.stringify(payload) }),
  updateComp: (id: number, payload: CompUpdatePayload) =>
    req<ApiComp>(`/guilds/${g()}/comps/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteComp: (id: number) =>
    req<void>(`/guilds/${g()}/comps/${id}`, { method: "DELETE" }),
  listRoles: () => req<CatalogRole[]>(`/guilds/${g()}/catalog/roles`),
  getPrices: (itemIds: string[], quality = 1) =>
    req<{ prices: Record<string, number> }>(`/guilds/${g()}/catalog/prices?items=${itemIds.join(",")}&quality=${quality}`),
  listWeapons: () => req<WeaponOut[]>(`/guilds/${g()}/catalog/weapons`),
  getRole: (id: number) => req<GameRoleDetail>(`/guilds/${g()}/catalog/roles/${id}`),
  createRole: (payload: Partial<GameRoleDetail> & { name: string }) =>
    req<GameRoleDetail>(`/guilds/${g()}/catalog/roles`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  updateRole: (id: number, payload: Partial<GameRoleDetail>) =>
    req<GameRoleDetail>(`/guilds/${g()}/catalog/roles/${id}`, {
      method: "PATCH", body: JSON.stringify(payload),
    }),
  deleteRole: (id: number) =>
    req<void>(`/guilds/${g()}/catalog/roles/${id}`, { method: "DELETE" }),
  weaponSpells: (baseId: string) =>
    req<WeaponSpell[]>(`/guilds/${g()}/catalog/weapons/${baseId}/spells`),
  suggestBuild: (target_function: string) =>
    req<BuildSuggestion>(`/guilds/${g()}/catalog/suggest`, {
      method: "POST", body: JSON.stringify({ target_function }),
    }),
  suggest: (c: number, target_function: string, scope = "comp") =>
    req<Suggestion>(`/guilds/${g()}/comps/${c}/suggest`, {
      method: "POST",
      body: JSON.stringify({ target_function, scope }),
    }),

  listEvents: () => req<EventSummary[]>(`/guilds/${g()}/events`),
  createEvent: (title: string) =>
    req<EventDetail>(`/guilds/${g()}/events`, {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getEvent: (id: number) => req<EventDetail>(`/guilds/${g()}/events/${id}`),
  transition: (id: number, to: string) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({ to }),
    }),
  setType: (id: number, type: string) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/type`, {
      method: "POST",
      body: JSON.stringify({ type }),
    }),
  setStep: (id: number, step: string, completed: boolean, data?: Record<string, unknown>) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/verification/${step}`, {
      method: "POST",
      body: JSON.stringify({ completed, data: data ?? null }),
    }),
  addParticipant: (id: number, payload: { user_id: number; user_name?: string; percent?: number }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/participants`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateParticipant: (id: number, participantId: number, payload: { game_role_id?: number | null; percent?: number; is_trial?: boolean }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/participants/${participantId}`, {
      method: "PATCH", body: JSON.stringify(payload),
    }),
  removeParticipant: (id: number, participantId: number) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/participants/${participantId}`, { method: "DELETE" }),
  getRegearEstimate: (id: number, participantId: number) =>
    req<RegearEstimate>(`/guilds/${g()}/events/${id}/participants/${participantId}/regear-estimate`),
  addDeath: (id: number, payload: { display_name: string; silver_value?: number; notes?: string }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/deaths`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateDeath: (id: number, deathId: number, payload: { approved?: boolean; silver_value?: number; notes?: string }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/deaths/${deathId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  removeDeath: (id: number, deathId: number) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/deaths/${deathId}`, { method: "DELETE" }),

  getLoot: (id: number) =>
    req<LootReconcile>(`/guilds/${g()}/events/${id}/loots`),
};
