import { useEffect, useState } from "react";
import {
  api,
  type BuildSuggestion,
  type CatalogRole,
  type GameRoleDetail,
  type RegearItem,
  type WeaponOut,
} from "../api";

const BUILD_FIELDS: { key: keyof GameRoleDetail; label: string; multiline?: boolean }[] = [
  { key: "offhand",   label: "Off-hand" },
  { key: "helmet",    label: "Capacete" },
  { key: "armor",     label: "Armadura" },
  { key: "boots",     label: "Botas" },
  { key: "cape",      label: "Capa" },
  { key: "food",      label: "Comida" },
  { key: "abilities", label: "Habilidades", multiline: true },
  { key: "play_style",label: "Estilo de jogo", multiline: true },
  { key: "obs",       label: "Observações", multiline: true },
];

// Slots que podem ter item_id para cálculo de regear
const REGEAR_SLOTS: { slot: string; label: string }[] = [
  { slot: "offhand", label: "Off-hand" },
  { slot: "helmet",  label: "Capacete" },
  { slot: "armor",   label: "Armadura" },
  { slot: "boots",   label: "Botas" },
  { slot: "cape",    label: "Capa" },
  { slot: "food",    label: "Comida" },
];

const QUALITY_LABELS: Record<number, string> = {
  1: "Normal", 2: "Bom", 3: "Excepcional", 4: "Excelente", 5: "Obra-prima",
};

const FUNCTION_COLORS: Record<string, string> = {
  support: "#6fa3db",
  healer:  "#6bbf73",
  tank:    "#e07a7a",
  dps:     "#e0a87a",
  pierce:  "#06b6d4",
};

function FnTag({ fn }: { fn: string | null }) {
  if (!fn) return null;
  const color = FUNCTION_COLORS[fn] ?? "var(--muted)";
  return (
    <span style={{
      fontSize: 11, padding: "1px 6px", borderRadius: 10,
      background: color + "33", color, border: `1px solid ${color}66`,
    }}>
      {fn}
    </span>
  );
}

type DraftRole = Omit<GameRoleDetail, "id" | "weapon_name" | "invisible_function"> & {
  id: number | null;
};

function emptyDraft(): DraftRole {
  return {
    id: null, name: "", weapon_id: null,
    offhand: null, helmet: null, armor: null, boots: null,
    cape: null, food: null, abilities: null, play_style: null, obs: null,
    build_items: [],
    color: null, q_spell: null, w_spell: null, passive_spell: null,
    gear_spells: null,
  };
}

function emptyBuildItem(slot: string): RegearItem {
  return { slot, item_id: "", name: "", quality: 1, quantity: 1 };
}

