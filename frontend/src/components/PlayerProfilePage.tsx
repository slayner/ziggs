import { useEffect, useMemo, useRef, useState } from "react";
import { navigate } from "../router";
import { silver, dateUTC, monthYearUTC } from "../lib/format";
import { itemRenderUrl, NO_WEAPON_ICON_ID } from "../data/albion-items";
import { useLang, useT, REGION_LABELS } from "../i18n";

const API = import.meta.env.DEV ? "http://localhost:8000" : "";
// Inverso de PLAYER_PREFIXES (router.ts) — pra montar o link de perfil do
// oponente a partir da região (mesma região do jogador do perfil: kills só
// acontecem dentro do mesmo servidor Albion).
const REGION_PREFIX: Record<string, string> = { americas: "am", asia: "as", europe: "eu" };

/* ── Types ─────────────────────────────────────────────────── */

interface GatherTotal { Total: number }
interface LifetimeStatistics {
  PvE?: { Total: number };
  Crafting?: { Total: number };
  Gathering?: { Fiber?: GatherTotal; Hide?: GatherTotal; Ore?: GatherTotal; Rock?: GatherTotal; Wood?: GatherTotal; All?: GatherTotal };
  FishingFame?: number;
  FarmingFame?: number;
}

interface EquipSlot { Type: string; Quality?: number }
type Equipment = Record<string, EquipSlot | null>;

type BattleFaction = { guild_id: string; guild_name: string; alliance_name: string | null; kills: number; player_count: number };

interface ZiggsKill {
  event_id: string;
  timestamp: string;
  fame: number;
  is_solo: boolean;
  participant_count: number;
  other_name: string | null;
  other_guild_name: string | null;
  other_alliance_name: string | null;
  equipment: Equipment;
  other_equipment: Equipment;
  role: string | null;
  battle_public_id: string | null;
  battle_factions: BattleFaction[];
}

type Activity = ZiggsKill & { kind: "kill" | "death" };

interface ZiggsBattle {
  public_id: string;
  region: string;
  start_time: string;
  cluster: string | null;
  is_zvz: boolean;
  players_total: number;
  total_fame: number;
  kill_count: number;
  factions: BattleFaction[];
  kills: number;
  deaths: number;
  kill_fame: number;
}

interface ZiggsData {
  region: string;
  first_seen_at: string;
  last_seen_at: string;
  guild_history: {
    guild_id: string | null; guild_name: string | null;
    alliance_id: string | null; alliance_tag: string | null;
    start: string; end: string | null;
    kills: number; deaths: number; silver_dropped: number;
    guild_is_deleted?: boolean; alliance_is_deleted?: boolean;
  }[];
  fame_history: { t: string; kill_fame: number; death_fame: number }[];
  battle_history: ZiggsBattle[];
  top_weapons: TopWeapon[];
  kills: ZiggsKill[];
  deaths: ZiggsKill[];
  silver_dropped: number;
}

type BuildBase = {
  weapon_base: string | null; offhand_base: string | null; helmet_base: string | null;
  armor_base: string | null; boots_base: string | null; kills: number;
};
type TopWeapon = { weapon_base: string; points: number; builds: BuildBase[] };

interface PlayerProfile {
  Id: string;
  Name: string;
  GuildId?: string;
  GuildName?: string;
  AllianceId?: string;
  AllianceName?: string;
  AllianceTag?: string;
  Avatar?: string;
  KillFame: number;
  DeathFame: number;
  LifetimeStatistics?: LifetimeStatistics;
  _ziggs: ZiggsData;
  _is_deleted?: boolean;
}

/* ── Helpers ────────────────────────────────────────────────── */

const fameColor = (n: number) =>
  n >= 1_000_000_000 ? "text-amber-300" :
  n >= 100_000_000  ? "text-yellow-300" :
  n >= 10_000_000   ? "text-zinc-100"   : "text-zinc-400";

