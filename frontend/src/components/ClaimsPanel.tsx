import { useEffect, useRef, useState } from "react";
import { navigate } from "../router";
import { useLang, useT, type Lang } from "../i18n";
import { CHALLENGE_ITEM_NAMES } from "../data/challenge-items";
import { itemRenderUrl } from "../data/albion-items";
import { api, imgRetry, PROFILE_THEMES, type CropRect, type MyProfile, type ProfileTheme } from "../api";
import { normSearch } from "../lib/search";
import CropModal from "./CropModal";

const THEME_COLORS: Record<ProfileTheme, string> = {
  gold: "#f5b942", blue: "#3b82f6", green: "#22c55e",
  red: "#ef4444", purple: "#a855f7", teal: "#14b8a6",
};

const REGION_PREFIX: Record<string, string> = { americas: "am", europe: "eu", asia: "as" };
const REGION_LABEL: Record<string, string> = { americas: "AM", europe: "EU", asia: "AS" };

interface ChallItem { slot: string; item_id: string; quantity: number }
interface Claim {
  id: number; albion_player_id: string; albion_player_name: string;
  region: string; challenge: ChallItem[]; status: "pending" | "verified" | "expired";
  created_at: string; verified_at: string | null;
}

const CLAIM_EXPIRY_MS = 24 * 3600 * 1000;
const CLAIM_COOLDOWN_MS = 30 * 24 * 3600 * 1000;

function hoursLeft(createdAt: string): number {
  const deadline = new Date(createdAt).getTime() + CLAIM_EXPIRY_MS;
  return Math.max(0, Math.ceil((deadline - Date.now()) / 3600_000));
}

function cooldownUntil(createdAt: string): Date {
  return new Date(new Date(createdAt).getTime() + CLAIM_EXPIRY_MS + CLAIM_COOLDOWN_MS);
}
interface Registered {
  id: number; albion_player_id: string; albion_player_name: string;
  region: string; registered_at: string;
  is_main?: boolean; // opcional até o backend (workstream D) expor
}

// Limites espelhados no backend (fonte da verdade é lá — trust boundary);
// aqui é só feedback rápido antes do upload. Ver docs/PLANO-PERFIL-V2.md.
const IMG_LIMITS = {
  avatar: { mb: 25, minW: 128, minH: 128, aspect: 1, round: true },
  banner: { mb: 100, minW: 320, minH: 100, aspect: 16 / 5, round: false },
} as const;
type ImgKind = keyof typeof IMG_LIMITS;

function itemName(itemId: string, lang: "pt" | "en" | "es"): string {
  return CHALLENGE_ITEM_NAMES[itemId]?.[lang] ?? itemId;
}

