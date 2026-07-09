import { useState } from "react";
import { api, type ChestUploadEntry } from "../api";
import { useT } from "../i18n";
import { ITEM_BY_ID } from "../data/albion-items";

interface Props {
  eventId: number;
  onReload: () => void;
}

// Mapa reverso nome(lowercase) → item_id. Prefere inglês (baú do jogo é EN),
// cai em PT. Construído 1x (lazy).
let _nameMap: Map<string, string> | null = null;
function nameMap(): Map<string, string> {
  if (_nameMap) return _nameMap;
  const m = new Map<string, string>();
  for (const it of ITEM_BY_ID.values()) {
    const en = it.nameEn?.trim().toLowerCase();
    if (en && !m.has(en)) m.set(en, it.id);
  }
  for (const it of ITEM_BY_ID.values()) {
    const pt = it.name.trim().toLowerCase();
    if (pt && !m.has(pt)) m.set(pt, it.id);
  }
  _nameMap = m;
  return m;
}

const TS_RE = /^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}/;
const QTY_RE = /^(\d{1,5})x?$/i;

/** Parser tolerante do log do baú (formato do jogo: tab ou ; separado,
 * colunas variáveis). Identifica item por nome (EN/PT), qty numérica, player.
 * Linhas não reconhecidas vão pra `skipped`. */
function parseChestLog(text: string): { entries: ChestUploadEntry[]; skipped: string[] } {
  const m = nameMap();
  const entries: ChestUploadEntry[] = [];
  const skipped: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("//")) continue;
    let fields = line.split(/\t|;|\s{2,}/).map(s => s.trim()).filter(Boolean);
    if (fields.length === 1) fields = line.split(/\s{2,}/).map(s => s.trim()).filter(Boolean);
    if (fields.length < 2) { skipped.push(line); continue; }

    let qty = 1, qtyIdx = -1, tsIdx = -1;
    fields.forEach((f, i) => {
      if (qtyIdx < 0 && QTY_RE.test(f)) { qty = parseInt(QTY_RE.exec(f)![1], 10); qtyIdx = i; }
      if (tsIdx < 0 && TS_RE.test(f)) tsIdx = i;
    });
    const rest = fields.filter((_, i) => i !== qtyIdx && i !== tsIdx);

    // acha o item: campo que casa com nome conhecido (case-insensitive).
    let itemField: string | null = null, itemId: string | null = null;
    for (const f of rest) {
      const hit = m.get(f.toLowerCase());
      if (hit) { itemField = f; itemId = hit; break; }
    }
    if (!itemId) { skipped.push(line); continue; }

    // player = outro campo que não é o item.
    const player = rest.find(f => f !== itemField) ?? null;
    entries.push({
      // Trunca p/ caber nas colunas do backend (String(255) item_name,
      // String(64) deposited_by_name) — texto colado pode exceder.
      item_type: itemId, item_name: itemField!.slice(0, 255), quantity: qty,
      deposited_by_name: player ? player.slice(0, 64) : null,
    });
  }
  // agrega mesmos itens do mesmo depositor (linhas duplicadas viram 1 entrada).
  const agg = new Map<string, ChestUploadEntry>();
  for (const e of entries) {
    const k = `${e.item_type}|${e.deposited_by_name ?? ""}`;
    const cur = agg.get(k);
    if (cur) cur.quantity += e.quantity;
    else agg.set(k, { ...e });
  }
  return { entries: [...agg.values()], skipped };
}

/** Corpo da sub-aba "Log" — SEM borda/header próprios (o quadrante e o header
 * de abas são do ReconcileSection, que já ocupa esse lugar). Processo
 * automático: colar já dispara o parse+upload, sem botão de enviar. Mesma
 * altura fixa do bloco do Lootlog (ver CodeBlock em LootLogPage.tsx) — não
 * redimensionável. */
export default function LootReconcilePage({ eventId, onReload }: Props) {
  const t = useT();
  const [chestText, setChestText] = useState("");
  const [parseInfo, setParseInfo] = useState<{ ok: number; skipped: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Automático: submete assim que o texto é colado, sem botão. `text` vem
  // direto do clipboardData (não do state, que ainda não terminou de
  // atualizar no momento do evento onPaste).
  async function submitChest(text: string) {
    setErr(null);
    const { entries, skipped } = parseChestLog(text);
    setParseInfo({ ok: entries.length, skipped: skipped.length });
    if (entries.length === 0) { setErr(t("recNoItems")); return; }
    try {
      await api.uploadChest(eventId, entries, true);
      onReload();
    } catch (e: any) { setErr(String(e?.message || e)); }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const text = e.clipboardData.getData("text");
    setChestText(text);
    if (text.trim()) submitChest(text);
  }

  return (
    <div>
      <textarea
        value={chestText} onChange={e => setChestText(e.target.value)} onPaste={handlePaste}
        placeholder={t("recChestPlaceholder")}
        style={{
          width: "100%", display: "block", border: "none", outline: "none", resize: "none",
          height: "30vh", padding: 12, background: "var(--surface)", color: "var(--text)",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          fontSize: 13, lineHeight: 1.5,
        }}
      />
      {(parseInfo || err) && (
        <div style={{ padding: "8px 12px", borderTop: "1px solid var(--border)", fontSize: 12 }}>
          {parseInfo && (
            <span>
              {t("recParsed")}: <strong style={{ color: "var(--green)" }}>{parseInfo.ok}</strong>
              {parseInfo.skipped > 0 && (
                <span style={{ color: "var(--gold)" }}> · {t("recSkipped")}: {parseInfo.skipped}</span>
              )}
            </span>
          )}
          {err && <div style={{ color: "var(--gold)", marginTop: parseInfo ? 4 : 0 }}>{err}</div>}
        </div>
      )}
    </div>
  );
}