function fameShort(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "0";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d`;
  return monthYearUTC(ts);
}

/* ── Sub-components ─────────────────────────────────────────── */

// `Avatar` da API do Albion é um cosmético (ex: "AVATAR_03"), não um item de
// equipamento — não dá pra renderizar via /render/item/. O CDN não-oficial
// cdn.albiononline2d.com está atrás de um bot-challenge da Cloudflare (403
// mesmo com header de browser), então servimos do nosso próprio
// frontend/public/avatars/ (cópia 1:1 dos nomes de arquivo da API).
// Os 5 avatares base do Albion (sempre existem em public/avatars/) — usados
// quando o avatar real do jogador não tem arquivo correspondente. Escolha
// determinística pelo nome, pra não trocar de avatar a cada render.
const DEFAULT_AVATARS = ["AVATAR_01", "AVATAR_02", "AVATAR_03", "AVATAR_04", "AVATAR_05"];
function defaultAvatarFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return DEFAULT_AVATARS[Math.abs(h) % DEFAULT_AVATARS.length];
}

function PlayerAvatar({ avatar, name, size = 48 }: { avatar?: string | null; name: string; size?: number }) {
  const [stage, setStage] = useState<"primary" | "default" | "initials">(avatar ? "primary" : "default");
  if (stage !== "initials") {
    const id = stage === "primary" ? avatar! : defaultAvatarFor(name);
    return (
      <img
        src={`/avatars/${encodeURIComponent(id)}.png`}
        alt={name}
        width={size}
        height={size}
        className="rounded-full"
        onError={() => setStage(s => (s === "primary" ? "default" : "initials"))}
      />
    );
  }
  return (
    <div
      className="rounded-full bg-zinc-700 flex items-center justify-center font-bold text-zinc-300"
      style={{ width: size, height: size, fontSize: size * 0.4 }}
    >
      {name[0]?.toUpperCase()}
    </div>
  );
}

function FameRow({ label, value, isGlowing, onGlowEnd }: {
  label: string; value: number; isGlowing?: boolean; onGlowEnd?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span
        className={`text-sm font-bold tabular-nums ${fameColor(value)}${isGlowing ? " dash-text-glow" : ""}`}
        onAnimationEnd={onGlowEnd}
      >
        {fameShort(value)}
      </span>
    </div>
  );
}

// Fama de coleta não é uma contagem útil de exibir direto — divide por 200
// como estimativa "quantos recursos esse jogador já coletou" (abaixo de 200
// de fama vira 0). Não usa fameColor: não é uma escala de fama, é contagem.
function ResourceCountRow({ label, fame }: { label: string; fame: number }) {
  const count = Math.floor(fame / 200);
  return (
    <div className="flex flex-col items-center justify-center gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
      <span className="text-sm font-bold tabular-nums text-zinc-100">{count < 1 ? 0 : count}</span>
    </div>
  );
}

// "T7 excelente" — normalização de exibição pedida explicitamente pro
// widget de armas mais usadas: mostra sempre o mesmo tier/qualidade pra não
// disputar atenção com o resto do header, independente do que foi usado de
// verdade em cada kill (build é identificada só pela base do item).
function t7Render(base: string | null): string | null {
  return base ? itemRenderUrl(`T7_${base}`, 4) : null;
}

function BuildRow({ b }: { b: BuildBase }) {
  const t = useT();
  const slots: { base: string | null; label: string }[] = [
    { base: b.weapon_base, label: t("equipSlotWeapon") },
    { base: b.offhand_base, label: t("equipSlotOffhand") },
    { base: b.helmet_base, label: t("equipSlotHelmet") },
    { base: b.armor_base, label: t("equipSlotArmor") },
    { base: b.boots_base, label: t("equipSlotBoots") },
  ];
  return (
    <div className="flex items-center gap-2 py-1.5">
      <div className="flex gap-1">
        {slots.map((s, i) => {
          const src = t7Render(s.base);
          return src ? (
            <img key={i} src={src} alt={s.label} title={s.label} width={28} height={28}
              className="rounded border border-zinc-800 bg-zinc-950" />
          ) : null;
        })}
      </div>
      <span className="ml-auto shrink-0 text-xs font-semibold text-amber-400/80 tabular-nums">{b.kills} {t("killsSuffix")}</span>
    </div>
  );
}

// Mesmo padrão de hover/pin do horizonte de eventos (BattlePage.tsx,
// KillTimelineChart): passa o mouse mostra, clica fixa aberto até clicar fora.
function TopWeaponsWidget({ weapons }: { weapons: TopWeapon[] }) {
  const t = useT();
  const [open, setOpen] = useState<{ x: number; y: number; weapon: TopWeapon; pinned: boolean } | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open?.pinned) return;
    function onDocMouseDown(ev: MouseEvent) {
      if (popupRef.current && !popupRef.current.contains(ev.target as Node)) setOpen(null);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open?.pinned]);

  if (!weapons.length) return null;

  return (
    <div className="flex shrink-0 items-end gap-3">
      {weapons.map(w => (
        <div key={w.weapon_base} className="flex flex-col items-center gap-1">
          <img
            src={itemRenderUrl(`T7_${w.weapon_base}`, 4)}
            alt={w.weapon_base}
            title={w.weapon_base}
            width={40}
            height={40}
            className="cursor-pointer rounded border border-zinc-800 bg-zinc-950"
            onMouseEnter={ev => setOpen(o => (o?.pinned ? o : { x: ev.clientX, y: ev.clientY, weapon: w, pinned: false }))}
            onMouseMove={ev => setOpen(o => (o && o.weapon === w && !o.pinned ? { ...o, x: ev.clientX, y: ev.clientY } : o))}
            onMouseLeave={() => setOpen(o => (o?.pinned ? o : null))}
            onClick={ev => setOpen({ x: ev.clientX, y: ev.clientY, weapon: w, pinned: true })}
          />
          <span className="text-[10px] text-zinc-500 tabular-nums">{w.points}</span>
        </div>
      ))}
      {open && (
        <div
          ref={popupRef}
          className="fixed z-50 w-64 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 shadow-lg"
          style={{ left: open.x - 12, top: open.y + 12, transform: "translateX(-100%)" }}
        >
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{t("topBuilds")}</div>
          <div className="divide-y divide-zinc-800/60">
            {open.weapon.builds.map((b, i) => <BuildRow key={i} b={b} />)}
          </div>
        </div>
      )}
    </div>
  );
}

// Layout 3×3 igual ao EquipGrid da comp builder (CompBuilder.tsx), mas com
// as chaves cruas do equipamento da API do Albion (MainHand/OffHand/Head/...)
// em vez do nosso DraftEquip. Ícone já vem com a moldura de qualidade (mesmo
// `itemRenderUrl` usado em BattlePage.tsx, que repassa ?quality= pro render).
const EQUIP_MINI_GRID: (string | null)[] = [
  null, "Head", "Cape",
  "MainHand", "Armor", "OffHand",
  "Potion", "Shoes", "Food",
];

function EquipMini({ equipment, label }: { equipment: Equipment; label?: string }) {
  return (
    <div>
      {label && <p className="mb-1 truncate text-[10px] uppercase tracking-wide text-zinc-600">{label}</p>}
      <div className="grid grid-cols-3 gap-1.5">
        {EQUIP_MINI_GRID.map((slot, i) => {
          if (!slot) return <div key={i} className="w-[54px] h-[54px]" />;
          const item = equipment[slot];
          // Slot de arma sem nada equipado: ícone de fallback (luta desarmado),
          // em vez de célula vazia — mesma convenção usada nas brackets.
          const iconId = item?.Type ?? (slot === "MainHand" ? NO_WEAPON_ICON_ID : null);
          return (
            <div key={i} className="w-[54px] h-[54px] rounded border border-zinc-800 bg-zinc-950/60 flex items-center justify-center overflow-hidden">
              {iconId && <img src={itemRenderUrl(iconId, item?.Quality ?? 0)} alt="" width={48} height={48} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// hh:mm em cima, dd/mm/aa abaixo — coluna compacta, sem repetir a data 2x.
function timeOverDate(ts: string): [string, string] {
  const d = new Date(ts);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const min = String(d.getUTCMinutes()).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const yy = String(d.getUTCFullYear()).slice(-2);
  return [`${hh}:${min}`, `${dd}/${mm}/${yy}`];
}

// Nome + guilda/aliança, sempre centralizado (usado nos dois lados da bracket).
// O link (quando tem onClick) só "agarra" o texto do nome, não a coluna
// inteira — clicar no espaço vazio ao lado não deve navegar.
function NameGuildBlock({ name, guild, alliance, onClick }: {
  name: string; guild: string | null; alliance?: string | null; onClick?: (e: React.MouseEvent) => void;
}) {
  const t = useT();
  return (
    <div className="min-w-0 flex-1 text-center">
      {onClick ? (
        <button onClick={onClick} className="max-w-full truncate text-sm font-medium text-zinc-100 hover:text-amber-400 hover:underline">
          {name}
        </button>
      ) : (
        <span className="block truncate text-sm font-medium text-zinc-100">{name}</span>
      )}
      <span className="block truncate text-[10px] text-zinc-600">
        {alliance && <span className="mr-1">[{alliance}]</span>}
        {guild ?? t("noGuild")}
      </span>
    </div>
  );
}

const albionKillboardUrl = (eventId: string) => `https://albiononline.com/en/killboard/kill/${eventId}`;

function ActivityRow({ ev, profileName, profileGuild, region, isNew, onGlowEnd }: {
  ev: Activity; profileName: string; profileGuild: string | null; region: string;
  isNew?: boolean; onGlowEnd?: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const isKill = ev.kind === "kill";
  const color = isKill ? "text-blue-400" : "text-red-400";
  const borderColor = isKill ? "#60a5fa" : "#f87171";
  const ownWeapon = ev.equipment.MainHand;
  const otherWeapon = ev.other_equipment.MainHand;
  const ownWeaponId = ownWeapon?.Type ?? NO_WEAPON_ICON_ID;
  const otherWeaponId = otherWeapon?.Type ?? NO_WEAPON_ICON_ID;
  const [time, date] = timeOverDate(ev.timestamp);

  const prefix = REGION_PREFIX[region];
  const goToOther = ev.other_name && prefix
    ? (e: React.MouseEvent) => { e.stopPropagation(); navigate(`/${prefix}/${encodeURIComponent(ev.other_name!)}`); }
    : undefined;

  return (
    <div className={`bg-zinc-900/60 border-l-2${isNew ? " dash-glow" : ""}`} style={{ borderLeftColor: borderColor }} onAnimationEnd={onGlowEnd}>
      {/* div (não button) porque o nome do oponente já é um botão clicável aninhado */}
      <div role="button" tabIndex={0} onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left cursor-pointer">
        <img src={itemRenderUrl(ownWeaponId, ownWeapon?.Quality ?? 0)} alt="" title={ownWeapon ? ownWeapon.Type : t("noWeaponEquipped")} width={28} height={28} className="shrink-0" />

        <NameGuildBlock name={profileName} guild={profileGuild} />

        <span className="w-12 shrink-0 flex flex-col items-center leading-tight">
          <span className="text-[11px] font-medium text-zinc-300 tabular-nums">{time}</span>
          <span className="text-[10px] text-zinc-600 tabular-nums">{date}</span>
        </span>

        <NameGuildBlock
          name={ev.other_name ?? "?"}
          guild={ev.other_guild_name}
          alliance={ev.other_alliance_name}
          onClick={goToOther}
        />

        <img src={itemRenderUrl(otherWeaponId, otherWeapon?.Quality ?? 0)} alt="" title={otherWeapon ? otherWeapon.Type : t("noWeaponEquipped")} width={28} height={28} className="shrink-0" />
      </div>
      {open && (
        <div className="flex items-center justify-between gap-4 border-t border-zinc-800 px-3 py-3">
          <EquipMini equipment={ev.equipment} />
          <div className="flex shrink-0 flex-col items-center gap-1.5 text-center">
            <div className={`text-sm font-semibold tabular-nums ${color}`}>{silver(ev.fame)}</div>
            {ev.battle_public_id ? (
              <button onClick={() => navigate(`/${ev.battle_public_id}`)} className="hover:opacity-80 transition-opacity">
                {(ev.battle_factions ?? []).length > 0
                  ? <FactionHeatmap factions={ev.battle_factions} />
                  : <span className="text-[11px] text-zinc-500">{t("viewBattleLink")}</span>}
              </button>
            ) : ev.is_solo ? (
              <a href={albionKillboardUrl(ev.event_id)} target="_blank" rel="noopener noreferrer" className="text-[11px] text-zinc-500 hover:text-amber-400">
                {t("viewOnAlbion")}
              </a>
            ) : null}
            <div className="text-[10px] capitalize text-zinc-600">{ev.role ?? "—"}</div>
          </div>
          <EquipMini equipment={ev.other_equipment} />
        </div>
      )}
    </div>
  );
}

// Mesmo modelo da listagem de batalhas (ver BattleTracker.tsx / Dashboard.tsx)
// — heatmap por facção em vez do cluster (sempre nulo na API do Albion hoje).
const BATTLE_HEAT_MAX: [number, number, number] = [0x66, 0x71, 0x60];
const BATTLE_HEAT_MIN: [number, number, number] = [0x52, 0x52, 0x5c];

function battleHeatColor(kills: number, maxKills: number, minKills: number): string {
  const t = maxKills === minKills ? 0 : (maxKills - kills) / (maxKills - minKills);
  const [r, g, b] = BATTLE_HEAT_MAX.map((c, i) => Math.round(c + (BATTLE_HEAT_MIN[i] - c) * t));
  return `rgb(${r}, ${g}, ${b})`;
}

function battleFactionTag(f: ZiggsBattle["factions"][number]): string {
  return f.alliance_name ? `[${f.alliance_name}]` : f.guild_name;
}

function battleFameShort(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return n > 0 ? String(n) : "—";
}

// Etiqueta centralizada por facção (tag colorida pelo heatmap de kills +
// contagem de jogadores abaixo) — mesmo conteúdo usado na listagem de
// batalhas, reaproveitado também no link de "ver luta" da atividade.
function FactionHeatmap({ factions }: { factions: BattleFaction[] }) {
  if (factions.length === 0) return null;
  const maxFactionKills = Math.max(...factions.map(f => f.kills));
  const minFactionKills = Math.min(...factions.map(f => f.kills));
  return (
    <span className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-sm font-semibold">
      {factions.map(f => (
        <span key={f.guild_id} className="flex flex-col items-center leading-tight">
          <span style={{ color: battleHeatColor(f.kills, maxFactionKills, minFactionKills) }}>
            {battleFactionTag(f)}
          </span>
          <span className="text-[10px] font-normal text-zinc-600">{f.player_count}</span>
        </span>
      ))}
    </span>
  );
}

function BattleRow({ b, isNew, onGlowEnd }: { b: ZiggsBattle; isNew?: boolean; onGlowEnd?: () => void }) {
  const t = useT();
  const { lang } = useLang();
  return (
    <a
      href={`/${b.public_id}`}
      onClick={e => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(`/${b.public_id}`);
      }}
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/40${isNew ? " dash-glow" : ""}`}
      onAnimationEnd={onGlowEnd}
    >
      <span className="w-24 shrink-0 flex flex-col leading-tight">
        <span className="text-[10px] uppercase tracking-wide text-zinc-600">{REGION_LABELS[lang][b.region] ?? b.region}</span>
        <span className="text-xs text-zinc-500 tabular-nums">{dateUTC(b.start_time)}</span>
      </span>

      <span className="flex-1 min-w-0 text-center">
        {b.factions.length > 0 ? (
          <FactionHeatmap factions={b.factions} />
        ) : (
          <div className="truncate text-sm text-zinc-200" title={b.cluster ?? ""}>
            {b.cluster ?? t("unknownZone")}
          </div>
        )}
      </span>

      <span className="w-12 shrink-0 text-right text-xs font-semibold text-amber-400/80 tabular-nums">
        {battleFameShort(b.total_fame)}
      </span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-500 tabular-nums">{b.kill_count} {t("killsSuffix")}</span>
      <span className="w-16 shrink-0 text-right text-xs text-zinc-600 tabular-nums">{timeAgo(b.start_time)}</span>
    </a>
  );
}

// Bracket igual à ActivityRow: data de entrada (hora/data) à esquerda, nome
// da guilda centralizado, data de saída ("atual" se ainda está lá) à
// direita — status (kills/mortes/prata) em extenso abaixo do nome.
function GuildHistoryRow({ h }: { h: ZiggsData["guild_history"][number] }) {
  const t = useT();
  const [startTime, startDate] = timeOverDate(h.start);
  const end = h.end ? timeOverDate(h.end) : null;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
      <span className="w-12 shrink-0 flex flex-col items-center leading-tight">
        <span className="text-[11px] font-medium text-zinc-300 tabular-nums">{startTime}</span>
        <span className="text-[10px] text-zinc-600 tabular-nums">{startDate}</span>
      </span>

      <div className="flex-1 min-w-0 text-center">
        {h.guild_is_deleted
          ? <span className="truncate text-sm text-zinc-600 line-through" title={t("guildDeletedTitle")}>{h.guild_name ?? t("noGuild")}</span>
          : h.guild_id
            ? <button onClick={() => navigate(`/guild/${h.guild_id}`)} className="truncate text-sm font-medium text-zinc-200 hover:text-amber-400 transition-colors">{h.guild_name ?? t("noGuild")}</button>
            : <span className="truncate text-sm font-medium text-zinc-200">{h.guild_name ?? t("noGuild")}</span>
        }
        {h.alliance_tag && (
          h.alliance_is_deleted
            ? <div className="text-xs text-zinc-600 line-through" title={t("allianceDeletedTitle")}>[{h.alliance_tag}]</div>
            : h.alliance_id
              ? <div><button onClick={() => navigate(`/alliance/${h.alliance_id}`)} className="text-xs text-amber-400 hover:opacity-75 transition-opacity">[{h.alliance_tag}]</button></div>
              : <div className="text-xs text-amber-400">[{h.alliance_tag}]</div>
        )}
      </div>

      <span className="w-12 shrink-0 flex flex-col items-center leading-tight">
        {end ? (
          <>
            <span className="text-[11px] font-medium text-zinc-300 tabular-nums">{end[0]}</span>
            <span className="text-[10px] text-zinc-600 tabular-nums">{end[1]}</span>
          </>
        ) : (
          <span className="text-[11px] font-medium text-emerald-400">{t("currentLabel")}</span>
        )}
      </span>
    </div>
  );
}

function SkeletonBox({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-zinc-800/80 ${className ?? ""}`} />;
}

// Estrutura da página já montada enquanto a única chamada à API ainda não
// voltou — sensação de progresso em vez de só um "carregando" parado.
function ProfileSkeleton() {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex flex-wrap items-center gap-4">
          <SkeletonBox className="h-16 w-16 rounded-full" />
          <div className="min-w-0 flex-1 space-y-2">
            <SkeletonBox className="h-5 w-40" />
            <SkeletonBox className="h-3 w-28" />
            <SkeletonBox className="h-3 w-48" />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(90px,1fr))] gap-2">
          {Array.from({ length: 5 }).map((_, i) => <SkeletonBox key={i} className="h-12" />)}
        </div>
      </div>

      <div className="flex gap-1 border-b border-zinc-800 pb-0">
        {[t("tabActivity"), t("battles"), t("tabGuildHistory")].map(label => (
          <span key={label} className="border-b-2 border-transparent px-4 py-2 text-sm font-medium text-zinc-700">{label}</span>
        ))}
      </div>

      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => <SkeletonBox key={i} className="h-[52px]" />)}
      </div>
    </div>
  );
}