// Render do item + nome completo (sem truncar) + quantidade embaixo + copiar nome.
// `spoiler`: cobre o item (é tratado como senha) até o usuário clicar pra revelar —
// evita vazamento em screen-share.
function ChallengeItem({ item, lang, spoiler = false }: { item: ChallItem; lang: Lang; spoiler?: boolean }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [revealed, setRevealed] = useState(!spoiler);
  const name = itemName(item.item_id, lang);

  function copy() {
    navigator.clipboard.writeText(name).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  if (!revealed) {
    return (
      <button onClick={() => setRevealed(true)} title={t("revealItemHint")}
        className="flex w-full items-center gap-2.5 rounded bg-zinc-800/60 px-2.5 py-2 text-left hover:bg-zinc-800">
        <span className="flex h-11 w-12 shrink-0 items-center justify-center">
          <i className="ti ti-eye-off text-zinc-500" style={{ fontSize: 20 }} />
        </span>
        <span className="flex-1 text-xs font-medium text-zinc-500">{t("revealItemHint")}</span>
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2.5 rounded bg-zinc-800/60 px-2.5 py-2">
      <div className="relative h-11 w-11 shrink-0">
        <img src={itemRenderUrl(item.item_id)} alt={name}
          className="h-11 w-11 object-contain" loading="lazy" onError={imgRetry()} />
        <span className="absolute bottom-1 right-1 text-[9px] font-bold leading-none text-amber-300 tabular-nums"
          style={{ textShadow: "0 0 2px #000, 0 0 3px #000" }}>×{item.quantity}</span>
      </div>
      <span className="flex-1 text-xs font-medium leading-snug text-zinc-200 break-words">{name}</span>
      <button onClick={copy} title={t("copyItemName")}
        className="shrink-0 p-1 text-zinc-500 hover:text-zinc-300">
        <i className={`ti ${copied ? "ti-check" : "ti-copy"}`} style={{ fontSize: 14 }} />
      </button>
    </div>
  );
}

// Só desbloqueado com personagem verificado (RegisteredCharacter) — o
// /register do bot não conta, é só filiação de guilda sem prova de posse.
function ProfileCustomize({ onBack, playerIds }: { onBack: () => void; playerIds: string[] }) {
  const t = useT();
  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [pendingCrop, setPendingCrop] = useState<{ kind: ImgKind; file: File; url: string } | null>(null);
  const mutating = useRef(false);
  const mutationGeneration = useRef(0);
  const blocked = !!profile?.blocked_until && new Date(profile.blocked_until).getTime() > Date.now();
  const pending = new Set(profile?.pending_kinds ?? []);

  useEffect(() => { api.getMyProfile().then(setProfile).catch(() => {}); }, []);
  useEffect(() => {
    if (!profile?.pending_kinds.length) return;
    const timer = window.setInterval(() => {
      if (mutating.current) return;
      const generation = mutationGeneration.current;
      api.getMyProfile().then(next => {
        if (mutating.current || generation !== mutationGeneration.current) return;
        setProfile(next);
        window.dispatchEvent(new CustomEvent("ziggs:profile-updated", { detail: { profile: next, playerIds } }));
      }).catch(() => {});
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [profile?.pending_kinds.length, playerIds.join(",")]);

  async function apply(action: () => Promise<MyProfile>) {
    mutating.current = true;
    mutationGeneration.current += 1;
    setBusy(true); setErr("");
    try {
      const next = await action();
      setProfile(next);
      // PlayerProfilePage aberta atrás escuta e aplica sem reload — só nos
      // perfis dos personagens do próprio usuário (playerIds).
      window.dispatchEvent(new CustomEvent("ziggs:profile-updated", { detail: { profile: next, playerIds } }));
    }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { mutating.current = false; setBusy(false); }
  }

  // Valida tamanho/dimensão e abre o modal de crop. Dimensões via <img> em
  // objectURL — funciona pra PNG/JPEG/WebP/GIF sem decodificar nada à mão.
  function pickFile(kind: ImgKind, f: File) {
    setErr("");
    const lim = IMG_LIMITS[kind];
    if (f.size > lim.mb * 1024 * 1024) {
      setErr(t("imgTooBig").replace("{mb}", String(lim.mb)));
      return;
    }
    const url = URL.createObjectURL(f);
    const probe = new Image();
    probe.onload = () => {
      if (probe.naturalWidth < lim.minW || probe.naturalHeight < lim.minH) {
        URL.revokeObjectURL(url);
        setErr(t("imgTooSmall").replace("{min}", `${lim.minW}×${lim.minH}px`));
        return;
      }
      setPendingCrop({ kind, file: f, url });
    };
    probe.onerror = () => { URL.revokeObjectURL(url); setErr(t("imgInvalid")); };
    probe.src = url;
  }

  function confirmCrop(rect: CropRect) {
    if (!pendingCrop) return;
    const { kind, file, url } = pendingCrop;
    setPendingCrop(null);
    URL.revokeObjectURL(url);
    apply(() => kind === "avatar" ? api.uploadProfileAvatar(file, rect) : api.uploadProfileBanner(file, rect));
  }

  return (
    <>
      <button className="user-menu-back" onClick={onBack}>
        <i className="ti ti-arrow-left" /> {t("customizeProfile")}
      </button>
      <div className="user-menu-divider" />
      {!profile ? (
        <div className="px-3 py-4 text-center text-xs text-zinc-600">{t("loading")}</div>
      ) : (
        <div className="px-3 py-2 space-y-3">
          {blocked && profile.blocked_until && (
            <p className="rounded border border-red-900/60 bg-red-950/30 px-2 py-1.5 text-[10px] text-red-300">
              {t("profileMediaBlocked").replace("{date}", new Date(profile.blocked_until).toLocaleDateString())}
            </p>
          )}
          <div>
            <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">{t("profileTheme")}</p>
            <div className="flex gap-2">
              {PROFILE_THEMES.map(th => (
                <button key={th} disabled={busy} title={th} onClick={() => apply(() => api.setProfileTheme(th))}
                  className={`h-6 w-6 rounded-full border-2 ${profile.theme === th ? "border-zinc-200" : "border-transparent"}`}
                  style={{ background: THEME_COLORS[th] }} />
              ))}
            </div>
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">{t("profileAvatar")}</p>
            <div className="flex items-center gap-2">
              {profile.avatar_url && <img src={profile.avatar_url} alt="" className="h-10 w-10 rounded-full object-cover" />}
              <label className={`flex-1 rounded-lg bg-zinc-800 px-3 py-1.5 text-center text-xs text-zinc-300 ${blocked || pending.has("avatar") ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-zinc-700"}`}>
                {pending.has("avatar") ? t("profileMediaPending") : t("uploadImage")}
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" disabled={busy || blocked || pending.has("avatar")}
                  onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) pickFile("avatar", f); }} />
              </label>
              {(profile.avatar_url || pending.has("avatar")) && (
                <button disabled={busy} onClick={() => apply(api.removeProfileAvatar)} className="text-zinc-600 hover:text-red-400 text-xs">✕</button>
              )}
            </div>
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">{t("profileBanner")}</p>
            {profile.banner_url && <img src={profile.banner_url} alt="" className="mb-1.5 h-12 w-full rounded object-cover" />}
            <div className="flex items-center gap-2">
              <label className={`flex-1 rounded-lg bg-zinc-800 px-3 py-1.5 text-center text-xs text-zinc-300 ${blocked || pending.has("banner") ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-zinc-700"}`}>
                {pending.has("banner") ? t("profileMediaPending") : t("uploadImage")}
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" disabled={busy || blocked || pending.has("banner")}
                  onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) pickFile("banner", f); }} />
              </label>
              {(profile.banner_url || pending.has("banner")) && (
                <button disabled={busy} onClick={() => apply(api.removeProfileBanner)} className="text-zinc-600 hover:text-red-400 text-xs">✕</button>
              )}
            </div>
          </div>

          {err && <p className="text-[10px] text-red-400">{err}</p>}
        </div>
      )}

      {pendingCrop && (
        <CropModal
          url={pendingCrop.url}
          aspect={IMG_LIMITS[pendingCrop.kind].aspect}
          round={IMG_LIMITS[pendingCrop.kind].round}
          onCancel={() => { URL.revokeObjectURL(pendingCrop.url); setPendingCrop(null); }}
          onConfirm={confirmCrop}
        />
      )}
    </>
  );
}

