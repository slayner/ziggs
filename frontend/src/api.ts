// Cliente da API do backend. Em dev, o Vite faz proxy de /auth, /guilds e /meta
// para http://localhost:8000, então usamos caminhos relativos. `credentials:
// include` manda o cookie de sessão.

let _guild = "";
export const setGuild = (id: string) => { _guild = id; };
export const g = () => _guild;

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
export interface ApiFnType {
  key: string;
  label: string;
  color: string;
  emoji?: string;
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

// ── Regear por screenshot (fila standalone) ─────────────────────────────────
export interface RegearChannelCfg {
  channel_id: string;
  coverage_pct: number;
}
export interface RegearSettings {
  enabled: boolean;
  channels: RegearChannelCfg[];
  enabled_categories: string[];
  disabled_items: string[];
  require_approval: boolean;
  approver_role_ids: number[];
}
export interface RegearQueueItem {
  item_id: string;
  name: string;
  quality: number;
  slot: string;
  category: string | null;
  eligible: boolean;
  unit_price: number;
  total_price: number;
}
export interface RegearRequest {
  id: number;
  guild_id: number;
  event_id: number | null;
  event_title: string | null;
  requester_user_id: number | null;
  requester_name: string | null;
  screenshot_url: string;
  ocr_name: string | null;
  albion_event_id: string | null;
  death_timestamp: string | null;
  detected_items: RegearQueueItem[];
  base_total: number;
  suggested_total: number;
  final_total: number | null;
  coverage_pct: number;
  price_basis: string;
  status: string;            // pending | paid | denied | removed
  handled_by_user_id: number | null;
  handled_at: string | null;
  notes: string | null;
  created_at: string;
  recognition_status: string; // recognized | manual | error
}
export interface RegearList { requests: RegearRequest[] }
export interface RegearUpdatePayload {
  final_total?: number | null;
  status?: string;
  notes?: string | null;
  detected_items?: RegearQueueItem[];
}

// ── Lootlog anônimo (área só-admin) ─────────────────────────────────────────
export interface LootLogSubmission {
  id: number;
  event_id: number;
  submitter_user_id: number | null;
  submitter_name: string | null;
  file_name: string;
  raw_text: string | null;
  created_at: string;
}
export interface LootLogList { submissions: LootLogSubmission[] }
export interface LootLogSettings { logger_percent: number; enabled: boolean }
export interface LootLogIngestResult { id: number; row_count: number; silver_total: number; is_update: boolean }

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
  parties?: { name: string | null; slots: { id?: number; label: string | null; fn: string | null; role_ids: number[] }[] }[];
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

// ── Perfil customizado (tema/avatar/banner do personagem verificado) ────────
// Retângulo de crop como FRAÇÕES 0..1 da imagem original — contrato com o
// backend (form fields crop_x/y/w/h), ver docs/PLANO-PERFIL-V2.md. O crop é
// aplicado no servidor (Pillow); o cliente só escolhe o retângulo.
export interface CropRect { x: number; y: number; w: number; h: number }
export const PROFILE_THEMES = ["gold", "blue", "green", "red", "purple", "teal"] as const;
export type ProfileTheme = typeof PROFILE_THEMES[number];
export interface MyProfile {
  verified: boolean;
  theme: ProfileTheme;
  avatar_url: string | null;
  banner_url: string | null;
  pending_kinds: ("avatar" | "banner")[];
  blocked_until: string | null;
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
  "nodes.view": boolean;
  "nodes.manage": boolean;
  "guild.admin": boolean;
  "escalacao.manage": boolean;
  // Opcional: backends antigos (pré-energy-admin) não devolvem a chave —
  // a aba Energia só aparece quando presente E true.
  "energy.manage"?: boolean;
}

export const NO_PERMS: Permissions = {
  "events.view": false, "events.create": false, "events.manage": false,
  "comps.view": false,  "comps.create": false,  "comps.manage": false,
  "nodes.view": false,  "nodes.manage": false,
  "guild.admin": false, "escalacao.manage": false,
};

export interface DiscordRole {
  id: string;
  name: string;
  color: number;
  permissions: Partial<Permissions>;
}

export interface AuditLogEntry {
  id: number;
  actor_id: string | null;
  actor_name: string | null;
  actor_type: string;
  source: string;
  action: string;
  entity: string;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  note: string | null;
  created_at: string;
}

// ── Portal do membro (/guilds/{guild_id}/member/*) ──────────────────────────
// Rotas member-facing: exigem apenas membresia ativa (403 caso contrário),
// não perms admin. Ver backend app/api/routes/member.py.
export interface MemberWalletTx {
  id: number;
  kind: string;      // event_payout|event_deficit|pay|add|remove|forfeit|bank_adjust
  direction: "in" | "out" | "neutral";  // derivada server-side, nunca do client
  amount: number;
  counterparty_name: string | null;
  counterparty_albion_name: string | null;
  actor_name: string | null;
  event_id: number | null;
  event_title: string | null;
  undone: boolean;
  created_at: string;
}
export interface MemberWallet {
  balance: number;
  total_earned: number;
  transactions: MemberWalletTx[];
  total: number;
}
export interface MemberEnergyEntry {
  id: number;
  kind: string;  // log | adjustment | baseline
  ts: string;
  player: string;
  reason: string | null;
  amount: number;
  created_at: string;
}
export interface MemberEnergy {
  balance: number;
  entries: MemberEnergyEntry[];
  total: number;
}
export interface WeaponFnPref {
  weapon_id: number;
  fn: string;
  weapon_name: string;
}
export interface WeaponFnValidPair {
  weapon_id: number;
  fn: string;
  weapon_name: string;
}

// ── Portal do membro: eventos + inscrições + comps read-only ────────────────
export interface MemberEventSummary {
  id: number;
  state: string; // scheduled | in_progress | review | finalized
  type: string | null;
  title: string | null;
  caller_name: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  comp_id: number | null;
  // True em scheduled/in_progress — a UI mostra signup. False em review/finalized.
  can_signup: boolean;
}
export interface MemberPayoutRow {
  user_id: number;
  display_name: string;
  silver_received: number;
}
export interface MemberSettlement {
  tab_value: number;
  total_paid: number;
  participants: MemberPayoutRow[];
}
export interface MemberEventDetail {
  id: number;
  state: string;
  type: string | null;
  title: string | null;
  message: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  comp: { id: number; name: string; description: string | null } | null;
  settlement: MemberSettlement | null;
}
export interface MemberSignupOption {
  key: string;
  weapon_id: number;
  weapon_name: string;
  fn: string;
  role_names: string[];
}
// Inscrição atual (signup): weapon_fns = [{weapon_id, fn}] na ordem de
// preferência — casa com as options por (weapon_id, fn), não por string key.
export interface MemberSignup {
  id: number;
  functions: string[];
  weapon_fns: { weapon_id: number; fn: string | null }[];
  created_at: string;
}
export interface MemberSignupOptions {
  eligible: MemberSignupOption[];
  block_reason: string | null;
  preselected: string[];
  min_builds: number | null;
  current: MemberSignup | null;
}
export interface MemberCompSummary {
  id: number;
  name: string;
  description: string | null;
  archived: boolean;
  party_count: number;
}
export interface MemberCompDetail {
  id: number;
  name: string;
  description: string | null;
  archived: boolean;
  parties: ApiParty[];
}

// ── Admin de energia (/energy-admin/*; perm energy.manage) ──────────────────
export interface EnergyAdminMember {
  user_id: number;
  display_name: string;
  balance: number;
  whitelisted: boolean;
  low_energy: boolean;
}
export interface EnergyAdminOverview {
  threshold: number;
  members: EnergyAdminMember[];
}
export interface EnergyImportResult {
  applied: number;
  duplicates: number;
  whitelisted_applied: number;
  unregistered: Record<string, number>;
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
  title: string | null;
  caller_name: string | null;
  scheduled_at: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  comp_id: number | null;
  participation_mode: string;
  signup_mode: string;
  assignment_mode: string;
  autofill_mode: string;
  published_at: string | null;
}
export interface VerificationStep {
  step: string;
  completed: boolean;
  data: Record<string, unknown>;
}

export interface Participant {
  id: number;
  user_id: string;
  user_name: string | null;
  percent: number;
  base_percent: number;
  is_trial: boolean;
  silver_received: number;
  snapshots_present?: number;
  game_role_id: number | null;
  game_role_name: string | null;
  // Válido (entra no split ao finalizar) vs irregular (presença sem inscrição,
  // ou inscrição sem presença) — usado no cálculo de payout. Editar percent
  // já implica válido (ver ParticipantsSection).
  is_valid: boolean;
  // Origem da presença além da escalação. null = escalado (sem marcador).
  // Demais: "battle_no_call" | "call_no_signup" | "call_signup" | "manual".
  origin?: string | null;
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
  scout: number;
  total: number;
}

export interface PayoutPreview {
  tab_value: number;
  // Setting da guilda usado neste cálculo —
  // "none" | "leftover" | "full" | "guild_backed".
  // Regear em si não tem mais tipo (sempre calculado); isto só decide se/como
  // a tab virou lootsplit, pra UI escolher o que mostrar.
  lootsplit_mode: string;
  payouts: PayoutRow[];
  total_lootsplit: number;
  total_regear: number;
  // Scout: pool SEPARADO financiado pelo valor vendido de cada node capturado
  // (NodeDef.weight × sold_value). Não reduz o que participantes recebem.
  total_scout: number;
  scout_payouts: PayoutRow[];
  rounding_loss?: number;
  guild_tax?: number;
  // Fatia dos loggers (lootlog anônimo) — só p/ CTA com submissões.
  logger_pool?: number;
  logger_payouts?: PayoutRow[];
  // Só preenchido em modo "guild_backed" quando o regear come mais que a tab:
  // rombo a descontar igualmente do saldo (EconomyBalance) de cada membro.
  guild_deficit_total?: number;
  guild_deficit_member_count?: number;
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

// Reconciliação própria: lootlog + baú + mortes.
export interface ChestUploadEntry {
  item_type: string;
  item_name: string;
  quantity: number;
  silver_value?: number;
  deposited_by_name?: string | null;
}
export interface NotDepositedLooter { looted_by: string; qty: number }
export interface NotDepositedItem {
  item_id: string;
  item_name: string;
  missing_qty: number;
  looted_qty: number;
  chest_qty: number;
  silver_value: number;
  missing_value: number;
  looters: NotDepositedLooter[];
}
export interface RecoveredItem {
  item_id: string | null;
  item_name: string | null;
  quantity: number;
  looted_by: string | null;
}
export interface DeathLoss {
  user_id: number | null;
  display_name: string;
  silver_value: number;
  notes: string | null;
  recovered_items: RecoveredItem[];
}
export interface LootReconcileEvent {
  item_id: string;
  item_name: string;
  quantity: number;
  looted_by: string;
  looted_from: string | null;
}
// Timeline por looter: cada item vira "missing" (sobreviveu, não depositou →
// vermelho/amarelo), "deposited" (baú → verde) ou "died" (morreu com → cinza).
export type ReconcileItemStatus = "missing" | "deposited" | "died";
export interface ReconcileLooterItem {
  item_id: string;
  item_name: string;
  status: ReconcileItemStatus;
  quantity: number;
  silver_value: number;
  value: number;
  verified: boolean;
}
export interface ReconcileLooter {
  looted_by: string;
  missing_qty: number;
  missing_value: number;
  items: ReconcileLooterItem[];
}
export interface UnifiedReconcile {
  has_loot_log: boolean;
  has_chest_log: boolean;
  has_deaths: boolean;
  deposited: ChestEntry[];
  not_deposited: NotDepositedItem[];
  looters: ReconcileLooter[];
  died_with: DeathLoss[];
  loot_events: LootReconcileEvent[];
  total_looted_value: number;
  total_chest_value: number;
  missing_value: number;
  total_regear_value: number;
}

export interface EventSignup {
  id: number;
  user_id: string;
  user_name: string | null;
  functions: string[];
  created_at: string;
}

// Registrado (/register) visto numa batalha real da guilda na janela do
// evento, mas sem nenhum EventParticipant — nem call, nem inscrição.
export interface BattleAbsentee {
  user_id: string;
  user_name: string | null;
}

export interface EventDetail {
  id: number;
  escalation_token: string;
  state: string;
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
  battleboard_url: string | null;
  participation_mode: string;
  signup_mode: string;
  assignment_mode: string;
  autofill_mode: string;
  published_at: string | null;
  functions_released: boolean;
  total_snapshots: number;
  // Pontos de attendance — UM valor por evento, igual pra todo participante
  // independente do percent do split. Aceita fração (ex.: 0.5, 1.5).
  attendance: number;
  allowed_transitions: string[];
  verification: VerificationStep[];
  participants: Participant[];
  deaths: Death[];
  signups: EventSignup[];
  battle_absentees: BattleAbsentee[];
  payout: PayoutPreview | null;
  regear_summary: RegearSummary | null;
}

export interface RegearSummary {
  pending: number;
  approved: number;
  denied: number;
  approved_total: number;
}

// ── Nodes (calendário de nodes) ──────────────────────────────────────────────

export interface NodeDef {
  id: number;
  name: string;
  emoji: string | null;
  weight: number;
  sort: number;
}

export interface NodeEventLog {
  id: number;
  node_type: string;
  map_name: string;
  spawn_at: string;
  scout_id: number | null;
  scout_name: string | null;
  logged_at: string;
  // Captura em review: node vinculado a este evento + valor vendido (scout payout).
  captured: boolean;
  sold_value: number;
  event_id: number | null;
}

export interface NearNodesOut {
  ts: string;
  window_seconds: number;
  nodes: NodeEventLog[];
}

export interface NodeMaps {
  extras: string[];
  exclusions: string[];
  builtin: string[];
}

// ── Escalação (assentamento de inscritos nos slots da comp) ───────────────────

export interface EscalationRole {
  id: number;
  name: string;
  invisible_function: string | null;
  weapon_id: number | null;
  weapon_name: string | null;
  offhand: string | null;
  helmet: string | null;
  armor: string | null;
  boots: string | null;
  cape: string | null;
  food: string | null;
  play_style: string | null;
  obs: string | null;
  build_items: RegearItem[];
  color: string | null;
  q_spell: string | null;
  w_spell: string | null;
  passive_spell: string | null;
  gear_spells: Record<string, string | null>;
}
export interface EscalationSlot {
  id: number;
  position: number;
  label: string | null;
  fn: string | null;
  notes: string | null;
  roles: EscalationRole[];
}
export interface EscalationParty {
  id: number;
  position: number;
  name: string | null;
  slots: EscalationSlot[];
}
export interface Assignment {
  slot_id: number | null;
  user_id: number;
  user_name: string | null;
  game_role_id: number | null;
  locked: boolean;
}
export interface EscalationSignup {
  user_id: number;
  user_name: string | null;
  functions: string[];
  // Identidade do signup (ago/2026): pares (weapon_id, fn) + chaves prontas
  // pra casar com os pares de cada slot. `functions` (nomes de GameRole) é
  // legado — eventos finalizados sem backfill ficam só com functions.
  weapon_fns: { weapon_id: number; fn: string | null }[];
  keys: string[];
}
export interface EscalationOut {
  event: {
    id: number;
    guild_id: string;
    title: string | null;
    scheduled_at: string | null;
    state: string;
    comp_id: number | null;
    comp_name: string | null;
    functions_released: boolean;
    assignment_mode: string;
    autofill_mode: string;
  };
  parties: EscalationParty[];
  assignments: Assignment[];
  enlisted: EscalationSignup[];
  can_manage: boolean;
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

// ── Market history (fonte própria — companion captura do jogo) ──────────────
export interface MarketHistoryBucket { bucket_ts: number; item_count: number; avg_price: number; }
export interface MarketHistoryOut {
  item_id: string; quality: number; timescale: number;
  location: string | null; buckets: MarketHistoryBucket[];
}

export interface MarketCatalogItem { id: string; en: string; pt: string; c: string; }

// Item de um carrinho de craft compartilhável (mesmo formato entra/sai do backend).
export interface CraftCartItem {
  uniqueName: string;
  qty: number;
  useFocus: boolean;
  placeLabel: string;
  journalId: string | null;
  transmuteTargetId: string | null;
}

export async function getMarketCatalog(): Promise<MarketCatalogItem[]> {
  return req<MarketCatalogItem[]>("/market-history/catalog");
}

export interface MarketSnapshotRow { id: string; price: number; change_pct: number; demand: number; source: string; }

// region = servidor do Albion (west|east|europe, mesmo valor do seletor do site).
export async function getMarketSnapshot(region: string): Promise<MarketSnapshotRow[]> {
  return req<MarketSnapshotRow[]>(`/market-history/snapshot?region=${encodeURIComponent(region)}`);
}

export async function getMarketHistory(
  itemId: string, region: string, quality = 1, timescale = 1, location?: string,
): Promise<MarketHistoryOut> {
  const qs = new URLSearchParams({
    region, quality: String(quality), timescale: String(timescale),
  });
  if (location) qs.set("location", location);
  return req<MarketHistoryOut>(`/market-history/${encodeURIComponent(itemId)}?${qs}`);
}

export async function fetchRetry(input: RequestInfo, retries = 3): Promise<Response> {
  let res: Response = await fetch(input);
  for (let i = 1; i < retries && !res.ok && res.status >= 500; i++) {
    await new Promise(r => setTimeout(r, 800 * i));
    res = await fetch(input);
  }
  return res;
}

// ── Estado global "backend fora do ar" (banner no App) ──────────────────────
// req() marca down quando o fetch falha na rede; o App mostra o banner e faz
// poll de /health até voltar. Qualquer resposta HTTP (mesmo erro) = backend vivo.
let _backendDown = false;
const _downListeners = new Set<(down: boolean) => void>();

export function onBackendDown(cb: (down: boolean) => void): () => void {
  _downListeners.add(cb);
  cb(_backendDown);
  return () => { _downListeners.delete(cb); };
}

export function setBackendDown(down: boolean): void {
  if (_backendDown === down) return;
  _backendDown = down;
  for (const cb of _downListeners) cb(down);
}

// Mensagens amigáveis pra falhas que não têm detail humano do backend —
// api.ts não tem acesso ao hook de idioma, lê o mesmo localStorage do i18n.
const _ERR_MSG: Record<string, { net: string; server: string }> = {
  pt: { net: "Sem conexão com o servidor. Tente novamente.", server: "Erro no servidor. Tente novamente em instantes." },
  en: { net: "Can't reach the server. Try again.", server: "Server error. Try again in a moment." },
  es: { net: "Sin conexión con el servidor. Inténtalo de nuevo.", server: "Error del servidor. Inténtalo de nuevo en un momento." },
};

function _errMsg(kind: "net" | "server"): string {
  const lang = localStorage.getItem("lang") ?? "pt";
  return (_ERR_MSG[lang] ?? _ERR_MSG.pt)[kind];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData: deixa o fetch definir o Content-Type (multipart + boundary).
  const isForm = init?.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "include",
      ...(isForm ? {} : { headers: { "Content-Type": "application/json" } }),
      ...init,
    });
  } catch (e) {
    // Falha de REDE (backend fora/aba offline) — não uma resposta HTTP.
    setBackendDown(true);
    throw new Error(_errMsg("net"));
  }
  setBackendDown(false);
  if (!res.ok) {
    let detail: string | undefined;
    try {
      detail = (await res.json()).detail;
    } catch {
      // sem corpo JSON
    }
    // 5xx sem detail humano (ou "Internal Server Error" cru) → mensagem
    // amigável em vez de vazar statusText técnico pro usuário.
    if (!detail || (res.status >= 500 && /internal server error/i.test(detail))) {
      detail = res.status >= 500 ? _errMsg("server") : (detail ?? res.statusText);
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : await res.json();
}

export const BOT_INVITE = `https://discord.com/oauth2/authorize?client_id=1518276093294153859&permissions=8&scope=bot+applications.commands`;

function profileImageForm(file: File, crop?: CropRect): FormData {
  const form = new FormData();
  form.append("file", file);
  if (crop) {
    form.append("crop_x", String(crop.x));
    form.append("crop_y", String(crop.y));
    form.append("crop_w", String(crop.w));
    form.append("crop_h", String(crop.h));
  }
  return form;
}

export const api = {
  me: () => req<Me | null>("/auth/me").catch(() => null),
  // Preferência pessoal (não-por-guilda) da calculadora de craft — vazio se
  // deslogado; PUT exige login (401 caso contrário, ver deps.require_user).
  getCraftFocusEfficiency: () => req<Record<string, number>>("/craft/focus-efficiency"),
  setCraftFocusEfficiency: (values: Record<string, number>) =>
    req<Record<string, number>>("/craft/focus-efficiency", { method: "PUT", body: JSON.stringify({ values }) }),
  // ── Carrinho de craft compartilhável via link ──────────────────────────────
  // POST devolve um código curto; GET recupera os itens. Sem auth.
  saveCraftCart: (items: CraftCartItem[]) =>
    req<{ code: string }>("/craft/carts", { method: "POST", body: JSON.stringify({ items }) }),
  loadCraftCart: (code: string) =>
    req<{ code: string; items: CraftCartItem[]; created_at: string }>(`/craft/carts/${encodeURIComponent(code)}`),
  myDiscordGuilds: () => req<DiscordGuild[]>("/auth/guilds"),
  selectGuild: (guild_id: string, guild_name: string, icon: string | null) =>
    req<{ guild_id: string; bot_present: boolean }>("/auth/select-guild", {
      method: "POST",
      body: JSON.stringify({ guild_id, guild_name, icon }),
    }),
  switchGuild: (guild_id: string) =>
    req<{ guild_id: string; bot_present: boolean }>(`/auth/switch-guild/${guild_id}`, { method: "POST" }),
  mySiteGuilds: () => req<SiteGuild[]>("/auth/my-site-guilds"),
  guildInfo: (guild_id: string) => req<SiteGuild & { albion_alliance_id: string | null; albion_alliance_name: string | null; settings: Record<string, unknown>; bank_balance: number }>(`/auth/guild-info/${guild_id}`),
  guildAuditLog: (guild_id: string, params: { before_id?: number; after_id?: number; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.before_id) query.set("before_id", String(params.before_id));
    if (params.after_id) query.set("after_id", String(params.after_id));
    if (params.limit) query.set("limit", String(params.limit));
    const suffix = query.size ? `?${query}` : "";
    return req<{ entries: AuditLogEntry[]; has_more: boolean }>(`/auth/guilds/${guild_id}/audit-log${suffix}`);
  },
  updateGuildSettings: (guild_id: string, payload: {
    albion_guild_name?: string | null; albion_guild_region?: string | null; register_role_id?: string | null;
    ally_role_id?: string | null; ally_allowed_guilds?: string[] | null; bot_language?: string | null;
    // Default (chave ausente) = true — /register exige guilda Albion e o bot
    // vigia saídas removendo o cargo. False: checagem só no self-register;
    // registrar terceiros é de confiança (sem vigilância).
    register_remove_role_on_leave?: boolean | null;
    events_channel_id?: string | null; event_review_channel_id?: string | null; event_weapon_gates?: Record<string, string[]> | null;
    signup_min_builds?: number | null;
    nodes_calendar_channel_id?: string | null;
    voice_cta_channel_id?: string | null; trial_percent?: number | null;
    trial_role_id?: string | null;
    // "none" | "leftover" | "full" | "guild_backed" — regear é sempre
    // calculado; isto só decide como a tab vira lootsplit (ver
    // events.get_lootsplit_mode no backend).
    lootsplit_mode?: string | null;
    // % da tab debitada pro banco da guilda ANTES do pool de participantes
    // (0-100, default 0). Só vale em modos com split. Ver events.get_guild_tax_percent.
    guild_tax_percent?: number | null;
    // "node" (default) | "tab" — de onde vem o bônus do scout (NodeDef.weight).
    // "node" = peso × sold_value (pool separado). "tab" = peso × tab_value,
    // deduzido da participant pool. Ver events.get_scout_bonus_source no backend.
    scout_bonus_source?: string | null;
    // % global do split que vai pro scout (0-100, default null = legacy weight
    // direto). Cada node multiplica: weight × scout_percent/100. Ver
    // events.get_scout_percent no backend.
    scout_percent?: number | null;
    // Subconjunto de ["created","t10min","in_progress","review"] — momentos em
    // que o mass-info do bot deleta a embed e reenvia com @everyone. Default
    // (chave ausente) = os 3 primeiros; [] = tudo off. Ver event_signups.py.
    events_ping_triggers?: string[] | null;
    // null = bot cria/mantém o canal próprio "logs-bot" (admin-only); setar um
    // canal faz o bot usar esse. Ver cogs/audit_log.py ensure_logs_channel.
    logs_channel_id?: string | null;
    // Canal dedicado onde o bot cria uma thread de regear por evento ao entrar
    // em IN_PROGRESS. Ver cogs/regear_threads.py.
    regear_thread_channel_id?: string | null;
    // Canal dedicado onde o bot cria uma thread de lootlog por evento ao entrar
    // em IN_PROGRESS. .csv do lootlogger postado na thread vira LootLogSubmission
    // atrelado ao evento. Ver cogs/lootlog_threads.py.
    lootlog_thread_channel_id?: string | null;
    // Default (chave ausente) = true — desligar faz o bot parar de criar/manter
    // o canal de logs e de postar. Ver cogs/audit_log.py.
    bot_logs_enabled?: boolean | null;
    // Canal onde o bot posta novas batalhas detectadas (link + imagem PNG
    // de resumo). Ver cogs/battle_feed.py.
    battle_feed_channel_id?: string | null;
    // Mínimo de jogadores pra uma batalha ser postada no feed (default 10).
    battle_feed_min_players?: number | null;
    // ── Juicy kills: kills com silver_dropped >= min postadas numa sala.
    juicy_kill_channel_id?: string | null;
    juicy_kill_min_silver?: number | null;
    juicy_kill_min_fame?: number | null;
    juicy_kill_regions?: string[] | null;
    energy_control_channel_id?: string | null;
  }) =>
    req<{ ok: boolean; albion_guild_resolved: boolean }>(`/auth/guild-settings/${guild_id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  myPermissions: () => req<Permissions>("/auth/my-permissions"),

  // ── Perfil customizado ────────────────────────────────────────────────────
  getMyProfile: () => req<MyProfile>("/profile/me"),
  setProfileTheme: (theme: ProfileTheme) =>
    req<MyProfile>("/profile/theme", { method: "PUT", body: JSON.stringify({ theme }) }),
  uploadProfileAvatar: (file: File, crop?: CropRect) =>
    req<MyProfile>("/profile/avatar", { method: "POST", body: profileImageForm(file, crop) }),
  removeProfileAvatar: () => req<MyProfile>("/profile/avatar", { method: "DELETE" }),
  uploadProfileBanner: (file: File, crop?: CropRect) =>
    req<MyProfile>("/profile/banner", { method: "POST", body: profileImageForm(file, crop) }),
  removeProfileBanner: () => req<MyProfile>("/profile/banner", { method: "DELETE" }),
  guildDiscordRoles: (guild_id: string) => req<DiscordRole[]>(`/auth/guild-discord-roles/${guild_id}`),
  guildDiscordChannels: (guild_id: string, voice = false) =>
    req<{ id: string; name: string; position: number }[]>(
      `/auth/guild-discord-channels/${guild_id}${voice ? "?voice=true" : ""}`,
    ),
  guildAllies: (guild_id: string) => req<{ id: string; name: string }[]>(`/auth/guild-allies/${guild_id}`),
  listAlbionLinks: (guild_id: string) =>
    req<{
      primary: string | null;
      primary_name: string | null;
      guild_verified: boolean;
      links: { albion_guild_id: string; albion_guild_name: string; region: string; alliance_id: string | null; alliance_name: string | null; verified: boolean }[];
    }>(`/auth/guilds/${guild_id}/albion-links`),
  addAlbionLink: (guild_id: string, name: string, region: string) =>
    req<{ ok: boolean; albion_guild_id: string }>(`/auth/guilds/${guild_id}/albion-links`, {
      method: "POST",
      body: JSON.stringify({ name, region }),
    }),
  removeAlbionLink: (guild_id: string, albion_guild_id: string) =>
    req<{ ok: boolean }>(`/auth/guilds/${guild_id}/albion-links/${albion_guild_id}`, { method: "DELETE" }),
  updateRolePermissions: (guild_id: string, role_id: string, role_name: string, permissions: Partial<Permissions>) =>
    req<{ ok: boolean }>(`/auth/guild-discord-roles/${guild_id}/${role_id}`, {
      method: "PATCH",
      body: JSON.stringify({ role_name, permissions }),
    }),
  guildCommands: (guild_id: string) =>
    req<{ name: string; description: string; category: string; enabled: boolean; allowed_roles: string[] }[]>(`/auth/guild-commands/${guild_id}`),
  toggleGuildCommand: (guild_id: string, name: string, enabled: boolean) =>
    req<{ ok: boolean }>(`/auth/guild-commands/${guild_id}/${name}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  updateCommandRoles: (guild_id: string, name: string, role_keys: string[]) =>
    req<{ ok: boolean }>(`/auth/guild-commands/${guild_id}/${name}/roles`, {
      method: "PATCH",
      body: JSON.stringify({ role_keys }),
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
  getCompFnTypes: () => req<{ fn_types: ApiFnType[] }>(`/guilds/${g()}/comps/fn-types`),
  putCompFnTypes: (fn_types: ApiFnType[]) =>
    req<{ fn_types: ApiFnType[] }>(`/guilds/${g()}/comps/fn-types`, {
      method: "PUT",
      body: JSON.stringify({ fn_types }),
    }),
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
  // Sem tipo — todo evento sempre calcula regear + lootsplit (o
  // lootsplit_mode é setting da guilda, não do evento).
  createEvent: (payload: {
    title?: string | null; scheduled_at?: string | null; comp_id?: number | null;
    message?: string | null;
    publish?: boolean; signup_mode?: string; assignment_mode?: string; autofill_mode?: string;
  }) =>
    req<EventDetail>(`/guilds/${g()}/events`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  // guildId opcional (default = guilda corrente via g()) — a página de
  // escalação é deep-link e passa a guilda explícita, já que pode não ter
  // sido selecionada como "corrente" nesta sessão do site.
  releaseFunctions: (id: number, released: boolean, guildId?: string) =>
    req<EventDetail>(`/guilds/${guildId ?? g()}/events/${id}/release-functions`, {
      method: "POST",
      body: JSON.stringify({ released }),
    }),
  setEventAttendance: (id: number, value: number) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/attendance`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),
  // PATCH parcial: só os campos passados são atualizados. comp_id=null
  // desvincula a comp; trocar a comp preserva inscritos e pede novas roles por DM.
  updateEvent: (id: number, payload: {
    title?: string | null; scheduled_at?: string | null;
    comp_id?: number | null; attendance?: number; signup_mode?: string;
    assignment_mode?: string; autofill_mode?: string; confirm_comp_reset?: boolean;
  }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listSignups: (id: number) => req<EventSignup[]>(`/guilds/${g()}/events/${id}/signups`),
  getEvent: (id: number) => req<EventDetail>(`/guilds/${g()}/events/${id}`),
  // Escalação: guildId explícito (página é deep-link, não depende da guilda corrente).
  // ponytail: guildId é string — snowflake do Discord > 2^53 perde em number.
  escalacao: (guildId: string, eventId: number) =>
    req<EscalationOut>(`/guilds/${guildId}/events/${eventId}/escalacao`),
  publicEscalacao: (token: string) =>
    req<EscalationOut>(`/public/escalacao/${token}`),
  assignEscalacao: (guildId: string, eventId: number, payload: {
    slot_id: number; user_id: number; user_name?: string | null; game_role_id: number;
  }) =>
    req<Assignment>(`/guilds/${guildId}/events/${eventId}/escalacao/assign`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  autofillEscalacao: (guildId: string, eventId: number) =>
    req<{ assigned: number; run_id: string | null }>(`/guilds/${guildId}/events/${eventId}/escalacao/autofill`, { method: "POST" }),
  previewAutofillEscalacao: (guildId: string, eventId: number) =>
    req<{ assignments: { user_name: string | null; game_role_name: string; slot_id: number }[] }>(
      `/guilds/${guildId}/events/${eventId}/escalacao/autofill/preview`,
    ),
  undoAutofillEscalacao: (guildId: string, eventId: number, runId: string) =>
    req<{ removed: number }>(`/guilds/${guildId}/events/${eventId}/escalacao/autofill/undo?run_id=${encodeURIComponent(runId)}`, { method: "POST" }),
  unassignSlot: (guildId: string, eventId: number, slotId: number) =>
    req<{ ok: boolean }>(`/guilds/${guildId}/events/${eventId}/escalacao/slot/${slotId}`, { method: "DELETE" }),
  unassignUser: (guildId: string, eventId: number, userId: number) =>
    req<{ ok: boolean }>(`/guilds/${guildId}/events/${eventId}/escalacao/user/${userId}`, { method: "DELETE" }),
  escalacaoPrices: (guildId: string, eventId: number) =>
    req<{ prices: Record<string, number> }>(`/guilds/${guildId}/events/${eventId}/escalacao/prices`),
  transition: (id: number, to: string) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({ to }),
    }),
  setStep: (id: number, step: string, completed: boolean, data?: Record<string, unknown>) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/verification/${step}`, {
      method: "POST",
      body: JSON.stringify({ completed, data: data ?? null }),
    }),
  // Captura de node em review: marca se pegamos o node + valor vendido. O scout
  // (quem adicionou) recebe NodeDef.weight × sold_value — pool separado da tab.
  claimNode: (id: number, nodeLogId: number, payload: { captured: boolean; sold_value: number }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/nodes/${nodeLogId}/claim`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addParticipant: (id: number, payload: { user_id: string; user_name?: string; percent?: number; is_valid?: boolean }) =>
    req<EventDetail>(`/guilds/${g()}/events/${id}/participants`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  searchMembers: (q: string) =>
    req<{ user_id: string; username: string; global_name: string | null; avatar: string | null; in_game_name: string | null }[]>(
      `/guilds/${g()}/members/search?q=${encodeURIComponent(q)}`,
    ),
  updateParticipant: (id: number, participantId: number, payload: { game_role_id?: number | null; percent?: number; is_trial?: boolean; is_valid?: boolean }) =>
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

  // Reconciliação própria (lootlog + baú + mortes).
  getReconcile: (eventId: number) =>
    req<UnifiedReconcile>(`/guilds/${g()}/events/${eventId}/reconcile`),
  // Toggle "conferido" num item devido (vermelho ↔ amarelo). Retorna novo estado.
  verifyReconcileItem: (eventId: number, looted_by: string, item_id: string) =>
    req<{ verified: boolean }>(`/guilds/${g()}/events/${eventId}/reconcile/verify`, {
      method: "POST",
      body: JSON.stringify({ looted_by, item_id }),
    }),
  uploadChest: (eventId: number, entries: ChestUploadEntry[], replace = true) =>
    req<LootReconcile>(`/guilds/${g()}/events/${eventId}/chest`, {
      method: "POST",
      body: JSON.stringify({ snapshot_at: new Date().toISOString(), entries, replace }),
    }),

  // ── Regear por screenshot ──────────────────────────────────────────────────
  listRegear: (status?: string, eventId?: number) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (eventId != null) params.set("event_id", String(eventId));
    const qs = params.toString();
    return req<RegearList>(`/guilds/${g()}/regear${qs ? `?${qs}` : ""}`);
  },
  eventRegears: (eventId: number) =>
    req<RegearList>(`/guilds/${g()}/events/${eventId}/regears`),
  getRegear: (id: number) => req<RegearRequest>(`/guilds/${g()}/regear/${id}`),
  updateRegear: (id: number, payload: RegearUpdatePayload) =>
    req<RegearRequest>(`/guilds/${g()}/regear/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  removeRegear: (id: number) =>
    req<{ ok: boolean }>(`/guilds/${g()}/regear/${id}`, { method: "DELETE" }),
  getRegearSettings: () => req<RegearSettings>(`/guilds/${g()}/regear/settings`),
  setRegearSettings: (payload: Partial<RegearSettings>) =>
    req<RegearSettings>(`/guilds/${g()}/regear/settings`, { method: "PUT", body: JSON.stringify(payload) }),
  regearScreenshotUrl: (id: number) => `/guilds/${g()}/regear/${id}/screenshot`,

  // ── Lootlog anônimo ─────────────────────────────────────────────────────────
  // Submit só pelo Discord (botão → modal). O site só lista/envios e remove.
  listLootLog: (eventId: number) =>
    req<LootLogList>(`/guilds/${g()}/lootlog?event_id=${eventId}`),
  removeLootLog: (submissionId: number) =>
    req<{ ok: boolean }>(`/guilds/${g()}/lootlog/${submissionId}`, { method: "DELETE" }),
  getLootLogSettings: () => req<LootLogSettings>(`/guilds/${g()}/lootlog/settings`),
  setLootLogSettings: (payload: Partial<LootLogSettings>) =>
    req<LootLogSettings>(`/guilds/${g()}/lootlog/settings`, { method: "PUT", body: JSON.stringify(payload) }),

  // ── Nodes (tipos de node + mapas; adicionar node em si é pelo Discord) ─────
  listNodeDefs: () => req<NodeDef[]>(`/guilds/${g()}/nodes/defs`),
  upsertNodeDef: (payload: { name: string; emoji: string | null; weight: number; sort: number }) =>
    req<NodeDef>(`/guilds/${g()}/nodes/defs`, { method: "POST", body: JSON.stringify(payload) }),
  updateNodeDef: (defId: number, payload: { name?: string | null; emoji?: string | null; weight?: number | null }) =>
    req<NodeDef>(`/guilds/${g()}/nodes/defs/${defId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  removeNodeDef: (name: string) =>
    req<void>(`/guilds/${g()}/nodes/defs/${encodeURIComponent(name)}`, { method: "DELETE" }),
  listNodeMaps: () => req<NodeMaps>(`/guilds/${g()}/nodes/maps`),
  addNodeMap: (map_name: string) =>
    req<NodeMaps>(`/guilds/${g()}/nodes/maps`, { method: "POST", body: JSON.stringify({ map_name }) }),
  removeNodeMap: (map_name: string) =>
    req<NodeMaps>(`/guilds/${g()}/nodes/maps/${encodeURIComponent(map_name)}`, { method: "DELETE" }),
  // Nodes próximos de um timestamp (default = agora) — usado pela revisão de
  // evento pra perguntar se capturamos cada node e o valor vendido.
  nearNodes: (ts?: string) =>
    req<NearNodesOut>(`/guilds/${g()}/nodes/near${ts ? `?ts=${encodeURIComponent(ts)}` : ""}`),

  // ── Portal do membro (/member/*) — carteira, energia, roles arma+fn ──────
  // Guilda pelo prefixo g() (padrão dos tabs; o componente sincroniza com
  // setGuild, ver RegearPage).
  memberWallet: (limit = 50, offset = 0) =>
    req<MemberWallet>(`/guilds/${g()}/member/wallet?limit=${limit}&offset=${offset}`),
  memberEnergy: (limit = 50, offset = 0) =>
    req<MemberEnergy>(`/guilds/${g()}/member/energy?limit=${limit}&offset=${offset}`),
  memberWeaponFnPrefs: () =>
    req<{ preferences: WeaponFnPref[]; valid_pairs: WeaponFnValidPair[] }>(`/guilds/${g()}/member/weapon-fn-preferences`),
  memberWeaponFnPrefsPut: (preferences: { weapon_id: number; fn: string }[]) =>
    req<{ preferences: WeaponFnPref[]; valid_pairs: WeaponFnValidPair[] }>(`/guilds/${g()}/member/weapon-fn-preferences`, {
      method: "PUT",
      body: JSON.stringify({ preferences }),
    }),

  // ── Portal do membro: eventos publicados + self-signup ────────────────────
  memberEvents: () =>
    req<MemberEventSummary[]>(`/guilds/${g()}/member/events`),
  memberEvent: (eventId: number) =>
    req<MemberEventDetail>(`/guilds/${g()}/member/events/${eventId}`),
  memberSignupOptions: (eventId: number) =>
    req<MemberSignupOptions>(`/guilds/${g()}/member/events/${eventId}/signup-options`),
  memberSignup: (eventId: number, options: string[]) =>
    req<MemberSignup>(`/guilds/${g()}/member/events/${eventId}/signup`, {
      method: "POST",
      body: JSON.stringify({ options }),
    }),
  memberSignupDelete: (eventId: number) =>
    req<void>(`/guilds/${g()}/member/events/${eventId}/signup`, { method: "DELETE" }),

  // ── Portal do membro: comps read-only ─────────────────────────────────────
  memberComps: () =>
    req<MemberCompSummary[]>(`/guilds/${g()}/member/comps`),
  memberComp: (compId: number) =>
    req<MemberCompDetail>(`/guilds/${g()}/member/comps/${compId}`),

  // ── Admin de energia (perm energy.manage) ─────────────────────────────────
  energyAdminOverview: () =>
    req<EnergyAdminOverview>(`/guilds/${g()}/energy-admin/overview`),
  energyAdminLogImport: (log_text: string) =>
    req<{ result: EnergyImportResult }>(`/guilds/${g()}/energy-admin/log-import`, {
      method: "POST",
      body: JSON.stringify({ log_text }),
    }),
  energyAdminSet: (user_id: number, value: number, reason?: string) =>
    req<{ user_id: number; balance: number }>(`/guilds/${g()}/energy-admin/set`, {
      method: "POST",
      body: JSON.stringify({ user_id, value, reason: reason || null }),
    }),
  energyAdminWhitelistToggle: (user_id: number) =>
    req<{ user_id: number; whitelisted: boolean }>(`/guilds/${g()}/energy-admin/whitelist/${user_id}`, {
      method: "POST",
    }),
};
