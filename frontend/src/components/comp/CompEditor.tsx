import { useEffect, useRef, useState } from "react";
import {
  api, imgRetry, type GameRoleDetail, type WeaponOut, type WeaponSpell, type Permissions,
} from "../../api";
import { ItemPicker } from "../ItemPicker";
import { is2H, wBase, ITEM_BY_ID } from "../../data/albion-items";
import { useT } from "../../i18n";

import { ColorPicker } from "./ColorPicker";
import { EquipStrip } from "./EquipStrip";
import {
  ALT_CAPABLE, DEFAULT_FN_TYPES, MAX_SLOTS, buildItemsToEquip, emptyRole,
  fnLabel, getFnDef, itemUrl, roleToPayload, safeAltArr, useEquipSlots,
} from "./helpers";
import { SpellPicker } from "./SpellPicker";
import type {
  CompCode, Draft, DraftEquip, DraftParty, DraftRole, DraftSlot, EquipItem, FnTypeDef,
} from "./types";

function readLocalFnTypes(): FnTypeDef[] | null {
  try {
    const saved = JSON.parse(localStorage.getItem("hideout_fn_types") ?? "null");
    if (!saved) return null;
    if (Array.isArray(saved)) return saved as FnTypeDef[];
    return Object.entries(saved as Record<string, { label: string; color: string }>)
      .filter(([k]) => !["dps_melee","dps_ranged","utility"].includes(k))
      .map(([key, v]) => ({ key, label: v.label, color: v.color }));
  } catch { return null; }
}

function isDefaultFnTypes(list: FnTypeDef[]): boolean {
  if (list.length !== DEFAULT_FN_TYPES.length) return false;
  return list.every((t, i) =>
    t.key === DEFAULT_FN_TYPES[i].key && t.label === DEFAULT_FN_TYPES[i].label && t.color === DEFAULT_FN_TYPES[i].color);
}

