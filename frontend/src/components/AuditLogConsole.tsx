import { useEffect, useRef, useState } from "react";
import { api, type AuditLogEntry } from "../api";
import { useT, type TKey } from "../i18n";

function timestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().replace("T", " ").replace(".000Z", "Z");
}

function mergeLogs(current: AuditLogEntry[], incoming: AuditLogEntry[]): AuditLogEntry[] {
  const byId = new Map(current.map(entry => [entry.id, entry]));
  for (const entry of incoming) byId.set(entry.id, entry);
  return [...byId.values()].sort((a, b) => b.id - a.id); // mais novo primeiro
}

// Categoria = prefixo do action antes do ponto (registration.*, economy.*, etc).
type Cat = "registration" | "economy" | "event" | "comp" | "node" | "guild";
const CATS: { id: Cat | "all"; label: TKey }[] = [
  { id: "all",          label: "auditFilterAll" },
  { id: "registration", label: "auditCatRegistration" },
  { id: "economy",      label: "auditCatEconomy" },
  { id: "event",        label: "auditCatEvent" },
  { id: "comp",         label: "auditCatComp" },
  { id: "node",         label: "auditCatNode" },
  { id: "guild",        label: "auditCatGuild" },
];

function entryCat(entry: AuditLogEntry): Cat | null {
  const prefix = entry.action.split(".")[0];
  return (CATS.some(c => c.id === prefix) ? prefix : null) as Cat | null;
}

export default function AuditLogConsole({ guildId, active }: { guildId: string; active: boolean }) {
  const t = useT();
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Cat | "all">("all");
  const entriesRef = useRef(entries);
  entriesRef.current = entries;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.guildAuditLog(guildId)
      .then(data => {
        if (cancelled) return;
        setEntries(data.entries);
        setHasMore(data.has_more);
      })
      .catch(err => { if (!cancelled) setError(String(err.message)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [guildId]);

  useEffect(() => {
    if (!active) return;
    const interval = setInterval(() => {
      const newestId = entriesRef.current[0]?.id; // ordem desc: [0] = mais novo
      if (!newestId) return;
      api.guildAuditLog(guildId, { after_id: newestId })
        .then(data => { if (data.entries.length) setEntries(current => mergeLogs(current, data.entries)); })
        .catch(() => {});
    }, 8000);
    return () => clearInterval(interval);
  }, [active, guildId]);

  function loadOlder() {
    const oldestId = entries[entries.length - 1]?.id; // ordem desc: último = mais antigo
    if (!oldestId || loadingOlder) return;
    setLoadingOlder(true);
    api.guildAuditLog(guildId, { before_id: oldestId })
      .then(data => {
        setEntries(current => mergeLogs(current, data.entries));
        setHasMore(data.has_more);
      })
      .catch(err => setError(String(err.message)))
      .finally(() => setLoadingOlder(false));
  }

  const visible = filter === "all" ? entries : entries.filter(e => entryCat(e) === filter);
  const presentCats = new Set(entries.map(entryCat).filter((c): c is Cat => c !== null));

  return (
    <section className="audit-console">
      <header className="audit-console-head">
        <span><i className="ti ti-terminal-2" aria-hidden="true" /> {t("auditConsole")}</span>
        <small>{visible.length} {t("auditEntries")}</small>
      </header>
      <div className="audit-console-filters">
        {CATS.map(c => {
          if (c.id !== "all" && !presentCats.has(c.id as Cat)) return null;
          return (
            <button
              key={c.id}
              className={"audit-filter-chip" + (filter === c.id ? " active" : "")}
              onClick={() => setFilter(c.id)}
            >
              {t(c.label)}
            </button>
          );
        })}
      </div>
      {error && <p className="audit-console-error">{error}</p>}
      <div className="audit-console-body" aria-live="polite">
        {loading && <p className="hint">{t("auditLoading")}</p>}
        {!loading && visible.length === 0 && <p className="hint">{t("auditEmpty")}</p>}
        {visible.map(entry => (
          <article className="audit-console-entry" key={entry.id}>
            <div className="audit-console-meta">
              <time>{timestamp(entry.created_at)}</time>
              <span>
                <em>{t("auditAction")}</em> <strong>{entry.action}</strong>
              </span>
              <span>
                <em>{t("auditTarget")}</em> {entry.entity}{entry.entity_id ? ` #${entry.entity_id}` : ""}
              </span>
              <span>
                <em>{t("auditActor")}</em> {entry.actor_name ? `${entry.actor_name} (@${entry.actor_id})`
                  : entry.actor_id ? `@${entry.actor_id}` : t("auditSystem")}
              </span>
              <span>
                <em>{t("auditSource")}</em> {entry.source}
              </span>
            </div>
            <pre>{JSON.stringify({ before: entry.before, after: entry.after, note: entry.note }, null, 2)}</pre>
          </article>
        ))}
        {hasMore && (
          <button className="audit-load-more" onClick={loadOlder} disabled={loadingOlder}>
            {loadingOlder ? t("auditLoading") : t("auditLoadOlder")}
          </button>
        )}
      </div>
    </section>
  );
}
