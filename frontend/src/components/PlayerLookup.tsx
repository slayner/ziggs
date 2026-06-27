import { useRef, useState } from "react";
import { navigate } from "../router";
import { fetchRetry } from "../api";

// Em dev o Vite proxy não encaminha /players corretamente, então chamamos a API
// diretamente. Em produção o frontend é servido do mesmo servidor (URL relativa).
const API = import.meta.env.DEV ? "http://localhost:8000" : "";

// Nomes não são únicos entre Americas/Europe/Asia (servidores separados) —
// busca nos 3 em paralelo e mostra de qual região é cada resultado, já que
// o usuário não necessariamente sabe de antemão. Ver router.ts (mesmos
// prefixos usados no link de perfil, /am/Nome etc).
const REGIONS: { key: string; label: string; prefix: string }[] = [
  { key: "americas", label: "Americas", prefix: "am" },
  { key: "europe", label: "Europe", prefix: "eu" },
  { key: "asia", label: "Asia", prefix: "as" },
];

interface PlayerSearchResult {
  Id: string;
  Name: string;
  GuildName?: string;
  AllianceName?: string;
  AllianceTag?: string;
  Avatar?: string;
  KillFame: number;
  DeathFame: number;
}

interface RegionResults { key: string; label: string; prefix: string; players: PlayerSearchResult[] }

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

// `Avatar` é cosmético (ex: "AVATAR_03"), não item de equipamento — não
// renderiza via /render/item/. Servido de frontend/public/avatars/ (cópia
// 1:1 dos nomes de arquivo da API; o CDN não-oficial está atrás de um
// bot-challenge da Cloudflare, não dá pra puxar de lá).
// Os 5 avatares base do Albion (sempre existem em public/avatars/) — usados
// quando o avatar real do jogador não tem arquivo correspondente. Escolha
// determinística pelo nome, pra não trocar de avatar a cada render.
const DEFAULT_AVATARS = ["AVATAR_01", "AVATAR_02", "AVATAR_03", "AVATAR_04", "AVATAR_05"];
function defaultAvatarFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return DEFAULT_AVATARS[Math.abs(h) % DEFAULT_AVATARS.length];
}

function PlayerAvatar({ avatar, name, size = 40 }: { avatar?: string | null; name: string; size?: number }) {
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

export default function PlayerLookup() {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<RegionResults[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function doSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    setSearching(true);
    setResults(null);
    setSearchError(null);
    try {
      const byRegion = await Promise.all(REGIONS.map(async r => {
        const res = await fetchRetry(`${API}/players/search?q=${encodeURIComponent(q)}&region=${r.key}`);
        if (!res.ok) return { ...r, players: [] as PlayerSearchResult[] };
        const data = await res.json();
        return { ...r, players: (data.players ?? []) as PlayerSearchResult[] };
      }));
      setResults(byRegion.filter(r => r.players.length > 0));
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  }

  function openProfile(prefix: string, name: string) {
    navigate(`/${prefix}/${encodeURIComponent(name)}`);
  }

  const totalResults = results?.reduce((n, r) => n + r.players.length, 0) ?? 0;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6">
      <form onSubmit={doSearch} className="mb-6 flex gap-2">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Buscar jogador por nick…"
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm text-zinc-100 outline-none focus:border-amber-500 placeholder:text-zinc-600"
        />
        <button
          type="submit"
          disabled={searching || query.trim().length < 2}
          className="rounded-lg border border-zinc-700 bg-zinc-800 px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
        >
          {searching ? "…" : "Buscar"}
        </button>
      </form>

      {searchError && <p className="mb-4 rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400">{searchError}</p>}

      {searching && <div className="py-16 text-center text-zinc-500">Buscando nas 3 regiões…</div>}

      {!searching && results !== null && (
        totalResults === 0 ? (
          <p className="py-12 text-center text-zinc-500">Nenhum jogador encontrado para "{query}".</p>
        ) : (
          <div className="space-y-6">
            {results.map(r => (
              <div key={r.key}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  {r.label} <span className="text-zinc-600">· {r.players.length}</span>
                </p>
                <div className="space-y-2">
                  {r.players.map(p => (
                    <button
                      key={p.Id}
                      onClick={() => openProfile(r.prefix, p.Name)}
                      className="w-full rounded-xl border border-zinc-800 bg-zinc-900/50 px-4 py-3 text-left hover:border-zinc-600 hover:bg-zinc-900 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <PlayerAvatar avatar={p.Avatar} name={p.Name} size={40} />
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="font-semibold text-zinc-100">{p.Name}</span>
                            {p.AllianceTag && <span className="text-xs text-amber-400">[{p.AllianceTag}]</span>}
                          </div>
                          <div className="text-xs text-zinc-500 truncate">
                            {p.GuildName ?? "Sem guilda"}
                            {p.AllianceName && <span className="ml-1">· {p.AllianceName}</span>}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className={`text-xs font-semibold ${fameColor(p.KillFame)}`}>{fameShort(p.KillFame)} kill</div>
                          <div className="text-[10px] text-zinc-600">{fameShort(p.DeathFame)} death</div>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {!searching && results === null && (
        <div className="py-20 text-center">
          <div className="text-4xl mb-4">⚔️</div>
          <p className="text-zinc-400 font-medium">Busque qualquer jogador de Albion</p>
          <p className="mt-1 text-sm text-zinc-600">Perfil público · Stats · Kills · Histórico de guilds</p>
        </div>
      )}
    </div>
  );
}