// Nome de arma sem o prefixo de tier: "6.3 Realmbreaker" → "Realmbreaker",
// "6.3 Cajado de Gelo Elevado" → "Gelo Elevado". Battlemount (noTier, nome
// próprio sem tier) guarda só a 1ª palavra: "Carruagem de Torre de Ouro" →
// "Carruagem". Cajados ainda perdem a palavra "Cajado" + conector (de/da/do):
// "Cajado Sagrado" → "Sagrado", "Cajado da Natureza Elevado" → "Natureza Elevado".
// Usa o catálogo local (ITEM_BY_ID) pra resolver o nome localizado; se não achar,
// deriva do id (uppercase first) — fallback raro.
function weaponDisplayName(weaponId: string): string {
  const item = ITEM_BY_ID.get(weaponId);
  if (item) {
    const n = item.noTier
      ? item.name.split(/\s+/)[0]
      : item.name.replace(/^\d+\.\d+\s+/, "");
    return n.replace(/^Cajado\s+(?:de\s+|da\s+|do\s+)?/, "");
  }
  const base = wBase(weaponId);
  return base.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

// Render grande pra bracket collapsada (igual ao EquipStrip mas com imagens maiores).
function BigRenders({ equip, weaponIs2H }: { equip: DraftEquip; weaponIs2H?: boolean }) {
  const order: { key: keyof DraftEquip; label: string }[] = [
    { key: "weapon",  label: "Weapon" },
    { key: "offhand", label: "Off" },
    { key: "helmet",  label: "Head" },
    { key: "armor",   label: "Armor" },
    { key: "boots",   label: "Boots" },
    { key: "cape",    label: "Cape" },
    { key: "food",    label: "Food" },
    { key: "potion",  label: "Potion" },
  ];
  const items = order
    .filter(o => o.key !== "offhand" || !weaponIs2H)
    .map(o => {
      const main = equip[o.key] as EquipItem | undefined;
      if (!main?.id) return null;
      const alts = safeAltArr((equip as Record<string, unknown>)[`${String(o.key)}_alt`]);
      return { key: o.key, label: o.label, main, alts };
    })
    .filter((x): x is { key: keyof DraftEquip; label: string; main: EquipItem; alts: EquipItem[] } => x !== null);
  if (!items.length) return null;
  return (
    <div className="rc-big-renders">
      {items.map(({ key, main, alts }) => (
        <div key={String(key)} className="rc-big-render-slot">
          <img className="rc-big-render" src={itemUrl(main.id)} alt={main.name} title={main.name}
            onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
          {alts.map((alt, ai) => alt?.id ? (
            <img key={ai} className="rc-big-render rc-big-render-alt" src={itemUrl(alt.id)} alt={alt.name} title={alt.name}
              onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
          ) : null)}
        </div>
      ))}
    </div>
  );
}

export function CompEditor({ initialDraft, initialImportCode, perms, weapons, onBack, onDeleted }: {
  initialDraft: Draft;
  initialEditing?: boolean;
  initialImportCode: CompCode | null;
  perms: Permissions;
  weapons: WeaponOut[];
  onBack: () => void;
  onDeleted?: (id: number) => void;
}) {
  const t = useT();
  const EQUIP_SLOTS = useEquipSlots();
  const [draft,            setDraft]            = useState<Draft | null>(initialDraft);
  const [openCard,         setOpenCard]         = useState<[number, number] | null>(null);
  const [dirty,            setDirty]            = useState(false);
  const [saving,           setSaving]           = useState(false);
  const [saveOk,           setSaveOk]           = useState(false);
  const [error,            setError]            = useState<string | null>(null);
  const [spellCache,       setSpellCache]       = useState<Record<string, WeaponSpell[]>>({});
  const [delConfirm,       setDelConfirm]       = useState<number | null>(null);
  const [fnTypes,          setFnTypes]          = useState<FnTypeDef[]>(() => [...DEFAULT_FN_TYPES]);
  const [showFnPanel,      setShowFnPanel]      = useState(false);
  const [history,          setHistory]          = useState<Draft[]>([]);
  const [fnDropdown,       setFnDropdown]       = useState<[number, number] | null>(null);
  // Wizard "Adicionar Função": { pi } | null — aberto na party pi.
  const [addFnWizard,      setAddFnWizard]      = useState<number | null>(null);
  // Estado do wizard: step (fn | weapon | suggest), selectedFn, selectedWeaponId
  const [wizStep,           setWizStep]           = useState<"fn"|"weapon"|"suggest">("fn");
  const [wizFn,             setWizFn]             = useState<string | null>(null);
  const [wizWeaponId,       setWizWeaponId]       = useState<string>("");
  // Editando nome de role inline: [pi, si] | null
  const [editingName,       setEditingName]       = useState<[number, number] | null>(null);

  useEffect(() => {
    api.getCompFnTypes()
      .then(({ fn_types }) => {
        if (fn_types.length > 0) { setFnTypes(fn_types); return; }
        const local = readLocalFnTypes();
        if (local && perms["comps.manage"] && !isDefaultFnTypes(local)) {
          api.putCompFnTypes(local)
            .then(({ fn_types: saved }) => { setFnTypes(saved); localStorage.removeItem("hideout_fn_types"); })
            .catch(() => {});
        }
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const histCaptured = useRef(false);

  function upd(fn: (d: Draft) => Draft) {
    setDraft(prev => {
      if (!prev) return prev;
      setHistory(h => [...h, prev]);
      return fn(prev);
    });
    setDirty(true); setSaveOk(false); setError(null);
  }
  function updQuiet(fn: (d: Draft) => Draft) {
    setDraft(prev => prev ? fn(prev) : prev);
    setDirty(true); setSaveOk(false); setError(null);
  }
  function captureHistory() {
    if (histCaptured.current) return;
    histCaptured.current = true;
    setDraft(prev => {
      if (!prev) return prev;
      setHistory(h => [...h, prev]);
      return prev;
    });
  }
  function releaseFocus() { histCaptured.current = false; }

  // Filtra parties vazias (sem slots) — usado pra auto-deletar parties que
  // ficaram sem roles. Sempre mantém pelo menos 1 party.
  function pruneEmptyParties(d: Draft): Draft {
    const filtered = d.parties.filter(p => p.slots.length > 0);
    return { ...d, parties: filtered.length ? filtered : [d.parties[0]] };
  }

  function updSlot(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRole(pi: number, si: number, fn: (r: DraftRole) => DraftRole) {
    updSlot(pi, si, s => ({ ...s, role: fn(s.role) }));
  }
  function updSlotQuiet(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    updQuiet(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRoleQuiet(pi: number, si: number, fn: (r: DraftRole) => DraftRole) {
    updSlotQuiet(pi, si, s => ({ ...s, role: fn(s.role) }));
  }
  function undo() {
    if (!history.length) return;
    setDraft(history[history.length - 1]);
    setHistory(h => h.slice(0, -1));
    setDirty(true); setSaveOk(false);
  }
  function saveFnTypes(next: FnTypeDef[]) {
    setFnTypes(next);
    api.putCompFnTypes(next).catch(() => {});
  }

  function deleteFnType(key: string) {
    saveFnTypes(fnTypes.filter(t => t.key !== key));
    if (draft) {
      upd(d => ({
        ...d,
        parties: d.parties.map(p => ({ ...p, slots: p.slots.filter(s => s.fn !== key) })),
      }));
      setOpenCard(null);
    }
  }

  // ── Open / close card + lazy equip load ───────────────────
  async function toggleCard(pi: number, si: number) {
    if (openCard?.[0] === pi && openCard?.[1] === si) {
      setOpenCard(null); return;
    }
    setOpenCard([pi, si]); setFnDropdown(null); setEditingName(null);
    if (!draft) return;
    const role = draft.parties[pi]?.slots[si]?.role;
    if (!role) return;
    if (role.catalog_id !== null && !role.equip_loaded) {
      try {
        const detail: GameRoleDetail = await api.getRole(role.catalog_id);
        const equip = detail.build_items?.length ? buildItemsToEquip(detail.build_items) : {};
        setDraft(prev => {
          if (!prev) return prev;
          return {
            ...prev,
            parties: prev.parties.map((p, i) =>
              i !== pi ? p : {
                ...p,
                slots: p.slots.map((s, j) =>
                  j !== si ? s : {
                    ...s,
                    role: {
                      ...s.role, equip,
                      color: detail.color ?? null,
                      play_style: detail.play_style,
                      abilities:  detail.abilities,
                      obs:        detail.obs,
                      q_spell:       detail.q_spell ?? null,
                      w_spell:       detail.w_spell ?? null,
                      passive_spell: detail.passive_spell ?? null,
                      gear_spells:   detail.gear_spells ?? {},
                      equip_loaded: true,
                    },
                  }
                ),
              }
            ),
          };
        });
        if (equip.weapon?.id) await loadSpells(wBase(equip.weapon.id));
        for (const gk of ["helmet", "armor", "boots"] as const)
          if (equip[gk]?.id) await loadSpells(wBase(equip[gk]!.id));
      } catch {
        setDraft(prev => {
          if (!prev) return prev;
          return {
            ...prev,
            parties: prev.parties.map((p, i) =>
              i !== pi ? p : {
                ...p,
                slots: p.slots.map((s, j) => j !== si ? s : { ...s, role: { ...s.role, equip_loaded: true } }),
              }
            ),
          };
        });
      }
    } else if (role.equip_loaded) {
      if (role.equip.weapon?.id) await loadSpells(wBase(role.equip.weapon.id));
      for (const gk of ["helmet", "armor", "boots"] as const)
        if (role.equip[gk]?.id) await loadSpells(wBase(role.equip[gk]!.id));
    }
  }

  async function ensureCatalogRoleLoaded(catalogId: number) {
    if (!draft) return;
    const already = draft.parties.some(p => p.slots.some(s => s.role.catalog_id === catalogId && s.role.equip_loaded));
    if (already) return;
    const applyToMatching = (fn: (r: DraftRole) => DraftRole) => {
      setDraft(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          parties: prev.parties.map(p => ({
            ...p,
            slots: p.slots.map(s => ({
              ...s,
              role: s.role.catalog_id !== catalogId ? s.role : fn(s.role),
            })),
          })),
        };
      });
    };
    try {
      const detail: GameRoleDetail = await api.getRole(catalogId);
      const equip = detail.build_items?.length ? buildItemsToEquip(detail.build_items) : {};
      applyToMatching(r => ({
        ...r, equip,
        color: detail.color ?? null,
        play_style: detail.play_style,
        abilities: detail.abilities,
        obs: detail.obs,
        q_spell: detail.q_spell ?? null,
        w_spell: detail.w_spell ?? null,
        passive_spell: detail.passive_spell ?? null,
        gear_spells: detail.gear_spells ?? {},
        equip_loaded: true,
      }));
    } catch {
      applyToMatching(r => ({ ...r, equip_loaded: true }));
    }
  }

  function ensurePickableRolesLoaded(roles: DraftRole[]) {
    for (const r of roles) {
      if (r.catalog_id != null && !r.equip_loaded) ensureCatalogRoleLoaded(r.catalog_id);
    }
  }

  async function loadSpells(base: string) {
    if (!base || spellCache[base] !== undefined) return;
    try {
      const spells = await api.weaponSpells(base);
      setSpellCache(prev => ({ ...prev, [base]: spells }));
    } catch {
      setSpellCache(prev => ({ ...prev, [base]: [] }));
    }
  }

  function onWeaponChange(pi: number, si: number, id: string, name: string) {
    const base = id ? wBase(id) : null;
    const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
    updRole(pi, si, r => ({
      ...r,
      equip: { ...r.equip, weapon: id ? { id, name } : undefined },
      fn: dbWeapon?.invisible_function ?? null,
      weapon_db_id: dbWeapon?.id ?? null,
      q_spell: null, w_spell: null, passive_spell: null,
      // Auto-nome: só sobrescreve se o nome atual for vazio ou igual ao nome
      // automático da arma anterior (não sobrescreve nome custom do user).
      // Aceita também o nome cru do catálogo ("6.3 Realmbreaker") — default
      // do formato antigo, pra roles antigas ainda renovarem o nome.
      name: (!r.name || r.name === weaponDisplayName(r.equip.weapon?.id ?? "")
        || r.name === ITEM_BY_ID.get(r.equip.weapon?.id ?? "")?.name)
        ? (id ? weaponDisplayName(id) : r.name) : r.name,
    }));
    if (base && id) loadSpells(base);
  }

  function loadSpellsForEquip(equip: DraftEquip) {
    const weaponId = equip.weapon?.id;
    if (weaponId) loadSpells(wBase(weaponId));
    for (const gk of ["helmet", "armor", "boots"] as const) {
      const gid = equip[gk]?.id;
      if (gid) loadSpells(wBase(gid));
    }
  }

  function applyCompCode(code: CompCode) {
    upd(d => ({
      ...d,
      parties: code.parties.map(p => ({
        name: p.name,
        slots: p.slots.map(s => {
          const r = s.role;
          const weaponId = r.equip.weapon?.id;
          const base = weaponId ? wBase(weaponId) : null;
          const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
          return {
            fn: s.fn,
            role: {
              catalog_id: null, name: r.name, color: null,
              fn: dbWeapon?.invisible_function ?? r.fn,
              weapon_db_id: dbWeapon?.id ?? null,
              equip: r.equip, equip_loaded: true,
              play_style: r.play_style, abilities: r.abilities, obs: null,
              q_spell: r.q_spell, w_spell: r.w_spell, passive_spell: r.passive_spell,
              gear_spells: r.gear_spells,
              potion_qty: r.potion_qty, food_qty: r.food_qty,
            },
          };
        }),
      })),
    }));
    setOpenCard(null);
    for (const p of code.parties) for (const s of p.slots) loadSpellsForEquip(s.role.equip);
  }

  useEffect(() => {
    if (initialImportCode) applyCompCode(initialImportCode);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Party / slot management ───────────────────────────────
  function addParty() {
    upd(d => ({ ...d, parties: [...d.parties, { name: `Party ${d.parties.length + 1}`, slots: [] }] }));
  }
  // Criar slot vazio e abrir wizard "Adicionar Função"
  function startAddFn(pi: number) {
    if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
    setAddFnWizard(pi);
    setWizStep("fn");
    setWizFn(null);
    setWizWeaponId("");
  }
  // Wizard: selecionou fn-type → vai pra step weapon
  function wizardSelectFn(fnKey: string) {
    setWizFn(fnKey);
    setWizStep("weapon");
  }
  // Wizard: selecionou arma → cria a role com fn + weapon, auto-nome, abre o card
  function wizardSelectWeapon(pi: number, weaponId: string, weaponName: string) {
    const base = wBase(weaponId);
    const dbWeapon = weapons.find(w => wBase(w.item_id) === base);
    const idx = draft?.parties[pi].slots.length ?? 0;
    const role = emptyRole();
    role.fn = wizFn;
    role.equip.weapon = { id: weaponId, name: weaponName };
    role.weapon_db_id = dbWeapon?.id ?? null;
    role.name = weaponDisplayName(weaponId);
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: [...p.slots, { fn: wizFn, role }] }
      ),
    }));
    setOpenCard([pi, idx]);
    setAddFnWizard(null);
    if (base) loadSpells(base);
  }
  // Wizard: copiar build existente (mesma fn) → cria role copiada
  function wizardCopyRole(pi: number, src: DraftRole, srcFn: string | null) {
    const idx = draft?.parties[pi].slots.length ?? 0;
    const copied: DraftRole = JSON.parse(JSON.stringify(src));
    copied.catalog_id = null;
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: [...p.slots, { fn: srcFn ?? wizFn, role: copied }] }
      ),
    }));
    setOpenCard([pi, idx]);
    setAddFnWizard(null);
  }
  function removeSlot(pi: number, si: number) {
    upd(d => {
      const next: Draft = {
        ...d,
        parties: d.parties.map((p, i) =>
          i !== pi ? p : { ...p, slots: p.slots.filter((_, j) => j !== si) }
        ),
      };
      return pruneEmptyParties(next);
    });
    if (openCard?.[0] === pi && openCard?.[1] === si) setOpenCard(null);
  }
  async function deleteFromCatalog(catalogId: number, pi: number, si: number) {
    try {
      await api.deleteRole(catalogId);
      removeSlot(pi, si);
      setDelConfirm(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ── Save ──────────────────────────────────────────────────
  async function save() {
    if (!draft) return;
    setSaving(true); setError(null);
    try {
      // Filtra parties vazias antes de salvar
      const partiesToSave = draft.parties.filter(p => p.slots.length > 0);
      const newParties: DraftParty[] = JSON.parse(JSON.stringify(partiesToSave));
      const updatedIds = new Set<number>();
      for (let pi = 0; pi < newParties.length; pi++) {
        for (let si = 0; si < newParties[pi].slots.length; si++) {
          const role = newParties[pi].slots[si].role;
          const payload = roleToPayload(role);
          if (role.catalog_id === null) {
            if (!role.name.trim()) continue;
            const created = await api.createRole({ name: role.name, ...payload });
            newParties[pi].slots[si].role.catalog_id = created.id;
          } else if (role.equip_loaded && !updatedIds.has(role.catalog_id)) {
            updatedIds.add(role.catalog_id);
            await api.updateRole(role.catalog_id, { name: role.name, ...payload });
          }
        }
      }
      const updated = await api.updateComp(draft.id, {
        name: draft.name,
        parties: newParties.map(p => ({
          name: p.name || null,
          slots: p.slots.map(s => ({
            label: s.role.name || null,
            fn: s.fn,
            role_ids: s.role.catalog_id !== null ? [s.role.catalog_id] : [],
          })),
        })),
      });
      if (!updated) {
        // Comp foi deletada por ficar vazia — volta pra lista.
        onDeleted?.(draft.id);
        onBack();
        return;
      }
      void updated;
      setDraft(prev => {
        if (!prev) return prev;
        const pruned = pruneEmptyParties(prev);
        return {
          ...pruned,
          parties: pruned.parties.map((p, pi) => ({
            ...p,
            slots: p.slots.map((s, si) => ({
              ...s,
              role: {
                ...s.role,
                catalog_id: newParties[pi]?.slots[si]?.role.catalog_id ?? s.role.catalog_id,
              },
            })),
          })),
        };
      });
      setDirty(false); setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2500);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

if (!draft) return <div className="container"><p className="muted">{t("loading")}</p></div>;
  const d = draft;

  // ── Alt spells (mesma lógica do primário, abaixo do alt picker) ──
  function renderAltSpells(pi: number, si: number, key: string, altId: string | undefined, altIdx: number) {
    if (!altId) return null;
    const sp = spellCache[wBase(altId)];
    if (!sp?.length) return null;
    return (
      <div className="rc-spells" style={{ marginTop: 4 }}>
        {[...new Set(sp.map(s => s.slot))].map(slotName => (
          <SpellPicker key={slotName} spells={sp} slot={slotName}
            selected={d.parties[pi].slots[si].role.gear_spells[`${key}_alt_${altIdx}_${slotName}`] ?? null}
            onChange={id => updRole(pi, si, r => ({
              ...r, gear_spells: { ...r.gear_spells, [`${key}_alt_${altIdx}_${slotName}`]: id },
            }))} />
        ))}
      </div>
    );
  }

  function renderCardDetail(pi: number, si: number) {
    if (!draft) return null;
    const slot = draft.parties[pi]?.slots[si];
    if (!slot) return null;
    const role = slot.role;
    const weapBase = role.equip_loaded && role.equip.weapon?.id ? wBase(role.equip.weapon.id) : null;
    const formSpells = weapBase ? spellCache[weapBase] : undefined;

    const gearSlots = EQUIP_SLOTS
      .filter(s => ["offhand", "helmet", "armor", "boots", "cape"].includes(s.key))
      .filter(s => !(s.key === "offhand" && is2H(role.equip.weapon?.id)));

    return (
      <div className="rc-card-detail" onClick={e => e.stopPropagation()}>
        <div className="rc-detail-top-row">
          {/* Col 1 — Arma + skills Q/W/Passive */}
          <div className="rc-detail-col">
            <div className="equip-field">
              <label className="equip-field-label">{t("cbSlotWeapon")}</label>
              <ItemPicker slot="weapon"
                valueId={role.equip.weapon?.id ?? ""}
                valueName={role.equip.weapon?.name ?? ""}
                placeholder={t("selectWeaponPlaceholder")}
                onChange={(id, name) => onWeaponChange(pi, si, id, name)} />
            </div>
            {formSpells && formSpells.length > 0 && (
              <div className="rc-spells">
                <SpellPicker spells={formSpells} slot="Q"
                  selected={role.q_spell ?? null}
                  onChange={id => updRole(pi, si, r => ({ ...r, q_spell: id }))} />
                <SpellPicker spells={formSpells} slot="W"
                  selected={role.w_spell ?? null}
                  onChange={id => updRole(pi, si, r => ({ ...r, w_spell: id }))} />
                <SpellPicker spells={formSpells} slot="passive"
                  selected={role.passive_spell ?? null}
                  onChange={id => updRole(pi, si, r => ({ ...r, passive_spell: id }))} />
              </div>
            )}
          </div>

          {/* Col 2 — Consumíveis */}
          <div className="rc-detail-col">
            <h4 className="detail-section-title"><i className="ti ti-flask" aria-hidden /> {t("consumablesLabel")}</h4>
            {(["food", "potion"] as const).map(key => (
              <div className="equip-field" key={key}>
                <label className="equip-field-label">{EQUIP_SLOTS.find(s => s.key === key)?.label}</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <ItemPicker slot={key}
                      valueId={role.equip[key]?.id ?? ""}
                      valueName={role.equip[key]?.name ?? ""}
                      placeholder={EQUIP_SLOTS.find(s => s.key === key)?.label}
                      onChange={(id, name) => updRole(pi, si, r => ({
                        ...r, equip: { ...r.equip, [key]: id ? { id, name } : undefined },
                      }))} />
                  </div>
                  <input type="number" className="input"
                    title={t("qtyShortLabel")}
                    style={{ width: 58, fontSize: 13, padding: "2px 6px", textAlign: "center", flexShrink: 0 }}
                    min={1} max={999}
                    value={key === "potion" ? (role.potion_qty ?? 10) : (role.food_qty ?? 1)}
                    onFocus={captureHistory} onBlur={releaseFocus}
                    onChange={e => {
                      const v = Math.max(1, parseInt(e.target.value) || 1);
                      updRoleQuiet(pi, si, r => key === "potion"
                        ? { ...r, potion_qty: v }
                        : { ...r, food_qty: v });
                    }} />
                </div>
              </div>
            ))}
          </div>

          {/* Col 3 — Notas */}
          <div className="rc-detail-col">
            <h4 className="detail-section-title"><i className="ti ti-notes" aria-hidden /> {t("notesLabel")}</h4>
            <div className="equip-field">
              <label className="equip-field-label">{t("playStyleTitle")}</label>
              <input className="input" style={{ fontSize: 13 }}
                placeholder={t("playStylePlaceholder")}
                value={role.play_style ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, r => ({ ...r, play_style: e.target.value || null }))} />
            </div>
            <div className="equip-field">
              <label className="equip-field-label">{t("obsTitle")}</label>
              <textarea className="input rc-autosize"
                placeholder={t("obsPlaceholder")}
                value={role.obs ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, r => ({ ...r, obs: e.target.value || null }))} />
            </div>
          </div>
        </div>

        {/* Gear grid — auto-fill, offhand cell some se 2H */}
        <h4 className="detail-section-title" style={{ marginTop: 14 }}>
          <i className="ti ti-shirt" aria-hidden /> {t("equipmentLabel")}
        </h4>
        <div className={"rc-gear-grid" + (is2H(role.equip.weapon?.id) ? " rc-gear-grid-no-off" : "")}>
          {gearSlots.map(({ key, label }) => {
            const gearBase = role.equip[key]?.id ? wBase(role.equip[key]!.id) : null;
            const gearSpells = gearBase ? spellCache[gearBase] : undefined;
            return (
              <div className="equip-field rc-gear-cell" key={key}>
                <label className="equip-field-label">{label}</label>
                <ItemPicker slot={key}
                  valueId={role.equip[key]?.id ?? ""}
                  valueName={role.equip[key]?.name ?? ""}
                  placeholder={label}
                  onChange={(id, name) => {
                    updRole(pi, si, r => ({
                      ...r,
                      equip: {
                        ...r.equip,
                        [key]: id ? { id, name } : undefined,
                        ...(!id && ALT_CAPABLE.has(key) ? { [`${key}_alt`]: undefined as unknown } : {}),
                      },
                      gear_spells: Object.fromEntries(Object.entries(r.gear_spells).filter(([k]) => !k.startsWith(`${key}_`))),
                    }));
                    if (id) loadSpells(wBase(id));
                  }} />
                {gearSpells && gearSpells.length > 0 && (["helmet","armor","boots"] as const).includes(key as "helmet"|"armor"|"boots") && (() => {
                  const alts = safeAltArr((role.equip as Record<string, unknown> | undefined)?.[`${key}_alt`]);
                  const altCols = alts.map((alt, ai) => {
                    const sp = alt.id ? spellCache[wBase(alt.id)] : null;
                    return sp?.length ? { ai, sp } : null;
                  }).filter((x): x is { ai: number; sp: WeaponSpell[] } => x !== null);
                  return (
                    <div style={{ display: "flex", gap: 0, alignItems: "flex-start", marginTop: 4 }}>
                      <div className="rc-spells" style={{ paddingRight: altCols.length ? 10 : 0 }}>
                        {[...new Set(gearSpells.map(s => s.slot))].map(slotName => (
                          <SpellPicker key={slotName} spells={gearSpells} slot={slotName}
                            selected={role.gear_spells[`${key}_${slotName}`] ?? null}
                            onChange={id => updRole(pi, si, r => ({
                              ...r, gear_spells: { ...r.gear_spells, [`${key}_${slotName}`]: id },
                            }))} />
                        ))}
                      </div>
                    </div>
                  );
                })()}
                {/* Alt items + spells abaixo do alt picker (mesma lógica do primário) */}
                {ALT_CAPABLE.has(key) && role.equip[key]?.id && (() => {
                  const rawAlt = (role.equip as Record<string, unknown>)[`${key}_alt`];
                  const alts: (EquipItem | undefined)[] = Array.isArray(rawAlt) ? rawAlt : [];
                  const usedIds = [role.equip[key]!.id, ...alts.map(a => a?.id).filter(Boolean) as string[]];
                  return (
                    <>
                      {alts.map((alt, ai) => (
                        <div key={ai} style={{ marginTop: 4 }}>
                          <ItemPicker slot={key}
                            valueId={alt?.id ?? ""}
                            valueName={alt?.name ?? ""}
                            placeholder={t("alternativePlaceholder")}
                            excludeIds={usedIds.filter(id => id !== alt?.id)}
                            onChange={(id, name) => {
                              updRole(pi, si, r => {
                                const rawA = (r.equip as Record<string, unknown>)[`${key}_alt`];
                                const prev: (EquipItem | undefined)[] = Array.isArray(rawA) ? rawA : [];
                                const next = [...prev];
                                next[ai] = id ? { id, name } : undefined;
                                const filtered = next.filter((a): a is EquipItem => !!(a?.id));
                                return { ...r, equip: { ...r.equip, [`${key}_alt`]: filtered.length ? filtered : undefined } };
                              });
                              if (id) loadSpells(wBase(id));
                            }} />
                          {renderAltSpells(pi, si, key, alt?.id, ai)}
                        </div>
                      ))}
                      {alts.length < 2 && (
                        <button className="btn" style={{ fontSize: 11, padding: "2px 6px", marginTop: 4 }}
                          onClick={e => {
                            e.stopPropagation();
                            updRole(pi, si, r => {
                              const rawA = (r.equip as Record<string, unknown>)[`${key}_alt`];
                              const prev: (EquipItem | undefined)[] = Array.isArray(rawA) ? rawA : [];
                              return { ...r, equip: { ...r.equip, [`${key}_alt`]: [...prev, { id: "", name: "" }] } };
                            });
                          }}>
                          <i className="ti ti-plus" /> Alt
                        </button>
                      )}
                    </>
                  );
                })()}
              </div>
            );
          })}
        </div>

        {/* Actions — excluir do catálogo */}
        {role.catalog_id !== null && role.catalog_id !== undefined && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 8, marginTop: 10 }}>
            {delConfirm === role.catalog_id ? (
              <>
                <button className="btn" style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                  onClick={() => deleteFromCatalog(role.catalog_id!, pi, si)}>
                  {t("confirmDeleteBtn")}
                </button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                  onClick={() => setDelConfirm(null)}>
                  {t("cancel")}
                </button>
              </>
            ) : (
              <button className="btn" style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                onClick={() => setDelConfirm(role.catalog_id)}>
                <i className="ti ti-trash" aria-hidden /> {t("deleteFromCatalogBtn")}
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  // ── Render ──────────────────────────────────────────────
  return (
    <div className="container">
      <div className="card comp-editor-card">

        <div className="comp-header comp-header-2row">
          <div className="comp-header-title">
            <button className="btn" style={{ padding: "5px 10px" }} onClick={onBack} title={t("backToListTitle")}>
              <i className="ti ti-arrow-left" aria-hidden />
            </button>
            <input className="comp-name-input" value={draft.name}
              onFocus={captureHistory} onBlur={releaseFocus}
              onChange={e => updQuiet(d => ({ ...d, name: e.target.value }))} />
            {error && <span className="comp-header-error">{error}</span>}
          </div>

          <div className="comp-header-tools">
            <button className="btn comp-tool-btn"
              onClick={() => setShowFnPanel(p => !p)} title={t("configFnTypesTitle")}>
              <i className="ti ti-palette" aria-hidden /> {t("compFnTypesToolbarBtn")}
            </button>
            <button className="btn comp-tool-btn" onClick={undo} disabled={!history.length} title={t("undoLastTitle")}>
              <i className="ti ti-arrow-back-up" aria-hidden /> {t("undoLastTitle")}
            </button>
            <button className={"btn comp-save-btn" + (dirty ? " primary" : "")}
              onClick={save} disabled={!dirty || saving}>
              {saveOk
                ? <><i className="ti ti-check" aria-hidden /> {t("saved")}</>
                : saving ? t("saving")
                : <><i className="ti ti-device-floppy" aria-hidden /> {t("save")}</>}
            </button>
          </div>
        </div>

        {/* Function type CRUD panel */}
        {showFnPanel && (perms["comps.manage"] ? (
          <div className="fn-colors-panel">
            {fnTypes.map((ft, fti) => (
              <div key={ft.key} className="fn-color-row">
                <ColorPicker value={ft.color}
                  onChange={c => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, color: c } : t))} />
                <input className="fn-emoji-input" value={ft.emoji ?? ""} placeholder="😀" maxLength={4} title="Emoji"
                  onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, emoji: e.target.value || undefined } : t))} />
                <input className="fn-type-name-input" value={ft.label} style={{ color: ft.color }}
                  onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, label: e.target.value } : t))} />
                <button className="cs-xbtn" title={t("moveUpTitle")} disabled={fti === 0}
                  onClick={() => { if (fti > 0) { const n = [...fnTypes]; [n[fti-1], n[fti]] = [n[fti], n[fti-1]]; saveFnTypes(n); } }}>
                  <i className="ti ti-chevron-up" aria-hidden />
                </button>
                <button className="cs-xbtn" title={t("moveDownTitle")} disabled={fti === fnTypes.length - 1}
                  onClick={() => { if (fti < fnTypes.length - 1) { const n = [...fnTypes]; [n[fti], n[fti+1]] = [n[fti+1], n[fti]]; saveFnTypes(n); } }}>
                  <i className="ti ti-chevron-down" aria-hidden />
                </button>
                <button className="cs-xbtn" title={t("deleteTypeTitle")} onClick={() => deleteFnType(ft.key)}>
                  <i className="ti ti-x" aria-hidden />
                </button>
              </div>
            ))}
            <div className="fn-color-row" style={{ gap: 8 }}>
              <button className="btn" style={{ fontSize: 11, padding: "3px 9px" }}
                onClick={() => saveFnTypes([...fnTypes, { key: `custom_${Date.now()}`, label: t("newFnTypeLabel"), color: "#888888" }])}>
                <i className="ti ti-plus" aria-hidden /> {t("addBtn")}
              </button>
              <button className="btn" style={{ fontSize: 11, padding: "3px 9px" }}
                onClick={() => saveFnTypes([...DEFAULT_FN_TYPES])}>
                {t("restoreDefaultsBtn")}
              </button>
            </div>
          </div>
        ) : (
          <div className="fn-colors-panel">
            {fnTypes.map(ft => (
              <div key={ft.key} className="fn-color-row">
                <span className="color-swatch" style={{ background: ft.color, width: 22, height: 22, flexShrink: 0 }} />
                <span className="fn-type-name-input" style={{ color: ft.color }}>{fnLabel(ft)}</span>
              </div>
            ))}
          </div>
        ))}

        {/* Parties */}
        <div className="comp-layout comp-builder-layout">
          <div className="comp-body">
            <div className="party-list">
            {draft.parties.map((party, pi) => {
              return (
                <div key={pi} className="party-row">
                  <div className="party-num">
                    {pi + 1}
                    {party.slots.length < MAX_SLOTS && (
                      <span className="party-num-sub">{party.slots.length}/{MAX_SLOTS}</span>
                    )}
                  </div>
                  <div className="party-slots">
                    {party.slots.map((slot, si) => {
                      const role = slot.role;
                      const isSelected = openCard?.[0] === pi && openCard?.[1] === si;
                      const fnDef = slot.fn ? getFnDef(slot.fn, fnTypes) : undefined;
                      const isEditingName = editingName?.[0] === pi && editingName?.[1] === si;

                      return (
                        <div key={si}
                          className={`role-card${isSelected ? " rc-open" : ""}`}
                          onClick={() => toggleCard(pi, si)}>

                          {/* Card head — emoji fn (clicável) + weapon icon + name (botão editar) + big renders + remove. */}
                          <div className="rc-head">
                            {/* Emoji da função (clicável, abre dropdown). Some quando expandido. */}
                            <span className={"rc-fn-emoji" + (slot.fn ? "" : " rc-fn-empty")}
                              style={slot.fn ? { color: fnDef?.color } : undefined}
                              title={slot.fn ? (fnDef?.label ?? t("noFnTitle")) : t("noFnTitle")}
                              onClick={e => {
                                e.stopPropagation();
                                setFnDropdown(prev => prev?.[0] === pi && prev?.[1] === si ? null : [pi, si]);
                              }}>
                              {fnDef?.emoji ?? "·"}
                            </span>
                            {/* fn-dropdown */}
                            {fnDropdown?.[0] === pi && fnDropdown?.[1] === si && (
                              <div className="fn-dropdown" onClick={e => e.stopPropagation()}>
                                {fnTypes.map(ft => (
                                  <button key={ft.key} className="fn-dropdown-btn"
                                    style={{ background: ft.color + "25", color: ft.color, borderColor: ft.color + "60" }}
                                    onClick={e => {
                                      e.stopPropagation();
                                      updSlot(pi, si, s => ({ ...s, fn: ft.key, role: { ...s.role, fn: ft.key } }));
                                      setFnDropdown(null);
                                    }}>
                                    {fnLabel(ft)}
                                  </button>
                                ))}
                              </div>
                            )}
                            {role.equip.weapon?.id && (
                              <img className="rc-weapon-icon"
                                src={itemUrl(role.equip.weapon.id)} alt=""
                                onError={imgRetry(img => { img.style.opacity = "0.15"; })} />
                            )}
                            {/* Nome: botão editar (não input). Clica → vira input. */}
                            {isEditingName ? (
                              <input className="rc-name-input" autoFocus
                                placeholder={t("noNamePlaceholder")}
                                value={role.name}
                                onClick={e => e.stopPropagation()}
                                onFocus={captureHistory}
                                onBlur={() => { releaseFocus(); setEditingName(null); }}
                                onChange={e => updRoleQuiet(pi, si, r => ({ ...r, name: e.target.value }))}
                                onKeyDown={e => { if (e.key === "Enter") { (e.target as HTMLInputElement).blur(); } }} />
                            ) : (
                              <button className="rc-name-btn"
                                onClick={e => { e.stopPropagation(); setEditingName([pi, si]); }}>
                                <span className="rc-name">{role.name || t("noNamePlaceholder")}</span>
                                <i className="ti ti-pencil rc-name-edit-icon" aria-hidden />
                              </button>
                            )}
                            {/* Big renders (todos os equipamentos + alts) — ocupa espaço do nome. */}
                            {!isSelected && role.equip_loaded && (
                              <div className="rc-head-renders">
                                <BigRenders equip={role.equip} weaponIs2H={is2H(role.equip.weapon?.id)} />
                              </div>
                            )}
                            <button className="cs-xbtn rc-card-remove"
                              onClick={e => { e.stopPropagation(); removeSlot(pi, si); }}
                              title={t("removeFromCompTitle")}>
                              <i className="ti ti-x" aria-hidden />
                            </button>
                          </div>

                          {isSelected && renderCardDetail(pi, si)}
                        </div>
                      );
                    })}

                  {/* Wizard "Adicionar Função" */}
                  {addFnWizard === pi && (() => {
                    const sameFnRoles: { role: DraftRole; fn: string | null }[] = [];
                    if (wizFn) {
                      const seen = new Set<string>();
                      for (const p of (draft?.parties ?? [])) {
                        for (const s of p.slots) {
                          if (s.fn !== wizFn) continue;
                          const key = `${s.role.catalog_id ?? ""}|${s.role.name}`;
                          if (!seen.has(key) && (s.role.catalog_id != null || s.role.name.trim())) {
                            seen.add(key);
                            sameFnRoles.push({ role: s.role, fn: s.fn });
                          }
                        }
                      }
                      ensurePickableRolesLoaded(sameFnRoles.map(x => x.role));
                    }
                    return (
                      <div className="rc-wizard" onClick={e => e.stopPropagation()}>
                        <div className="rc-wizard-head">
                          <span className="rc-wizard-title">{t("addRoleBtn")}</span>
                          <button className="cs-xbtn" onClick={() => setAddFnWizard(null)}>
                            <i className="ti ti-x" aria-hidden />
                          </button>
                        </div>
                        {wizStep === "fn" && (
                          <div className="rc-wizard-step">
                            <p className="rc-wizard-hint">{t("selectFnTypeHint")}</p>
                            <div className="rc-wizard-fn-grid">
                              {fnTypes.map(ft => (
                                <button key={ft.key} className="fn-dropdown-btn"
                                  style={{ background: ft.color + "25", color: ft.color, borderColor: ft.color + "60" }}
                                  onClick={() => wizardSelectFn(ft.key)}>
                                  {fnLabel(ft)}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        {wizStep === "weapon" && (
                          <div className="rc-wizard-step">
                            <p className="rc-wizard-hint">{t("selectWeaponPlaceholder")}</p>
                            <ItemPicker slot="weapon"
                              valueId={wizWeaponId}
                              valueName={ITEM_BY_ID.get(wizWeaponId)?.name ?? ""}
                              placeholder={t("selectWeaponPlaceholder")}
                              onChange={(id, name) => wizardSelectWeapon(pi, id, name)} />
                            {/* Sugestões de builds com mesma fn */}
                            {sameFnRoles.length > 0 && (
                              <div className="rc-wizard-suggest">
                                <p className="rc-wizard-hint">{t("createNewRoleBtn")}:</p>
                                {sameFnRoles.map((item, idx) => (
                                  <button key={idx} className="flex-picker-item"
                                    onClick={() => wizardCopyRole(pi, item.role, item.fn)}>
                                    {item.role.equip.weapon?.id
                                      ? <img src={itemUrl(item.role.equip.weapon.id)} alt=""
                                          style={{ width: 20, height: 20, flexShrink: 0 }}
                                          onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                      : <span style={{ width: 20, flexShrink: 0 }} />}
                                    <span className="flex-picker-name">{item.role.name || t("noNamePlaceholder")}</span>
                                    {item.role.equip_loaded && <EquipStrip equip={item.role.equip} />}
                                  </button>
                                ))}
                              </div>
                            )}
                            <button className="btn" style={{ fontSize: 11, marginTop: 6 }}
                              onClick={() => setWizStep("fn")}>
                              <i className="ti ti-arrow-left" aria-hidden /> {t("backToListTitle")}
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  <div className="party-add-row">
                    <button className="party-col-add party-col-add-primary"
                      onClick={() => startAddFn(pi)}
                      disabled={party.slots.length >= MAX_SLOTS}>
                      <i className="ti ti-plus" aria-hidden />
                      {party.slots.length >= MAX_SLOTS ? t("fullLabel") : t("addRoleBtn")}
                    </button>
                  </div>
                  </div>
                </div>
              );
            })}
            </div>

            <button className="party-col-add" onClick={addParty}>
              <i className="ti ti-plus" aria-hidden /> {t("newPartyBtn")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