// Paginação — mesmo visual/lógica de BattlePage.tsx e BattleTracker.tsx
// (janela de até 5 números de página centrada na atual).
const PROFILE_PAGE_SIZE = 10;

function Pagination({ page, totalPages, onPage }: { page: number; totalPages: number; onPage: (p: number) => void }) {
  const pageNums = useMemo(() => {
    const start = Math.max(1, Math.min(page - 2, totalPages - 4));
    const to = Math.min(totalPages, start + 4);
    return Array.from({ length: to - start + 1 }, (_, i) => start + i);
  }, [page, totalPages]);

  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-end gap-1 pt-2 text-xs">
      <button disabled={page === 1} onClick={() => onPage(1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">«</button>
      <button disabled={page === 1} onClick={() => onPage(page - 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">‹</button>
      {pageNums.map(n => (
        <button
          key={n}
          onClick={() => onPage(n)}
          className={`px-2 py-1 rounded ${n === page ? "bg-amber-500/10 text-amber-300 border border-amber-500" : "text-zinc-400 hover:text-zinc-200"}`}
        >
          {n}
        </button>
      ))}
      <button disabled={page === totalPages} onClick={() => onPage(page + 1)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">›</button>
      <button disabled={page === totalPages} onClick={() => onPage(totalPages)} className="px-2 py-1 rounded disabled:opacity-30 text-zinc-400 hover:text-zinc-200">»</button>
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────── */

const PROFILE_REFRESH_MS = 60_000;
const STAGGER_MS = 350;

export default function PlayerProfilePage({ region, name, onBack }: { region: string; name: string; onBack: () => void }) {
  const t = useT();
  const { lang } = useLang();
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeTab, setActiveTab] = useState<"activity" | "battles" | "history">("activity");
  const [activityPage, setActivityPage] = useState(1);
  const [battlesPage, setBattlesPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);

  // live update: glow no que mudou
  const prevStatsRef = useRef<{ killFame: number; deathFame: number; silver: number } | null>(null);
  const [glowingStats, setGlowingStats] = useState<Set<string>>(new Set());
  const knownActivityRef = useRef<Set<string>>(new Set());
  const [newActivityIds, setNewActivityIds] = useState<Set<string>>(new Set());
  const knownBattlesRef = useRef<Set<string>>(new Set());
  const [newBattleIds, setNewBattleIds] = useState<Set<string>>(new Set());
  const staggerTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  function load(silent: boolean) {
    if (silent) setRefreshing(true); else setLoading(true);
    setError(null);

    const TRANSIENT = new Set([502, 503, 504]);
    const attempt = (delay: number) => {
      fetch(`${API}/players/by-name/${region}/${encodeURIComponent(name)}`)
        .then(async res => {
          if (TRANSIENT.has(res.status)) {
            // API do Albion instável — aguarda e retenta silenciosamente
            setTimeout(() => attempt(Math.min(delay * 2, 30_000)), delay);
            return null;
          }
          if (!res.ok) {
            const body = await res.json().catch(() => null);
            throw new Error(body?.detail ?? `HTTP ${res.status}`);
          }
          return res.json();
        })
        .then((data: PlayerProfile | null) => {
          if (data === null) return; // retry agendado, não processa
          resolve(data);
        })
        .catch(e => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => { /* loading liberado em resolve */ });
    };

    let resolved = false;
    const resolve = (data: PlayerProfile) => {
      if (resolved) return;
      resolved = true;
      if (silent) setRefreshing(false); else setLoading(false);
      const z = data._ziggs;

      if (silent && prevStatsRef.current) {
        const changed = new Set<string>();
        if (data.KillFame !== prevStatsRef.current.killFame) { changed.add("killFame"); changed.add("kd"); }
        if (data.DeathFame !== prevStatsRef.current.deathFame) { changed.add("deathFame"); changed.add("kd"); }
        if (z.silver_dropped !== prevStatsRef.current.silver) changed.add("silver");
        if (changed.size) setGlowingStats(prev => new Set([...prev, ...changed]));

        const allActivity = [
          ...z.kills.map(ev => ({ id: ev.event_id })),
          ...z.deaths.map(ev => ({ id: ev.event_id })),
        ];
        const newActivity = allActivity.filter(ev => !knownActivityRef.current.has(ev.id));
        if (newActivity.length) {
          staggerTimersRef.current.forEach(clearTimeout);
          staggerTimersRef.current = [];
          newActivity.forEach(({ id }, i) => {
            const t = setTimeout(() => setNewActivityIds(prev => new Set([...prev, id])), i * STAGGER_MS);
            staggerTimersRef.current.push(t);
          });
        }
        allActivity.forEach(({ id }) => knownActivityRef.current.add(id));

        const newBattles = z.battle_history.filter(b => !knownBattlesRef.current.has(b.public_id));
        if (newBattles.length) {
          newBattles.forEach((b, i) => {
            const t = setTimeout(() => setNewBattleIds(prev => new Set([...prev, b.public_id])), i * STAGGER_MS);
            staggerTimersRef.current.push(t);
          });
        }
        z.battle_history.forEach(b => knownBattlesRef.current.add(b.public_id));
      }

      prevStatsRef.current = { killFame: data.KillFame, deathFame: data.DeathFame, silver: z.silver_dropped };

      if (!silent) {
        knownActivityRef.current = new Set([
          ...z.kills.map(ev => ev.event_id),
          ...z.deaths.map(ev => ev.event_id),
        ]);
        knownBattlesRef.current = new Set(z.battle_history.map(b => b.public_id));
        setActiveTab("activity");
        setActivityPage(1);
        setBattlesPage(1);
        setHistoryPage(1);
      }

      setProfile(data);
    };

    attempt(3_000);
  }

  useEffect(() => {
    setProfile(null);
    prevStatsRef.current = null;
    knownActivityRef.current = new Set();
    knownBattlesRef.current = new Set();
    staggerTimersRef.current.forEach(clearTimeout);
    staggerTimersRef.current = [];
    setGlowingStats(new Set());
    setNewActivityIds(new Set());
    setNewBattleIds(new Set());
    load(false);
    const timer = setInterval(() => load(true), PROFILE_REFRESH_MS);
    return () => {
      clearInterval(timer);
      staggerTimersRef.current.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region, name]);

  const ratio = profile
    ? profile.DeathFame > 0 ? (profile.KillFame / profile.DeathFame).toFixed(2) : "∞"
    : null;

  const stats = profile?.LifetimeStatistics;
  const gathering = stats?.Gathering;
  const z = profile?._ziggs;
  const activity: Activity[] = z
    ? [
        ...z.kills.map(ev => ({ ...ev, kind: "kill" as const })),
        ...z.deaths.map(ev => ({ ...ev, kind: "death" as const })),
      ].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    : [];

  const activityTotalPages = Math.max(1, Math.ceil(activity.length / PROFILE_PAGE_SIZE));
  const activityPageClamped = Math.min(activityPage, activityTotalPages);
  const activityItems = activity.slice((activityPageClamped - 1) * PROFILE_PAGE_SIZE, activityPageClamped * PROFILE_PAGE_SIZE);

  const battlesTotalPages = Math.max(1, Math.ceil((z?.battle_history.length ?? 0) / PROFILE_PAGE_SIZE));
  const battlesPageClamped = Math.min(battlesPage, battlesTotalPages);
  const battlesItems = (z?.battle_history ?? []).slice((battlesPageClamped - 1) * PROFILE_PAGE_SIZE, battlesPageClamped * PROFILE_PAGE_SIZE);

  const historyTotalPages = Math.max(1, Math.ceil((z?.guild_history.length ?? 0) / PROFILE_PAGE_SIZE));
  const historyPageClamped = Math.min(historyPage, historyTotalPages);
  const historyItems = (z?.guild_history ?? []).slice((historyPageClamped - 1) * PROFILE_PAGE_SIZE, historyPageClamped * PROFILE_PAGE_SIZE);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <button onClick={onBack} className="mb-4 text-sm text-zinc-400 hover:text-zinc-200">← {t("back")}</button>

      {loading && <ProfileSkeleton />}
      {error && !loading && (
        <p className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400">{error}</p>
      )}

      {profile && z && !loading && (
        <div className="space-y-4">
          {/* Header */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="flex flex-wrap items-center gap-4">
              <PlayerAvatar avatar={profile.Avatar} name={profile.Name} size={64} />
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <h2 className="text-xl font-bold text-zinc-100">{profile.Name}</h2>
                  {profile._is_deleted && (
                    <span className="rounded border border-red-900/60 bg-red-950/40 px-2 py-0.5 text-[10px] text-red-400 uppercase tracking-wide">{t("deletedBadge")}</span>
                  )}
                  <span className="self-center rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
                    {REGION_LABELS[lang][z.region] ?? z.region}
                  </span>
                </div>
                <div className="mt-0.5 flex items-baseline gap-1.5 text-sm text-zinc-400">
                  {(profile.AllianceTag || profile.AllianceName) && (() => {
                    const label = profile.AllianceTag || profile.AllianceName;
                    return profile.AllianceId
                      ? <button onClick={() => navigate(`/alliance/${profile.AllianceId}`)} className="font-medium text-zinc-300 hover:text-amber-400 transition-colors">[{label}]</button>
                      : <span className="font-medium text-zinc-300">[{label}]</span>;
                  })()}
                  {profile.GuildName ? (
                    profile.GuildId
                      ? <button onClick={() => navigate(`/guild/${profile.GuildId}`)} className="font-medium text-zinc-300 hover:text-amber-400 transition-colors">{profile.GuildName}</button>
                      : <span className="font-medium text-zinc-300">{profile.GuildName}</span>
                  ) : (
                    <span className="text-zinc-600">{t("noGuild")}</span>
                  )}
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-zinc-600">
                  <button
                    onClick={() => load(true)}
                    disabled={refreshing}
                    title={t("forceRefreshTitle")}
                    className="text-zinc-500 hover:text-amber-400 disabled:opacity-40 transition-colors"
                  >
                    <span className={refreshing ? "inline-block animate-spin" : "inline-block"}>⟳</span>
                  </button>
                  {t("updatedAgoPrefix")} {timeAgo(z.last_seen_at)} {t("agoSinceSuffix")} {dateUTC(z.first_seen_at)}
                </div>
              </div>
              <TopWeaponsWidget weapons={z.top_weapons} />
            </div>

            {/* Fama */}
            <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(90px,1fr))] gap-2">
              <FameRow label="Kill Fame" value={profile.KillFame}
                isGlowing={glowingStats.has("killFame")}
                onGlowEnd={() => setGlowingStats(p => { const n = new Set(p); n.delete("killFame"); return n; })} />
              <FameRow label="Death Fame" value={profile.DeathFame}
                isGlowing={glowingStats.has("deathFame")}
                onGlowEnd={() => setGlowingStats(p => { const n = new Set(p); n.delete("deathFame"); return n; })} />
              <div className="flex flex-col items-center justify-center gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wide text-zinc-500">K/D</span>
                <span
                  className={`text-sm font-bold text-zinc-100${glowingStats.has("kd") ? " dash-text-glow" : ""}`}
                  onAnimationEnd={() => setGlowingStats(p => { const n = new Set(p); n.delete("kd"); return n; })}
                >{ratio}</span>
              </div>
              <FameRow label={t("silverDroppedLabel")} value={z.silver_dropped}
                isGlowing={glowingStats.has("silver")}
                onGlowEnd={() => setGlowingStats(p => { const n = new Set(p); n.delete("silver"); return n; })} />
              {!!stats?.PvE?.Total && <FameRow label="PvE Fame" value={stats.PvE.Total} />}
            </div>

            {/* Coleta por recurso — contagem aproximada, não fama */}
            {gathering && !!gathering.All?.Total && (
              <div className="relative mt-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
                <i
                  className="ti ti-info-circle absolute right-2 top-2 text-zinc-600"
                  title={t("gatheringEstimateTooltip")}
                />
                <div className="grid grid-cols-[repeat(auto-fit,minmax(90px,1fr))] gap-2">
                  {([[t("resourceWood"), gathering.Wood], [t("resourceHide"), gathering.Hide], [t("resourceOre"), gathering.Ore],
                    [t("resourceRock"), gathering.Rock], [t("resourceFiber"), gathering.Fiber]] as const).map(([label, g]) =>
                    g?.Total ? <ResourceCountRow key={label} label={label} fame={g.Total} /> : null
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Abas */}
          <div className="flex gap-1 border-b border-zinc-800 pb-0">
            {(["activity", "battles", "history"] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab ? "border-amber-500 text-amber-300" : "border-transparent text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {tab === "activity" ? `${t("tabActivity")} (${activity.length})` :
                  tab === "battles" ? `${t("battles")} (${z.battle_history.length})` : t("tabGuildHistory")}
              </button>
            ))}
          </div>

          <div className="space-y-2">
            {activeTab === "activity" && (
              activity.length === 0
                ? <p className="py-8 text-center text-sm text-zinc-600">{t("noActivityYet")}</p>
                : <>
                  {activityItems.map((ev, i) => (
                    <ActivityRow
                      key={i} ev={ev} profileName={profile.Name}
                      profileGuild={profile.GuildName ?? null} region={z.region}
                      isNew={newActivityIds.has(ev.event_id)}
                      onGlowEnd={() => setNewActivityIds(p => { const n = new Set(p); n.delete(ev.event_id); return n; })}
                    />
                  ))}
                  <Pagination page={activityPageClamped} totalPages={activityTotalPages} onPage={setActivityPage} />
                </>
            )}
            {activeTab === "battles" && (
              z.battle_history.length === 0
                ? <p className="py-8 text-center text-sm text-zinc-600">{t("noProfileBattlesYet")}</p>
                : <>
                  {battlesItems.map(b => (
                    <BattleRow
                      key={b.public_id} b={b}
                      isNew={newBattleIds.has(b.public_id)}
                      onGlowEnd={() => setNewBattleIds(p => { const n = new Set(p); n.delete(b.public_id); return n; })}
                    />
                  ))}
                  <Pagination page={battlesPageClamped} totalPages={battlesTotalPages} onPage={setBattlesPage} />
                </>
            )}
            {activeTab === "history" && (
              z.guild_history.length === 0
                ? <p className="py-8 text-center text-sm text-zinc-600">{t("noGuildHistoryYet")}</p>
                : <>
                  {historyItems.map((h, i) => <GuildHistoryRow key={i} h={h} />)}
                  <Pagination page={historyPageClamped} totalPages={historyTotalPages} onPage={setHistoryPage} />
                </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
