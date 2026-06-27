import { useEffect, useState } from "react";
import { navigate } from "../router";

const REGION_PREFIX: Record<string, string> = { americas: "am", europe: "eu", asia: "as" };
const REGION_LABEL: Record<string, string> = { americas: "AM", europe: "EU", asia: "AS" };

interface ChallItem { slot: string; item_id: string; quantity: number; display_name: string }
interface Claim {
  id: number; albion_player_id: string; albion_player_name: string;
  region: string; challenge: ChallItem[]; status: "pending" | "verified";
  created_at: string; verified_at: string | null;
}
interface Registered {
  id: number; albion_player_id: string; albion_player_name: string;
  region: string; registered_at: string;
}

const SLOT_LABELS: Record<string, string> = {
  Food: "Comida", Potion: "Poção", Head: "Capacete", Armor: "Armadura",
  Shoes: "Botas", MainHand: "Arma Principal", OffHand: "Off-hand", Cape: "Capa",
};

export default function ClaimsPanel({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<"list" | "new">("list");
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
      const match = (d.players ?? []).find((p: { Name: string; Id: string }) =>
        p.Name.toLowerCase() === name.trim().toLowerCase()
      );
      if (match) setFound({ id: match.Id, name: match.Name });
      else setSearchErr("Personagem não encontrado nessa região. Verifique o nome e a região.");
    } catch { setSearchErr("Erro ao buscar. Tente novamente."); }
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
      if (!r.ok) throw new Error("Erro ao criar claim");
      const claim = await r.json();
      setNewClaim(claim);
      await loadMy();
    } catch { setSearchErr("Erro ao criar reivindicação. Tente novamente."); }
    finally { setSubmitting(false); }
  }

  const pendingClaims = claims.filter(c => c.status === "pending");
  const verifiedClaims = claims.filter(c => c.status === "verified");

  if (view === "new") {
    return (
      <>
        <button className="user-menu-back" onClick={() => { setView("list"); setNewClaim(null); setFound(null); setName(""); setSearchErr(""); }}>
          <i className="ti ti-arrow-left" /> Registrar personagem
        </button>
        <div className="user-menu-divider" />

        {newClaim ? (
          <div className="px-3 py-2 space-y-3">
            <p className="text-xs text-zinc-400 leading-relaxed">
              Reivindicação criada para <span className="font-semibold text-zinc-200">{newClaim.albion_player_name}</span>.
              Para verificar, equipe exatamente os itens abaixo e participe de qualquer combate (kill ou morte):
            </p>
            <div className="space-y-1.5">
              {newClaim.challenge.map((item, i) => (
                <div key={i} className="flex items-center gap-2 rounded bg-zinc-800/60 px-2.5 py-1.5">
                  <span className="text-[10px] text-zinc-500 w-14 shrink-0">{SLOT_LABELS[item.slot] ?? item.slot}</span>
                  <span className="flex-1 text-xs text-zinc-200 font-medium truncate">{item.display_name}</span>
                  <span className="text-xs font-bold text-amber-400 tabular-nums">×{item.quantity}</span>
                </div>
              ))}
            </div>
            <p className="text-[10px] text-zinc-600 leading-relaxed">
              A verificação é automática e pode levar até 2 minutos após o combate.
            </p>
            <button className="w-full rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700"
              onClick={() => { setView("list"); setNewClaim(null); setFound(null); setName(""); }}>
              Ver minhas reivindicações
            </button>
          </div>
        ) : (
          <div className="px-3 py-2 space-y-3">
            <div className="flex gap-2">
              <input value={name} onChange={e => setName(e.target.value)}
                onKeyDown={e => e.key === "Enter" && searchPlayer()}
                placeholder="Nome do personagem…"
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
                {searching ? "Buscando…" : "Buscar"}
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
                  {submitting ? "Criando…" : "Criar reivindicação"}
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
        <i className="ti ti-arrow-left" /> Meus personagens
      </button>
      <div className="user-menu-divider" />

      {loading ? (
        <div className="px-3 py-4 text-center text-xs text-zinc-600">Carregando…</div>
      ) : (
        <>
          {registered.length > 0 && (
            <div className="px-1 py-1">
              {registered.map(r => (
                <button key={r.id}
                  onClick={() => navigate(`/${REGION_PREFIX[r.region] ?? "am"}/${encodeURIComponent(r.albion_player_name)}`)}
                  className="topbar-dropdown-item">
                  <i className="ti ti-user-check text-emerald-500" style={{ fontSize: 14 }} />
                  <span className="flex-1 text-left truncate">{r.albion_player_name}</span>
                  <span className="text-[10px] text-zinc-600">{REGION_LABEL[r.region] ?? r.region}</span>
                </button>
              ))}
            </div>
          )}

          {pendingClaims.length > 0 && (
            <>
              {registered.length > 0 && <div className="user-menu-divider" />}
              <div className="px-3 py-1.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-600 mb-1.5">Pendentes</p>
                {pendingClaims.map(c => (
                  <div key={c.id} className="mb-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-2 space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-zinc-300 truncate">{c.albion_player_name}</span>
                      <span className="text-[10px] text-zinc-600 shrink-0">{REGION_LABEL[c.region]}</span>
                    </div>
                    <div className="space-y-1">
                      {c.challenge.map((item, i) => (
                        <div key={i} className="flex items-center gap-1.5 text-[10px]">
                          <span className="text-zinc-600 w-12 shrink-0">{SLOT_LABELS[item.slot] ?? item.slot}</span>
                          <span className="flex-1 text-zinc-400 truncate">{item.display_name}</span>
                          <span className="font-bold text-amber-400 tabular-nums">×{item.quantity}</span>
                        </div>
                      ))}
                    </div>
                    <p className="text-[9px] text-zinc-700">Equipe os itens acima e participe de um combate</p>
                  </div>
                ))}
              </div>
            </>
          )}

          {registered.length === 0 && pendingClaims.length === 0 && verifiedClaims.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-zinc-600">Nenhum personagem registrado</div>
          )}

          {registered.length > 0 || pendingClaims.length > 0 ? <div className="user-menu-divider" /> : null}

          <button className="topbar-dropdown-item" onClick={() => setView("new")}>
            <i className="ti ti-plus" style={{ fontSize: 14 }} />
            Registrar personagem
          </button>
        </>
      )}
    </>
  );
}