export default function ClaimsPanel({ onBack }: { onBack: () => void }) {
  const { lang } = useLang();
  const t = useT();
  const [view, setView] = useState<"list" | "new" | "customize">("list");
  const [claims, setClaims] = useState<Claim[]>([]);
  const [registered, setRegistered] = useState<Registered[]>([]);
  const [loading, setLoading] = useState(true);

  // form state
  const [name, setName] = useState("");
  const [region, setRegion] = useState("americas");
  const [searching, setSearching] = useState(false);
  const [found, setFound] = useState<{ id: string; name: string } | null>(null);
  const [searchErr, setSearchErr] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [newClaim, setNewClaim] = useState<Claim | null>(null);

  useEffect(() => { loadMy(); }, []);

  async function loadMy() {
    setLoading(true);
    try {
      const r = await fetch("/claims/my", { credentials: "include" });
      if (r.ok) {
        const d = await r.json();
        setClaims(d.claims);
        setRegistered(d.registered);
      }
    } finally { setLoading(false); }
  }

  async function searchPlayer() {
    setSearchErr(""); setFound(null);
    if (name.trim().length < 2) return;
    setSearching(true);
    try {
      const API = import.meta.env.DEV ? "http://localhost:8000" : "";
      const r = await fetch(`${API}/players/search?q=${encodeURIComponent(name.trim())}&region=${region}`);
      if (!r.ok) throw new Error("Erro na busca");
      const d = await r.json();
      // ponytail: só normalização (espaços + leet), SEM edit-distance — claim
      // é num personagem específico, fuzzy casaria o personagem errado.
      const nq = normSearch(name.trim());
      const match = (d.players ?? []).find((p: { Name: string; Id: string }) =>
        normSearch(p.Name) === nq
      );
      if (match) setFound({ id: match.Id, name: match.Name });
      else setSearchErr(t("charSearchError"));
    } catch { setSearchErr(t("searchErrorRetry")); }
    finally { setSearching(false); }
  }

  async function submitClaim() {
    if (!found) return;
    setSubmitting(true);
    try {
      const r = await fetch("/claims/character", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ albion_player_id: found.id, albion_player_name: found.name, region }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        throw new Error(body?.detail || "");
      }
      const claim = await r.json();
      setNewClaim(claim);
      await loadMy();
    } catch (e) {
      setSearchErr((e instanceof Error && e.message) || t("claimCreateError"));
    }
    finally { setSubmitting(false); }
  }

  async function setMain(id: number) {
    const r = await fetch(`/claims/main/${id}`, { method: "PUT", credentials: "include" });
    if (r.ok) {
      const d = await r.json(); // mesmo shape do GET /claims/my
      setClaims(d.claims);
      setRegistered(d.registered);
    }
  }

  const pendingClaims = claims.filter(c => c.status === "pending");
  const verifiedClaims = claims.filter(c => c.status === "verified");
  const expiredClaims = claims.filter(c => c.status === "expired" && cooldownUntil(c.created_at).getTime() > Date.now());

  if (view === "customize") {
    return <ProfileCustomize onBack={() => setView("list")} playerIds={registered.map(r => r.albion_player_id)} />;
  }

  if (view === "new") {
    return (
      <>
        <button className="user-menu-back" onClick={() => { setView("list"); setNewClaim(null); setFound(null); setName(""); setSearchErr(""); }}>
          <i className="ti ti-arrow-left" /> {t("registerCharacter")}
        </button>
        <div className="user-menu-divider" />

        {newClaim ? (
          <div className="px-3 py-2 space-y-3">
            <p className="text-xs text-zinc-400 leading-relaxed">
              {t("claimCreatedFor")} <span className="font-semibold text-zinc-200">{newClaim.albion_player_name}</span>.
              {" "}{t("claimCreatedInstructions")}
            </p>
            <div className="space-y-1.5">
              {newClaim.challenge.map((item, i) => (
                <ChallengeItem key={i} item={item} lang={lang} spoiler={i >= newClaim.challenge.length - 3} />
              ))}
            </div>
            <p className="text-[10px] text-zinc-600 leading-relaxed">
              {t("claimAutoVerify")}
            </p>
            <p className="text-[10px] font-medium text-amber-500/80">
              {t("claimExpiresIn")} {hoursLeft(newClaim.created_at)}h
            </p>
            <button className="w-full rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700"
              onClick={() => { setView("list"); setNewClaim(null); setFound(null); setName(""); }}>
              {t("viewMyClaims")}
            </button>
          </div>
        ) : (
          <div className="px-3 py-2 space-y-3">
            <div className="flex gap-2">
              <input value={name} onChange={e => setName(e.target.value)}
                onKeyDown={e => e.key === "Enter" && searchPlayer()}
                placeholder={t("characterNamePlaceholder")}
                className="flex-1 rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-zinc-500" />
              <select value={region} onChange={e => setRegion(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-xs text-zinc-300 outline-none">
                <option value="americas">AM</option>
                <option value="europe">EU</option>
                <option value="asia">AS</option>
              </select>
            </div>

            {!found ? (
              <button onClick={searchPlayer} disabled={searching || name.trim().length < 2}
                className="w-full rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700 disabled:opacity-40">
                {searching ? t("searching") : t("searchBtn")}
              </button>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-2 rounded bg-zinc-800/60 px-2.5 py-2">
                  <i className="ti ti-user text-zinc-500 text-sm" />
                  <span className="flex-1 text-xs font-semibold text-zinc-200">{found.name}</span>
                  <span className="text-[10px] text-zinc-600">{REGION_LABEL[region]}</span>
                  <button onClick={() => setFound(null)} className="text-zinc-600 hover:text-zinc-400 text-xs">✕</button>
                </div>
                <button onClick={submitClaim} disabled={submitting}
                  className="w-full rounded-lg border border-amber-700/50 bg-amber-950/30 px-3 py-2 text-xs font-medium text-amber-300 hover:bg-amber-950/50 disabled:opacity-40">
                  {submitting ? t("creating") : t("createClaim")}
                </button>
              </div>
            )}

            {searchErr && <p className="text-[10px] text-red-400">{searchErr}</p>}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <button className="user-menu-back" onClick={onBack}>
        <i className="ti ti-arrow-left" /> {t("myCharacters")}
      </button>
      <div className="user-menu-divider" />

      {loading ? (
        <div className="px-3 py-4 text-center text-xs text-zinc-600">{t("loading")}</div>
      ) : (
        <>
          {registered.length > 0 && (
            <div className="px-1 py-1">
              {registered.map(r => (
                <div key={r.id} className="flex items-center">
                  <button
                    onClick={() => navigate(`/${REGION_PREFIX[r.region] ?? "am"}/${encodeURIComponent(r.albion_player_name)}`)}
                    className="topbar-dropdown-item min-w-0 flex-1">
                    <i className="ti ti-user-check text-emerald-500" style={{ fontSize: 14 }} />
                    <span className="flex-1 text-left truncate">{r.albion_player_name}</span>
                    <span className="text-[10px] text-zinc-600">{REGION_LABEL[r.region] ?? r.region}</span>
                  </button>
                  {registered.length > 1 && (
                    <button onClick={() => setMain(r.id)} title={t("setAsMain")} disabled={r.is_main}
                      className={`shrink-0 px-2 py-1 ${r.is_main ? "text-amber-400" : "text-zinc-600 hover:text-amber-400"}`}>
                      <i className={`ti ${r.is_main ? "ti-star-filled" : "ti-star"}`} style={{ fontSize: 13 }} />
                    </button>
                  )}
                </div>
              ))}
              <button className="topbar-dropdown-item" onClick={() => setView("customize")}>
                <i className="ti ti-palette" style={{ fontSize: 14 }} />
                {t("customizeProfile")}
              </button>
            </div>
          )}

          {pendingClaims.length > 0 && (
            <>
              {registered.length > 0 && <div className="user-menu-divider" />}
              <div className="px-3 py-1.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">{t("pendingClaims")}</p>
                {pendingClaims.map(c => (
                  <div key={c.id} className="mb-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-2 space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-zinc-300 truncate">{c.albion_player_name}</span>
                      <span className="text-[10px] text-zinc-600 shrink-0">{REGION_LABEL[c.region]}</span>
                    </div>
                    <div className="space-y-1.5">
                      {c.challenge.map((item, i) => (
                        <ChallengeItem key={i} item={item} lang={lang} spoiler={i >= c.challenge.length - 3} />
                      ))}
                    </div>
                    <p className="text-[9px] text-zinc-700">{t("dieWithExactBag")}</p>
                    <p className="text-[9px] font-medium text-amber-500/80">{t("claimExpiresIn")} {hoursLeft(c.created_at)}h</p>
                  </div>
                ))}
              </div>
            </>
          )}

          {expiredClaims.length > 0 && (
            <>
              {(registered.length > 0 || pendingClaims.length > 0) && <div className="user-menu-divider" />}
              <div className="px-3 py-1.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">{t("claimsCooldown")}</p>
                {expiredClaims.map(c => (
                  <div key={c.id} className="mb-2 rounded-lg border border-zinc-800 bg-zinc-900/40 px-2.5 py-2 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-zinc-400 truncate">{c.albion_player_name}</span>
                      <span className="text-[10px] text-zinc-600 shrink-0">{REGION_LABEL[c.region]}</span>
                    </div>
                    <p className="text-[10px] text-zinc-600">{t("claimCooldownUntil")} {cooldownUntil(c.created_at).toLocaleDateString()}</p>
                  </div>
                ))}
              </div>
            </>
          )}

          {registered.length === 0 && pendingClaims.length === 0 && verifiedClaims.length === 0 && expiredClaims.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-zinc-600">{t("noCharactersRegistered")}</div>
          )}

          {registered.length > 0 || pendingClaims.length > 0 || expiredClaims.length > 0 ? <div className="user-menu-divider" /> : null}

          <button className="topbar-dropdown-item" onClick={() => setView("new")}>
            <i className="ti ti-plus" style={{ fontSize: 14 }} />
            {t("registerCharacter")}
          </button>
        </>
      )}
    </>
  );
}
