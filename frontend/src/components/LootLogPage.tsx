import { useEffect, useState } from "react";
import { api, setGuild, type EventSummary, type LootLogSubmission } from "../api";
import { useT } from "../i18n";

interface Props { guildId: string; eventId: number; events: EventSummary[] }

// Colunas exibidas (semicolon-separated), na ordem que o user quer ver.
const FMT_COLS = [
  "timestamp_utc", "looted_by__alliance", "looted_by__guild", "looted_by__name",
  "item_id", "item_name", "quantity",
];

/** Parse robusto do timestamp do lootlogger ( tolera frac de 7 dígitos e tz
 * variável) → epoch ms UTC, ou null. Não reformat o string p/ filtrar — só
 * converte p/ comparar com a janela do evento. */
function parseTs(s: string): number | null {
  if (!s) return null;
  const m = s.match(
    /^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/
  );
  if (!m) return null;
  let iso = m[1].replace(" ", "T");
  if (m[2]) iso += "." + m[2].slice(0, 3).padEnd(3, "0");
  if (!m[3] || m[3] === "Z") iso += "Z";
  else if (m[3].length === 5) iso += m[3].slice(0, 3) + ":" + m[3].slice(3);
  else iso += m[3];
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d.getTime();
}

/** 2026-06-15T22:13:58.3060786Z → 15-06-26_22:13 (DD-MM-YY_HH:MM UTC). */
function fmtTs(s: string): string {
  const ms = parseTs(s);
  if (ms == null) return s;
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}-${p(d.getUTCMonth() + 1)}-` +
         `${String(d.getUTCFullYear()).slice(-2)}_` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Reformata o .csv do lootlogger: só as colunas em FMT_COLS, timestamp →
 * DD-MM-YY_HH:MM. Devolve só as linhas de dados (sem header) — o header da
 * lista combinada é emitido uma vez pelo caller. Primeira linha não-vazia =
 * header. Filtros: (1) descarta mortes (item_id vazio); (2) só loot da
 * aliança/guilda da casa (filterCol+filterVal, case-insensitive; se filterVal
 * vazio não filtra por guild); (3) só logs DURANTE o evento — timestamp dentro
 * de [winStart, winEnd] (janela started-5min…ended+15min, igual ao backend). Se
 * winStart null (sem scheduled/started), não filtra por tempo. */
function formatRows(
  raw: string, filterCol: string | null, filterVal: string,
  winStart: number | null, winEnd: number | null,
): string[] {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  let hi = 0;
  while (hi < lines.length && !lines[hi].trim()) hi++;
  if (hi >= lines.length) return [];
  const header = lines[hi].split(";").map(c => c.trim());
  const idx: Record<string, number> = {};
  header.forEach((h, i) => { idx[h] = i; });
  const want = filterVal.trim().toLowerCase();
  const out: string[] = [];
  for (let i = hi + 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    const cols = line.split(";");
    const get = (name: string) => {
      const k = idx[name];
      return k !== undefined && k < cols.length ? cols[k].trim() : "";
    };
    if (!get("item_id")) continue;          // linha de morte → descarta
    if (filterCol && want) {                 // só loot da aliança/guilda da casa
      if (get(filterCol).toLowerCase() !== want) continue;
    }
    if (winStart != null) {                   // só logs durante o evento
      const ts = parseTs(get("timestamp_utc"));
      if (ts == null || ts < winStart || ts > winEnd!) continue;
    }
    const row = FMT_COLS.map(c => c === "timestamp_utc" ? fmtTs(get(c)) : get(c));
    out.push(row.join(";"));
  }
  return out;
}

/** Corpo <pre> monospace sem coloração, sem header (nem nomes dos loggers,
 * nem botão copy) — SEM borda própria, igual à caixa de colar do baú (sub-aba
 * Log). Altura fixa (30vh) pra ficar igual às duas. */
function CodeBlock({ text }: { text: string }) {
  return (
    <pre style={{
      margin: 0, padding: 12, overflow: "auto",
      height: "30vh",
      fontSize: 13, lineHeight: 1.5, whiteSpace: "pre",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      color: "var(--text)",
    }}>
      {text}
    </pre>
  );
}

export default function LootLogPage({ guildId, eventId, events }: Props) {
  const t = useT();
  const [subs, setSubs] = useState<LootLogSubmission[] | null>(null);
  const [busy, setBusy] = useState(false);
  // Filtro de aliança/guilda da casa: looted_by__alliance == alliance, ou
  // looted_by__guild == albion_guild_name se a guilda não tem aliança.
  const [filterCol, setFilterCol] = useState<string | null>(null);
  const [filterVal, setFilterVal] = useState<string>("");

  useEffect(() => { setGuild(guildId); }, [guildId]);

  // Dados da guilda (aliança/nome Albion) p/ filtrar o loot da casa.
  useEffect(() => {
    let alive = true;
    api.guildInfo(guildId)
      .then(g => {
        if (!alive) return;
        const alliance = (g.albion_alliance_name || "").trim();
        const guildName = (g.albion_guild_name || "").trim();
        if (alliance) { setFilterCol("looted_by__alliance"); setFilterVal(alliance); }
        else if (guildName) { setFilterCol("looted_by__guild"); setFilterVal(guildName); }
        else { setFilterCol(null); setFilterVal(""); }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [guildId]);

  // Guard de "alive": este componente é montado/desmontado toda vez que a
  // sub-aba troca (ReconcileSection alterna entre 2 tipos de componente
  // diferentes, não é um simples toggle de props) — sem o guard, um fetch da
  // MONTAGEM ANTERIOR ainda em voo podia resolver depois da nova montagem já
  // ter disparado o seu próprio fetch, e o setBusy(false)/setSubs(...) do
  // fetch velho (ou do StrictMode, que roda o effect 2x em dev) sobrescrevia
  // o estado da instância nova no meio do carregamento — Lootlog ficava preso
  // em "Loading…" ao alternar rápido entre as abas.
  useEffect(() => {
    let alive = true;
    (async () => {
      setBusy(true);
      try {
        const list = await api.listLootLog(eventId);
        if (alive) setSubs(list.submissions);
      } catch {
        if (alive) setSubs([]);
      } finally {
        if (alive) setBusy(false);
      }
    })();
    return () => { alive = false; };
  }, [eventId]);

  // Dedup por logger (reenvio sobrescreve no backend, mas por segurança).
  const seen = new Set<number | string>();
  const loggers = (subs ?? []).filter(s => s.raw_text).filter(s => {
    const k = s.submitter_user_id ?? s.submitter_name ?? s.id;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  // Janela do evento (igual ao backend _event_window): started_at ou
  // scheduled_at -5min … ended_at ou agora +15min. null se não há anchor.
  const ev = events.find(e => e.id === eventId);
  const startSrc = ev?.started_at || ev?.scheduled_at;
  let winStart: number | null = null;
  let winEnd: number | null = null;
  if (startSrc) {
    const s = new Date(startSrc).getTime();
    if (!isNaN(s)) {
      winStart = s - 5 * 60 * 1000;
      const e = ev?.ended_at ? new Date(ev.ended_at).getTime() : Date.now();
      winEnd = (isNaN(e) ? Date.now() : e) + 15 * 60 * 1000;
    }
  }

  // Logs combinadas: UM header + todas as linhas de todos os loggers, sem
  // separador. Lista única e sólida — só logs durante o evento.
  const combined = [
    FMT_COLS.join(";"),
    ...loggers.flatMap(s => formatRows(s.raw_text ?? "", filterCol, filterVal, winStart, winEnd)),
  ].join("\n");

  return (
    <div>
      {busy && <p className="hint" style={{ padding: 14 }}>{t("loading")}</p>}

      {subs && (
        loggers.length === 0
          ? <p className="hint" style={{ padding: 14 }}>{t("llEmpty")}</p>
          : <CodeBlock text={combined} />
      )}
    </div>
  );
}