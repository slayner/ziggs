import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  api, imgRetry, type GameRoleDetail, type WeaponOut, type WeaponSpell, type Permissions,
} from "../../api";
import { ItemPicker } from "../ItemPicker";
import { is2H, wBase } from "../../data/albion-items";
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

// fn-types viviam em localStorage (hideout_fn_types), por-BROWSER — agora
// migrados pra Guild.settings via API (comp/fn-types), compartilhado pela
// guilda inteira. Helpers de leitura/comparação pro migrate one-shot em
// CompEditor (useEffect de fnTypes).
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

// A área de comps é só de administradores — sempre em modo edit.
export function CompEditor({ initialDraft, initialImportCode, perms, offline, weapons, onBack }: {
  initialDraft: Draft;
  initialEditing?: boolean; // ignorado — sempre edit
  initialImportCode: CompCode | null;
  perms: Permissions;
  offline: boolean;
  weapons: WeaponOut[];
  onBack: () => void;
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
  const [collapsedParties, setCollapsedParties] = useState<Set<number>>(new Set());
  const [history,          setHistory]          = useState<Draft[]>([]);
  // fn-dot dropdown: [pi, si] | null — aberto no card cujo dot foi clicado.
  const [fnDropdown,       setFnDropdown]       = useState<[number, number] | null>(null);
  // add-copy menu: índice da party | null
  const [addCopyMenu,      setAddCopyMenu]      = useState<number | null>(null);

  // ── fn-types: carrega da guilda, migra localStorage antigo uma vez só ────
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

  // ── Mutations ─────────────────────────────────────────────
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
  function togglePartyCollapse(pi: number) {
    setCollapsedParties(prev => {
      const next = new Set(prev);
      if (next.has(pi)) next.delete(pi); else next.add(pi);
      return next;
    });
  }

  // ── Open / close card + lazy equip load ───────────────────
  async function toggleCard(pi: number, si: number) {
    if (openCard?.[0] === pi && openCard?.[1] === si) {
      setOpenCard(null); return;
    }
    setOpenCard([pi, si]); setFnDropdown(null);
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

  // Carrega o equip de uma role do catálogo por catalog_id — usado pelo menu
  // "copiar slot existente" (addCopyMenu), que lista roles de qualquer parte
  // da comp; muitas nunca tiveram o card aberto nesta sessão (equip_loaded=false).
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

  // ── Weapon change ─────────────────────────────────────────
  function onWeaponChange(pi: number, si: number, id: string, name: string) {
    const base = id ? wBase(id) : null;
    const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
    updRole(pi, si, r => ({
      ...r,
      equip: { ...r.equip, weapon: id ? { id, name } : undefined },
      fn: dbWeapon?.invisible_function ?? null,
      weapon_db_id: dbWeapon?.id ?? null,
      q_spell: null, w_spell: null, passive_spell: null,
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

  // Código de composição — cola a comp INTEIRA importada na criação.
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
  function removeParty(pi: number) {
    if (!draft || draft.parties.length <= 1) return;
    upd(d => ({ ...d, parties: d.parties.filter((_, i) => i !== pi) }));
    if (openCard?.[0] === pi) setOpenCard(null);
    setCollapsedParties(prev => {
      const next = new Set<number>();
      for (const i of prev) {
        if (i < pi) next.add(i);
        else if (i > pi) next.add(i - 1);
      }
      return next;
    });
  }
  function addSlot(pi: number) {
    if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
    const idx = draft.parties[pi].slots.length;
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: [...p.slots, { fn: null, role: emptyRole() }] }
      ),
    }));
    setOpenCard([pi, idx]);
    setAddCopyMenu(null);
  }
  function removeSlot(pi: number, si: number) {
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.filter((_, j) => j !== si) }
      ),
    }));
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
      const newParties: DraftParty[] = JSON.parse(JSON.stringify(draft.parties));
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
      void updated;
      setDraft(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          parties: prev.parties.map((p, pi) => ({
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

  // ── Conteúdo de edição inline (accordion) ──────────────────
  // Layout: 3 colunas no topo (arma+skills / consumíveis / notas) + gear grid 5 colunas abaixo.
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
        {/* 3 colunas — topo */}
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
              <textarea className="input" rows={2}
                style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                placeholder={t("playStylePlaceholder")}
                value={role.play_style ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, r => ({ ...r, play_style: e.target.value || null }))} />
            </div>
            <div className="equip-field">
              <label className="equip-field-label">{t("obsTitle")}</label>
              <textarea className="input" rows={2}
                style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                placeholder={t("obsPlaceholder")}
                value={role.obs ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, r => ({ ...r, obs: e.target.value || null }))} />
            </div>
          </div>
        </div>

        {/* Gear grid — 5 colunas */}
        <h4 className="detail-section-title" style={{ marginTop: 14 }}>
          <i className="ti ti-shirt" aria-hidden /> {t("equipmentLabel")}
        </h4>
        <div className="rc-gear-grid">
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
                      {altCols.map(({ ai, sp }) => (
                        <div key={ai} style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>
                          <div className="rc-spells">
                            {[...new Set(sp.map(s => s.slot))].map(slotName => (
                              <SpellPicker key={slotName} spells={sp} slot={slotName}
                                selected={role.gear_spells[`${key}_alt_${ai}_${slotName}`] ?? null}
                                onChange={id => updRole(pi, si, r => ({
                                  ...r, gear_spells: { ...r.gear_spells, [`${key}_alt_${ai}_${slotName}`]: id },
                                }))} />
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
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

  // ── Render (builder) ──────────────────────────────────────
  return (
    <div className="container">
      <div className="card comp-editor-card">

        {/* Header */}
        <div className="comp-header comp-header-2row">
          <div className="comp-header-title">
            <button className="btn" style={{ padding: "5px 10px" }} onClick={onBack} title={t("backToListTitle")}>
              <i className="ti ti-arrow-left" aria-hidden />
            </button>
            <input className="comp-name-input" value={draft.name}
              onFocus={captureHistory} onBlur={releaseFocus}
              onChange={e => updQuiet(d => ({ ...d, name: e.target.value }))} />
            {offline && <span className="badge">{t("demoBadge")}</span>}
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
            {draft.parties.map((party, pi) => {
              const isCollapsed = collapsedParties.has(pi);
              return (
                <div key={pi} className="party-card">

                  <div className="party-card-head">
                    <button className="party-collapse-btn"
                      onClick={() => togglePartyCollapse(pi)}
                      title={isCollapsed ? t("expandBtn") : t("collapseBtn")}>
                      <i className={`ti ti-chevron-${isCollapsed ? "right" : "down"}`} aria-hidden />
                    </button>
                    <input className="party-name-input" value={party.name}
                      placeholder={`Party ${pi + 1}`}
                      onFocus={captureHistory} onBlur={releaseFocus}
                      onChange={e => updQuiet(d => ({
                        ...d,
                        parties: d.parties.map((p, i) => i !== pi ? p : { ...p, name: e.target.value }),
                      }))} />
                    <span className={"party-count" + (party.slots.length >= MAX_SLOTS ? " full" : "")}>
                      {party.slots.length}/{MAX_SLOTS}
                    </span>
                    {draft.parties.length > 1 && (
                      <button className="cs-xbtn" onClick={() => removeParty(pi)} title={t("removePartyTitle")}>
                        <i className="ti ti-x" aria-hidden />
                      </button>
                    )}
                  </div>

                  {!isCollapsed && (
                    <div className="party-card-body">
                      {party.slots.map((slot, si) => {
                        const role = slot.role;
                        const isSelected = openCard?.[0] === pi && openCard?.[1] === si;
                        const chipColor = slot.fn ? (getFnDef(slot.fn, fnTypes)?.color ?? "#888") : undefined;
                        const fnDef = slot.fn ? getFnDef(slot.fn, fnTypes) : undefined;

                        return (
                          <div key={si}
                            className={`role-card${isSelected ? " rc-open" : ""}`}
                            style={{ "--chip-color": chipColor ?? "var(--border-strong)", cursor: "pointer" } as CSSProperties}
                            onClick={() => toggleCard(pi, si)}>

                            {/* Card head — fn dot (clicável) + weapon icon + name (editável) + equip strip + remove. */}
                            <div className="rc-head">
                              <span className={"rc-fn-dot" + (slot.fn ? "" : " rc-fn-empty")}
                                style={{ background: chipColor ?? "transparent" }}
                                title={slot.fn ? (fnDef?.label ?? t("noFnTitle")) : t("noFnTitle")}
                                onClick={e => {
                                  e.stopPropagation();
                                  setFnDropdown(prev => prev?.[0] === pi && prev?.[1] === si ? null : [pi, si]);
                                }} />
                              {/* fn-dropdown — menu de fn-types ancorado ao dot */}
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
                              {isSelected ? (
                                <input className="rc-name-input"
                                  placeholder={t("noNamePlaceholder")}
                                  value={role.name}
                                  onClick={e => e.stopPropagation()}
                                  onFocus={captureHistory} onBlur={releaseFocus}
                                  onChange={e => updRoleQuiet(pi, si, r => ({ ...r, name: e.target.value }))} />
                              ) : (
                                <span className="rc-name">{role.name || t("noNamePlaceholder")}</span>
                              )}
                              {role.equip_loaded && (
                                <div className="rc-head-strip" style={{ marginLeft: 6, flexShrink: 0 }}>
                                  <EquipStrip equip={role.equip} weaponIs2H={is2H(role.equip.weapon?.id)} />
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

                    {/* Add role — 1 clique cria role vazia + botão copiar de outra */}
                    {addCopyMenu === pi && (() => {
                      const seen = new Set<string>();
                      const pickableSlots: { fn: string | null; role: DraftRole }[] = [];
                      for (const p of (draft?.parties ?? [])) {
                        for (const s of p.slots) {
                          const key = `${s.role.catalog_id ?? ""}|${s.role.name}`;
                          if (!seen.has(key) && (s.role.catalog_id != null || s.role.name.trim())) {
                            seen.add(key);
                            pickableSlots.push({ fn: s.fn, role: s.role });
                          }
                        }
                      }
                      return (
                        <div className="flex-picker-menu">
                          {pickableSlots.map((item, idx) => (
                            <button key={idx} className="flex-picker-item"
                              onClick={() => {
                                if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
                                const newSlotIdx = draft.parties[pi].slots.length;
                                const copied: DraftRole = JSON.parse(JSON.stringify(item.role));
                                copied.catalog_id = null;
                                upd(d => ({
                                  ...d,
                                  parties: d.parties.map((p, i) =>
                                    i !== pi ? p : { ...p, slots: [...p.slots, { fn: item.fn, role: copied }] }
                                  ),
                                }));
                                setOpenCard([pi, newSlotIdx]);
                                setAddCopyMenu(null);
                              }}>
                              {item.role.equip.weapon?.id
                                ? <img src={itemUrl(item.role.equip.weapon.id)} alt=""
                                    style={{ width: 20, height: 20, flexShrink: 0 }}
                                    onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                : <span style={{ width: 20, flexShrink: 0 }} />}
                              <span className="flex-picker-name">{item.role.name || t("noNamePlaceholder")}</span>
                              {item.fn && (() => { const ft = getFnDef(item.fn, fnTypes); return ft ? (
                                <span className="rc-fn-badge" style={{ background: ft.color + "25", color: ft.color, flexShrink: 0 }}>
                                  {fnLabel(ft)}
                                </span>
                              ) : null; })()}
                              {item.role.equip_loaded && <EquipStrip equip={item.role.equip} />}
                            </button>
                          ))}
                          {!pickableSlots.length && <span className="flex-picker-empty">{t("noRoleInComp")}</span>}
                        </div>
                      );
                    })()}
                    <div className="party-add-row">
                      <button className="party-col-add party-col-add-primary"
                        onClick={() => addSlot(pi)}
                        disabled={party.slots.length >= MAX_SLOTS}>
                        <i className="ti ti-plus" aria-hidden />
                        {party.slots.length >= MAX_SLOTS ? t("fullLabel") : t("addRoleBtn")}
                      </button>
                      <button className="party-col-add party-col-add-secondary"
                        onClick={() => {
                          const opening = addCopyMenu !== pi;
                          setAddCopyMenu(opening ? pi : null);
                          if (opening) {
                            const roles = (draft?.parties ?? []).flatMap(p => p.slots.map(s => s.role).filter(Boolean));
                            ensurePickableRolesLoaded(roles);
                          }
                        }}
                        disabled={party.slots.length >= MAX_SLOTS}
                        title={t("createNewRoleBtn")}>
                        <i className="ti ti-copy" aria-hidden />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
            })}

            <button className="party-col-add" onClick={addParty}>
              <i className="ti ti-plus" aria-hidden /> {t("newPartyBtn")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}