export default function RolesPage() {
  const [roles, setRoles]     = useState<CatalogRole[]>([]);
  const [weapons, setWeapons] = useState<WeaponOut[]>([]);
  const [sel, setSel]         = useState<number | null>(null);  // role id or -1 (novo)
  const [draft, setDraft]     = useState<DraftRole>(emptyDraft());
  const [suggestion, setSuggestion] = useState<BuildSuggestion | null>(null);
  const [loadingSug, setLoadingSug] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState<string | null>(null);

  function reload() {
    api.listRoles().then(setRoles).catch(() => {});
  }

  useEffect(() => {
    reload();
    api.listWeapons().then(setWeapons).catch(() => {});
  }, []);

  const weaponMap = new Map(weapons.map(w => [w.id, w]));
  const selWeapon = draft.weapon_id ? weaponMap.get(draft.weapon_id) ?? null : null;
  const invisFn   = selWeapon?.invisible_function ?? null;

  // Busca sugestão quando a função invisível muda
  useEffect(() => {
    if (!invisFn) { setSuggestion(null); return; }
    setLoadingSug(true);
    api.suggestBuild(invisFn)
      .then(s => setSuggestion(s.sample_size > 0 ? s : null))
      .catch(() => setSuggestion(null))
      .finally(() => setLoadingSug(false));
  }, [invisFn]);

  async function selectRole(id: number) {
    setSel(id);
    setError(null);
    setSuggestion(null);
    const detail = await api.getRole(id).catch(() => null);
    if (detail) {
      setDraft({
        id: detail.id,
        name: detail.name,
        weapon_id: detail.weapon_id,
        offhand: detail.offhand,
        helmet: detail.helmet,
        armor: detail.armor,
        boots: detail.boots,
        cape: detail.cape,
        food: detail.food,
        abilities: detail.abilities,
        play_style: detail.play_style,
        obs: detail.obs,
        build_items: detail.build_items ?? [],
        color: detail.color ?? null,
        q_spell: detail.q_spell ?? null,
        w_spell: detail.w_spell ?? null,
        passive_spell: detail.passive_spell ?? null,
        gear_spells: detail.gear_spells ?? null,
      });
    }
  }

  function setBuildItem(slot: string, field: keyof RegearItem, value: string | number) {
    setDraft(d => {
      const items = [...(d.build_items ?? [])];
      const idx = items.findIndex(bi => bi.slot === slot);
      if (idx >= 0) {
        items[idx] = { ...items[idx], [field]: value };
        if (field === "name" && !items[idx].item_id) {
          // keep it
        }
        if (field === "item_id" && !value) {
          // remove if both item_id and name are empty
          if (!items[idx].name) { items.splice(idx, 1); }
          else { items[idx] = { ...items[idx], item_id: "" }; }
        }
      } else if (value) {
        items.push({ ...emptyBuildItem(slot), [field]: value });
      }
      return { ...d, build_items: items };
    });
  }

  function newRole() {
    setSel(-1);
    setDraft(emptyDraft());
    setSuggestion(null);
    setError(null);
  }

  function set<K extends keyof DraftRole>(key: K, val: DraftRole[K]) {
    setDraft(d => ({ ...d, [key]: val || null }));
  }

  function applySuggestion(field: string, value: string) {
    setDraft(d => ({ ...d, [field]: value }));
  }

  function applyAllSuggestions() {
    if (!suggestion) return;
    setDraft(d => {
      const next = { ...d };
      for (const [f, s] of Object.entries(suggestion.fields)) {
        if (!(next as Record<string, unknown>)[f]) {
          (next as Record<string, unknown>)[f] = s.value;
        }
      }
      return next;
    });
  }

  async function save() {
    if (!draft.name.trim()) { setError("Nome obrigatório"); return; }
    setSaving(true); setError(null);
    try {
      const payload = {
        name: draft.name.trim(),
        weapon_id: draft.weapon_id ?? undefined,
        offhand: draft.offhand ?? undefined,
        helmet: draft.helmet ?? undefined,
        armor: draft.armor ?? undefined,
        boots: draft.boots ?? undefined,
        cape: draft.cape ?? undefined,
        food: draft.food ?? undefined,
        abilities: draft.abilities ?? undefined,
        play_style: draft.play_style ?? undefined,
        obs: draft.obs ?? undefined,
        build_items: (draft.build_items ?? []).filter(bi => bi.item_id.trim()),
      };
      if (draft.id && draft.id > 0) {
        await api.updateRole(draft.id, payload);
      } else {
        const created = await api.createRole(payload as Parameters<typeof api.createRole>[0]);
        setSel(created.id);
        setDraft(d => ({ ...d, id: created.id }));
      }
      reload();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function del() {
    if (!draft.id || draft.id < 0) return;
    if (!confirm(`Remover a função "${draft.name}"? Slots que usam ela ficarão vazios.`)) return;
    await api.deleteRole(draft.id).catch(() => {});
    setSel(null);
    setDraft(emptyDraft());
    reload();
  }

  return (
    <div className="container">
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>

        {/* Lista de funções */}
        <div style={{ flex: "0 0 260px" }}>
          <div className="card">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>Funções</span>
              <button className="btn primary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={newRole}>
                <i className="ti ti-plus" aria-hidden /> Nova
              </button>
            </div>
            {roles.length === 0 && <p className="muted" style={{ fontSize: 13 }}>Nenhuma função cadastrada.</p>}
            {roles.map(r => (
              <button
                key={r.id}
                onClick={() => selectRole(r.id)}
                style={{
                  display: "flex", alignItems: "center", gap: 8, width: "100%",
                  padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "none",
                  background: sel === r.id ? "var(--surface-2)" : "transparent",
                  cursor: "pointer", textAlign: "left", marginBottom: 2,
                }}
              >
                <i className="ti ti-shield" style={{ color: "var(--muted)", fontSize: 13 }} aria-hidden />
                <span style={{ flex: 1, fontSize: 13 }}>{r.name}</span>
                <FnTag fn={r.invisible_function} />
              </button>
            ))}
          </div>
        </div>

        {/* Editor */}
        {sel !== null && (
          <div style={{ flex: "1 1 420px" }}>
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>
                  {draft.id && draft.id > 0 ? "Editar função" : "Nova função"}
                </span>
                {invisFn && <FnTag fn={invisFn} />}
                {draft.id && draft.id > 0 && (
                  <button className="btn" style={{ marginLeft: "auto", fontSize: 12, color: "#e07a7a" }} onClick={del}>
                    <i className="ti ti-trash" aria-hidden /> Remover
                  </button>
                )}
              </div>

              {/* Nome + Arma */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
                <div>
                  <label className="cs-field-lbl">Nome da função *</label>
                  <input
                    className="input" style={{ width: "100%" }}
                    placeholder="ex.: Locus Suporte"
                    value={draft.name}
                    onChange={e => set("name", e.target.value as DraftRole["name"])}
                  />
                </div>
                <div>
                  <label className="cs-field-lbl">Arma</label>
                  <select
                    className="cs-select" style={{ width: "100%" }}
                    value={draft.weapon_id ?? ""}
                    onChange={e => set("weapon_id", (e.target.value ? Number(e.target.value) : null) as DraftRole["weapon_id"])}
                  >
                    <option value="">— sem arma —</option>
                    {weapons.map(w => (
                      <option key={w.id} value={w.id}>
                        {w.name}{w.invisible_function ? ` [${w.invisible_function}]` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Painel de sugestão */}
              {(loadingSug || suggestion) && (
                <SuggestionPanel
                  suggestion={suggestion}
                  loading={loadingSug}
                  draft={draft}
                  onApply={applySuggestion}
                  onApplyAll={applyAllSuggestions}
                />
              )}

              {/* Campos de build */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {BUILD_FIELDS.map(f => {
                  const sug = suggestion?.fields[f.key];
                  const d = draft as Record<string, unknown>;
                  const currentVal = (d[f.key] as string | null) ?? "";
                  return (
                    <div
                      key={f.key}
                      style={f.multiline ? { gridColumn: "1 / -1" } : undefined}
                    >
                      <label className="cs-field-lbl" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        {f.label}
                        {sug && !currentVal && (
                          <span
                            style={{ fontSize: 10, color: "var(--info)", cursor: "pointer", fontWeight: 400 }}
                            onClick={() => applySuggestion(f.key, sug.value)}
                            title={`Sugestão: ${sug.value} (${sug.votes}/${sug.total})`}
                          >
                            💡 {sug.votes}/{sug.total}
                          </span>
                        )}
                      </label>
                      {f.multiline ? (
                        <textarea
                          className="input"
                          style={{ width: "100%", minHeight: 68, resize: "vertical" }}
                          placeholder={sug && !currentVal ? `Sugestão: ${sug.value}` : ""}
                          value={currentVal}
                          onChange={e => setDraft(dr => ({ ...dr, [f.key]: e.target.value || null }))}
                        />
                      ) : (
                        <input
                          className="input"
                          style={{ width: "100%" }}
                          placeholder={sug && !currentVal ? `Sugestão: ${sug.value}` : ""}
                          value={currentVal}
                          onChange={e => setDraft(dr => ({ ...dr, [f.key]: e.target.value || null }))}
                        />
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Itens de Regear — item_ids para cálculo de preço */}
              <RegearItemsPanel
                buildItems={draft.build_items ?? []}
                onSet={setBuildItem}
              />

              {error && <p style={{ color: "#e07a7a", fontSize: 12, marginTop: 10 }}>{error}</p>}

              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button
                  className="btn primary"
                  onClick={save}
                  disabled={saving}
                >
                  <i className="ti ti-device-floppy" aria-hidden />
                  {saving ? " Salvando…" : " Salvar"}
                </button>
                <button className="btn" onClick={() => { setSel(null); setDraft(emptyDraft()); }}>
                  Cancelar
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Painel de Itens de Regear ─────────────────────────────────────────────────

function RegearItemsPanel({
  buildItems,
  onSet,
}: {
  buildItems: RegearItem[];
  onSet: (slot: string, field: keyof RegearItem, value: string | number) => void;
}) {
  const [open, setOpen] = useState(false);
  const itemMap = new Map(buildItems.map(bi => [bi.slot, bi]));
  const filledCount = REGEAR_SLOTS.filter(s => itemMap.get(s.slot)?.item_id).length;

  return (
    <div style={{
      borderRadius: "var(--radius-sm)", border: "1px solid var(--border)",
      marginTop: 14, overflow: "hidden",
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 8, width: "100%",
          padding: "9px 14px", background: "var(--surface-2)", border: "none",
          cursor: "pointer", textAlign: "left",
        }}
      >
        <i className={`ti ti-${open ? "chevron-down" : "chevron-right"}`} aria-hidden />
        <span style={{ fontSize: 13, fontWeight: 500 }}>Itens de Regear</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
          {filledCount} / {REGEAR_SLOTS.length} slots preenchidos
        </span>
      </button>
      {open && (
        <div style={{ padding: "12px 14px" }}>
          <p style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12, marginTop: 0 }}>
            Informe o ID canônico do Albion Online para calcular o valor de regear automaticamente.
            Ex.: <code style={{ fontSize: 10 }}>T8_HEAD_CLOTH_MORGANA@4</code>
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {REGEAR_SLOTS.map(({ slot, label }) => {
              const item = itemMap.get(slot);
              return (
                <div key={slot}>
                  <label className="cs-field-lbl" style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {label}
                    {item?.item_id && (
                      <span style={{ fontSize: 9, color: "var(--success)", fontWeight: 600 }}>✓ ID</span>
                    )}
                  </label>
                  <input
                    className="input"
                    style={{ width: "100%", fontSize: 11, fontFamily: "monospace" }}
                    placeholder={`ex: T8_HEAD_CLOTH_MORGANA@4`}
                    value={item?.item_id ?? ""}
                    onChange={e => onSet(slot, "item_id", e.target.value.trim())}
                  />
                  <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                    <input
                      className="input"
                      style={{ flex: 1, fontSize: 11 }}
                      placeholder="Nome amigável (opcional)"
                      value={item?.name ?? ""}
                      onChange={e => onSet(slot, "name", e.target.value)}
                    />
                    <select
                      className="cs-select"
                      style={{ width: 90, fontSize: 11 }}
                      value={item?.quality ?? 1}
                      onChange={e => onSet(slot, "quality", Number(e.target.value))}
                    >
                      {Object.entries(QUALITY_LABELS).map(([q, lbl]) => (
                        <option key={q} value={q}>{lbl}</option>
                      ))}
                    </select>
                    <input
                      className="input"
                      style={{ width: 48, fontSize: 11, textAlign: "center" }}
                      type="number"
                      min={1}
                      title="Quantidade"
                      value={item?.quantity ?? 1}
                      onChange={e => onSet(slot, "quantity", Math.max(1, Number(e.target.value)))}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Painel de sugestão ────────────────────────────────────────────────────────

function SuggestionPanel({
  suggestion, loading, draft, onApply, onApplyAll,
}: {
  suggestion: BuildSuggestion | null;
  loading: boolean;
  draft: DraftRole;
  onApply: (field: string, value: string) => void;
  onApplyAll: () => void;
}) {
  const d = draft as Record<string, unknown>;
  const hasEmpty = suggestion
    ? Object.entries(suggestion.fields).some(([k]) => !d[k])
    : false;

  return (
    <div style={{
      background: "var(--surface-2)", borderRadius: "var(--radius-sm)",
      padding: "10px 14px", marginBottom: 14,
      border: "1px solid var(--border)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: loading ? 0 : 8 }}>
        <i className="ti ti-bulb" style={{ color: "var(--info)" }} aria-hidden />
        <span style={{ fontSize: 13, fontWeight: 500 }}>
          {loading
            ? "Buscando sugestões…"
            : `Sugestão — ${suggestion!.sample_size} ${suggestion!.sample_size !== 1 ? "funções similares" : "função similar"}`}
        </span>
        {!loading && hasEmpty && (
          <button
            className="btn primary"
            style={{ marginLeft: "auto", fontSize: 11, padding: "3px 8px" }}
            onClick={onApplyAll}
          >
            Aplicar vazios
          </button>
        )}
      </div>
      {!loading && suggestion && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {Object.entries(suggestion.fields).map(([field, s]) => {
            const fieldLabel = BUILD_FIELDS.find(f => f.key === field)?.label ?? field;
            const alreadyFilled = !!d[field];
            return (
              <div
                key={field}
                onClick={() => !alreadyFilled && onApply(field, s.value)}
                style={{
                  fontSize: 11, padding: "3px 8px", borderRadius: 6,
                  background: alreadyFilled ? "var(--surface)" : "var(--info)22",
                  border: `1px solid ${alreadyFilled ? "var(--border)" : "var(--info)66"}`,
                  color: alreadyFilled ? "var(--hint)" : "var(--info)",
                  cursor: alreadyFilled ? "default" : "pointer",
                  opacity: alreadyFilled ? 0.6 : 1,
                }}
                title={`${fieldLabel}: ${s.value} — ${s.votes} de ${s.total} funções`}
              >
                <strong>{fieldLabel}</strong>{" "}
                {s.value.length > 20 ? s.value.slice(0, 20) + "…" : s.value}
                {" "}
                <span style={{ opacity: 0.7 }}>({s.votes}/{s.total})</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
