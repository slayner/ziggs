import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  api, imgRetry, type GameRoleDetail, type WeaponOut, type WeaponSpell, type Permissions,
} from "../../api";
import { ItemPicker } from "../ItemPicker";
import { is2H, wBase } from "../../data/albion-items";
import { useT } from "../../i18n";

import { BuildTabs } from "./BuildTabs";
import { ColorPicker } from "./ColorPicker";
import { EquipStrip } from "./EquipStrip";
import {
  ALT_CAPABLE, DEFAULT_FN_TYPES, MAX_SLOTS, buildItemsToEquip, emptyRole,
  fnLabel, getFnDef, itemUrl, roleIdentityKey, roleToPayload, safeAltArr, useEquipSlots,
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
    // formato antigo: Record<string, {label, color}>
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

// A área de comps é só de administradores — não há modo view, o editor está
// SEMPRE em modo edit. `initialEditing` é mantido na assinatura pra não
// quebrar o caller (CompBuilder), mas ignorado.
export function CompEditor({ initialDraft, initialImportCode, perms, offline, weapons, onBack }: {
  initialDraft: Draft;
  initialEditing?: boolean;
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
  // fn-types agora são da guilda (Guild.settings via API — comp/fn-types),
  // não mais por-browser (localStorage). Começa no default (fallback offline
  // e "primeiro paint" antes do fetch) — o useEffect abaixo carrega o valor
  // real do servidor e, se o servidor estiver vazio, migra o localStorage
  // antigo (hideout_fn_types) uma vez só.
  const [fnTypes,          setFnTypes]          = useState<FnTypeDef[]>(() => [...DEFAULT_FN_TYPES]);
  const [showFnPanel,      setShowFnPanel]      = useState(false);
  const [collapsedParties, setCollapsedParties] = useState<Set<number>>(new Set());
  const [history,          setHistory]          = useState<Draft[]>([]);
  const [flexMenu,         setFlexMenu]         = useState<[number, number] | null>(null);
  const [addCopyMenu,      setAddCopyMenu]      = useState<number | null>(null);
  const [editRi,           setEditRi]           = useState(0);

  // ── fn-types: carrega da guilda, migra localStorage antigo uma vez só ────
  useEffect(() => {
    api.getCompFnTypes()
      .then(({ fn_types }) => {
        if (fn_types.length > 0) { setFnTypes(fn_types); return; }
        // Servidor vazio — migra o localStorage antigo (por-browser) só se
        // tiver algo customizado e este user puder gravar pra guilda; senão
        // fica no default (já é o estado inicial, nada a fazer).
        const local = readLocalFnTypes();
        if (local && perms["comps.manage"] && !isDefaultFnTypes(local)) {
          api.putCompFnTypes(local)
            .then(({ fn_types: saved }) => {
              setFnTypes(saved);
              localStorage.removeItem("hideout_fn_types");
            })
            .catch(() => {});
        }
      })
      .catch(() => {}); // offline/erro — mantém DEFAULT_FN_TYPES do estado inicial
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
  // Atualiza sem empurrar ao histórico — usado em inputs de texto (onChange)
  function updQuiet(fn: (d: Draft) => Draft) {
    setDraft(prev => prev ? fn(prev) : prev);
    setDirty(true); setSaveOk(false); setError(null);
  }
  // Captura o estado atual no histórico uma vez por sessão de foco
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

  function getPickableRoles(pi: number, si: number): DraftRole[] {
    if (!draft) return [];
    const inSlot = new Set(
      draft.parties[pi]?.slots[si]?.roles.map(roleIdentityKey) ?? []
    );
    const seen = new Set<string>();
    const result: DraftRole[] = [];
    for (const p of draft.parties) {
      for (const s of p.slots) {
        for (const r of s.roles) {
          const key = roleIdentityKey(r);
          if (!seen.has(key) && !inSlot.has(key) && (r.catalog_id != null || r.name.trim())) {
            seen.add(key);
            result.push(r);
          }
        }
      }
    }
    return result;
  }

  function updSlot(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRole(pi: number, si: number, ri: number, fn: (r: DraftRole) => DraftRole) {
    updSlot(pi, si, s => ({ ...s, roles: s.roles.map((r, i) => i !== ri ? r : fn(r)) }));
  }
  function updSlotQuiet(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    updQuiet(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRoleQuiet(pi: number, si: number, ri: number, fn: (r: DraftRole) => DraftRole) {
    updSlotQuiet(pi, si, s => ({ ...s, roles: s.roles.map((r, i) => i !== ri ? r : fn(r)) }));
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
  // Painel de detalhe: clicar num slot seleciona; o formulário de edição
  // sempre renderiza no painel direito (master-detail).
  async function toggleCard(pi: number, si: number) {
    if (openCard?.[0] === pi && openCard?.[1] === si) {
      setOpenCard(null); setFlexMenu(null); return;
    }
    setOpenCard([pi, si]); setFlexMenu(null); setEditRi(0);
    if (!draft) return;
    const roles = draft.parties[pi]?.slots[si]?.roles ?? [];
    for (let ri = 0; ri < roles.length; ri++) {
      const role = roles[ri];
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
                      roles: s.roles.map((r, k) =>
                        k !== ri ? r : {
                          ...r, equip,
                          color: detail.color ?? null,
                          play_style: detail.play_style,
                          abilities:  detail.abilities,
                          obs:        detail.obs,
                          q_spell:       detail.q_spell ?? null,
                          w_spell:       detail.w_spell ?? null,
                          passive_spell: detail.passive_spell ?? null,
                          gear_spells:   detail.gear_spells ?? {},
                          equip_loaded: true,
                        }
                      ),
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
                  slots: p.slots.map((s, j) =>
                    j !== si ? s : {
                      ...s,
                      roles: s.roles.map((r, k) => k !== ri ? r : { ...r, equip_loaded: true }),
                    }
                  ),
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
  }

  // Carrega o equip de uma role do catálogo por catalog_id, sem depender de
  // (pi,si,ri) — usado pelo menu de "copiar build/slot existente"
  // (getPickableRoles / addCopyMenu), que listam roles de QUALQUER parte da
  // comp: muitas nunca tiveram o card aberto nesta sessão, então
  // equip_loaded ainda é false e o EquipStrip da sugestão fica vazio.
  async function ensureCatalogRoleLoaded(catalogId: number) {
    if (!draft) return;
    const already = draft.parties.some(p => p.slots.some(s => s.roles.some(r => r.catalog_id === catalogId && r.equip_loaded)));
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
              roles: s.roles.map(r => r.catalog_id !== catalogId ? r : fn(r)),
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
  function onWeaponChange(pi: number, si: number, ri: number, id: string, name: string) {
    const base = id ? wBase(id) : null;
    const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
    updRole(pi, si, ri, r => ({
      ...r,
      equip: { ...r.equip, weapon: id ? { id, name } : undefined },
      fn: dbWeapon?.invisible_function ?? null,
      weapon_db_id: dbWeapon?.id ?? null,
      q_spell: null, w_spell: null, passive_spell: null,
    }));
    if (base && id) loadSpells(base);
  }

  // Carrega spells de todo item com kit próprio (arma + gear) de um equip —
  // usado depois de colar um código de composição, que pode trazer várias
  // armas/gear de uma vez (cada role da comp colada).
  function loadSpellsForEquip(equip: DraftEquip) {
    const weaponId = equip.weapon?.id;
    if (weaponId) loadSpells(wBase(weaponId));
    for (const gk of ["helmet", "armor", "boots"] as const) {
      const gid = equip[gk]?.id;
      if (gid) loadSpells(wBase(gid));
    }
  }

  // Código de composição — cola a comp INTEIRA (todas as parties, slots e
  // roles) importada na criação da comp (ver CompList). SUBSTITUI a
  // estrutura atual; cada role vira uma role NOVA a salvar (catalog_id
  // null), igual ao "copiar slot existente" do menu de flex.
  function applyCompCode(code: CompCode) {
    upd(d => ({
      ...d,
      parties: code.parties.map(p => ({
        name: p.name,
        slots: p.slots.map(s => ({
          fn: s.fn,
          roles: s.roles.map((r): DraftRole => {
            const weaponId = r.equip.weapon?.id;
            const base = weaponId ? wBase(weaponId) : null;
            const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
            return {
              catalog_id: null, name: r.name, color: null,
              fn: dbWeapon?.invisible_function ?? r.fn,
              weapon_db_id: dbWeapon?.id ?? null,
              equip: r.equip, equip_loaded: true,
              play_style: r.play_style, abilities: r.abilities, obs: null,
              q_spell: r.q_spell, w_spell: r.w_spell, passive_spell: r.passive_spell,
              gear_spells: r.gear_spells,
              potion_qty: r.potion_qty, food_qty: r.food_qty,
              ...(r.flex_of ? { flex_of: r.flex_of } : {}),
            };
          }),
        })),
      })),
    }));
    setOpenCard(null); setEditRi(0);
    for (const p of code.parties) for (const s of p.slots) for (const r of s.roles) loadSpellsForEquip(r.equip);
  }

  // Comp criada a partir de um código importado (ver CompList) — aplica uma
  // vez só ao abrir, antes de qualquer interação do usuário.
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
  // Cria uma role VAZIA direto e abre o painel de edição nela — caminho
  // principal de "adicionar role". 1 clique. (O menu de copiar de outra role
  // virou botão secundário — addCopyMenu.)
  function addSlot(pi: number) {
    if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
    const idx = draft.parties[pi].slots.length;
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: [...p.slots, { fn: null, roles: [emptyRole()] }] }
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
          for (let ri = 0; ri < newParties[pi].slots[si].roles.length; ri++) {
            const role = newParties[pi].slots[si].roles[ri];
            const payload = roleToPayload(role);
            if (role.catalog_id === null) {
              if (!role.name.trim()) continue;
              const created = await api.createRole({ name: role.name, ...payload });
              newParties[pi].slots[si].roles[ri].catalog_id = created.id;
            } else if (role.equip_loaded && !updatedIds.has(role.catalog_id)) {
              updatedIds.add(role.catalog_id);
              await api.updateRole(role.catalog_id, { name: role.name, ...payload });
            }
          }
        }
      }
      const updated = await api.updateComp(draft.id, {
        name: draft.name,
        parties: newParties.map(p => ({
          name: p.name || null,
          slots: p.slots.map(s => ({
            label: s.roles[0]?.name || null,
            fn: s.fn,
            role_ids: s.roles.map(r => r.catalog_id).filter((id): id is number => id !== null),
          })),
        })),
      });
      // Keep current draft (preserves fn, equip, spells, etc.) — only sync new catalog_ids
      void updated;
      setDraft(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          parties: prev.parties.map((p, pi) => ({
            ...p,
            slots: p.slots.map((s, si) => ({
              ...s,
              roles: s.roles.map((r, ri) => ({
                ...r,
                catalog_id: newParties[pi]?.slots[si]?.roles[ri]?.catalog_id ?? r.catalog_id,
              })),
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
  // Antes um painel direito (master-detail de 2 quadrantes); agora renderiza
  // DENTRO do card expandido (1 quadrante só). 5 seções com headers editoriais:
  // (a) Identidade, (b) Equipamento, (c) Habilidades, (d) Alts, (e) Notas.
  function renderCardDetail(pi: number, si: number) {
    if (!draft) return null;
    const slot = draft.parties[pi]?.slots[si];
    if (!slot) return null;
    const role = slot.roles[0];
    const formRole = slot.roles[editRi] ?? role;
    const formWeapBase = formRole?.equip_loaded && formRole.equip.weapon?.id
      ? wBase(formRole.equip.weapon.id) : null;
    const formSpells = formWeapBase ? spellCache[formWeapBase] : undefined;

    // Equipamento (gear com kit próprio): offhand/helmet/armor/boots/cape —
    // weapon e consumíveis ficam em suas próprias seções.
    const gearSlots = EQUIP_SLOTS
      .filter(s => ["offhand", "helmet", "armor", "boots", "cape"].includes(s.key))
      .filter(s => !(s.key === "offhand" && is2H(formRole?.equip.weapon?.id)));

    return (
      <div className="rc-card-detail" onClick={e => e.stopPropagation()}>
        {/* Cabeçalho — preview vivo do equipamento (a EquipStrip do head do
            card some no aberto; esta é a versão completa do detail). */}
        <div className="rc-card-detail-head">
          {formRole?.equip_loaded && (
            <div className="rc-equip-strip">
              <EquipStrip equip={formRole.equip} weaponIs2H={is2H(formRole.equip.weapon?.id)} />
            </div>
          )}
        </div>

        {/* Abas de build (flex) — integradas visualmente ao card expandido,
            logo abaixo do head, antes das seções. A aba "+" abre o flex picker. */}
        <div className="rc-card-buildtabs">
          <BuildTabs roles={slot.roles}
            active={editRi}
            onSelect={setEditRi}
            onAdd={() => setFlexMenu(prev => {
              const next: [number, number] | null =
                prev?.[0] === pi && prev?.[1] === si ? null : [pi, si];
              if (next) ensurePickableRolesLoaded(getPickableRoles(pi, si));
              return next;
            })}
            addOpen={flexMenu?.[0] === pi && flexMenu?.[1] === si} />
        </div>

        <div className="rc-card-detail-body">
          {/* Flex picker — ancorado logo abaixo das abas */}
          {flexMenu?.[0] === pi && flexMenu?.[1] === si && (() => {
            const pickable = getPickableRoles(pi, si);
            return (
              <div className="flex-picker-menu">
                <button className="flex-picker-item flex-picker-new"
                  onClick={() => {
                    const newRi = slot.roles.length;
                    updSlot(pi, si, s => ({ ...s, roles: [...s.roles, emptyRole()] }));
                    setEditRi(newRi);
                    setFlexMenu(null);
                  }}>
                  <i className="ti ti-plus" aria-hidden /> {t("newBuildBtn")}
                </button>
                {pickable.map((r, idx) => (
                  <button key={idx} className="flex-picker-item"
                    onClick={() => {
                      const newRi = slot.roles.length;
                      const copy = JSON.parse(JSON.stringify(r));
                      copy.catalog_id = null;
                      copy.flex_of = r.name || r.flex_of;
                      copy.name = r.name ? `${r.name} (flex)` : "";
                      updSlot(pi, si, s => ({ ...s, roles: [...s.roles, copy] }));
                      setEditRi(newRi);
                      setFlexMenu(null);
                    }}>
                    {r.equip.weapon?.id
                      ? <img src={itemUrl(r.equip.weapon.id)} alt=""
                          style={{ width: 20, height: 20, flexShrink: 0 }}
                          onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                      : <span style={{ width: 20, flexShrink: 0 }} />}
                    <span className="flex-picker-name">
                      {r.name || t("noNamePlaceholder")}
                      {r.flex_of && <span style={{ color: "var(--muted)", fontSize: 10 }}> ({t("flexOfPrefix")} {r.flex_of})</span>}
                    </span>
                    {r.fn && (() => { const ft = getFnDef(r.fn, fnTypes); return ft ? (
                      <span className="rc-fn-badge" style={{
                        background: ft.color + "25", color: ft.color, flexShrink: 0,
                      }}>{fnLabel(ft)}</span>
                    ) : null; })()}
                    {r.equip_loaded && <EquipStrip equip={r.equip} />}
                  </button>
                ))}
                {!pickable.length && (
                  <span className="flex-picker-empty">{t("noOtherBuildInComp")}</span>
                )}
              </div>
            );
          })()}

          {/* ── (a) Identidade: nome + função + cor do slot ─────── */}
          <div className="detail-section">
            <h4 className="detail-section-title"><i className="ti ti-id-badge-2" aria-hidden /> {t("identityLabel")}</h4>
            <div className="rc-head">
              {formRole?.equip.weapon?.id && (
                <img className="rc-weapon-icon" src={itemUrl(formRole.equip.weapon.id)} alt=""
                  onError={imgRetry(img => { img.style.opacity = "0.15"; })} />
              )}
              <input className="input"
                style={{ flex: 1, minWidth: 0, padding: "2px 6px", fontSize: 13, height: 26 }}
                placeholder={t("noNamePlaceholder")}
                value={formRole.name}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, editRi, r => ({ ...r, name: e.target.value }))} />
            </div>
            {role ? null : <span className="chip-empty" style={{ fontSize: 12 }}>{t("noRoleAssigned")}</span>}
            <div className="equip-field">
              <label className="equip-field-label">{t("compFnTypesToolbarBtn")}</label>
              <select className="fn-type-select"
                value={slot.fn ?? ""}
                onChange={e => {
                  const v = e.target.value || null;
                  updSlot(pi, si, s => ({ ...s, fn: v, roles: s.roles.map((r, i) => i === editRi ? { ...r, fn: v } : r) }));
                }}
                style={{ color: getFnDef(slot.fn, fnTypes)?.color ?? "var(--muted)" }}>
                <option value="">—</option>
                {fnTypes.map(ft => (
                  <option key={ft.key} value={ft.key}>{fnLabel(ft)}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Fn type picker — só botões de escolha (um por fn-type). O painel
              de editar/criar/deletar fn-types fica no botão "Funções" da toolbar
              (showFnPanel); aqui é só atribuir, não configurar. */}
          {slot.fn === null && (
            <div className="fn-type-picker">
              <p className="fn-picker-hint">{t("selectFnTypeHint")}</p>
              <div className="fn-picker-btns">
                {fnTypes.map(ft => (
                  <button key={ft.key} className="fn-type-assign-btn"
                    style={{ background: ft.color + "25", color: ft.color, borderColor: ft.color + "60" }}
                    onClick={() => updSlot(pi, si, s => ({
                      ...s, fn: ft.key,
                      roles: s.roles.map((r, i) => i === 0 ? { ...r, fn: ft.key } : r),
                    }))}>
                    {fnLabel(ft)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── (b) Equipamento: weapon + gear grid ───────────── */}
          <div className="detail-section">
            <h4 className="detail-section-title"><i className="ti ti-shirt" aria-hidden /> {t("equipmentLabel")}</h4>
            <div className="equip-field">
              <label className="equip-field-label">{t("cbSlotWeapon")}</label>
              <ItemPicker slot="weapon"
                valueId={formRole?.equip.weapon?.id ?? ""}
                valueName={formRole?.equip.weapon?.name ?? ""}
                placeholder={t("selectWeaponPlaceholder")}
                onChange={(id, name) => onWeaponChange(pi, si, editRi, id, name)} />
            </div>
            <div className="equip-grid-narrow">
              {gearSlots.map(({ key, label }) => {
                const gearBase = formRole?.equip[key]?.id ? wBase(formRole.equip[key]!.id) : null;
                const gearSpells = gearBase ? spellCache[gearBase] : undefined;
                return (
                  <div className="equip-field" key={key}>
                    <label className="equip-field-label">{label}</label>
                    <ItemPicker slot={key}
                      valueId={formRole?.equip[key]?.id ?? ""}
                      valueName={formRole?.equip[key]?.name ?? ""}
                      placeholder={label}
                      onChange={(id, name) => {
                        updRole(pi, si, editRi, r => ({
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
                    {ALT_CAPABLE.has(key) && formRole?.equip[key]?.id && (() => {
                      const rawAlt = (formRole.equip as Record<string, unknown>)[`${key}_alt`];
                      const alts: (EquipItem | undefined)[] = Array.isArray(rawAlt) ? rawAlt : [];
                      const usedIds = [formRole.equip[key]!.id, ...alts.map(a => a?.id).filter(Boolean) as string[]];
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
                                  updRole(pi, si, editRi, r => {
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
                                updRole(pi, si, editRi, r => {
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
                      const alts = safeAltArr((formRole?.equip as Record<string, unknown> | undefined)?.[`${key}_alt`]);
                      const altCols = alts.map((alt, ai) => {
                        const sp = alt.id ? spellCache[wBase(alt.id)] : null;
                        return sp?.length ? { ai, sp } : null;
                      }).filter((x): x is { ai: number; sp: WeaponSpell[] } => x !== null);
                      return (
                        <div style={{ display: "flex", gap: 0, alignItems: "flex-start", marginTop: 4 }}>
                          <div className="rc-spells" style={{ paddingRight: altCols.length ? 10 : 0 }}>
                            {[...new Set(gearSpells.map(s => s.slot))].map(slotName => (
                              <SpellPicker key={slotName} spells={gearSpells} slot={slotName}
                                selected={formRole?.gear_spells[`${key}_${slotName}`] ?? null}
                                onChange={id => updRole(pi, si, editRi, r => ({
                                  ...r, gear_spells: { ...r.gear_spells, [`${key}_${slotName}`]: id },
                                }))} />
                            ))}
                          </div>
                          {altCols.map(({ ai, sp }) => (
                            <div key={ai} style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>
                              <div className="rc-spells">
                                {[...new Set(sp.map(s => s.slot))].map(slotName => (
                                  <SpellPicker key={slotName} spells={sp} slot={slotName}
                                    selected={formRole?.gear_spells[`${key}_alt_${ai}_${slotName}`] ?? null}
                                    onChange={id => updRole(pi, si, editRi, r => ({
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
          </div>

          {/* ── (c) Habilidades: Q/W/Passive da arma ───────────── */}
          {formSpells && formSpells.length > 0 && (
            <div className="detail-section">
              <h4 className="detail-section-title"><i className="ti ti-bolt" aria-hidden /> {t("abilitiesQwpLabel")}</h4>
              <div className="rc-spells">
                <SpellPicker spells={formSpells} slot="Q"
                  selected={formRole?.q_spell ?? null}
                  onChange={id => updRole(pi, si, editRi, r => ({ ...r, q_spell: id }))} />
                <SpellPicker spells={formSpells} slot="W"
                  selected={formRole?.w_spell ?? null}
                  onChange={id => updRole(pi, si, editRi, r => ({ ...r, w_spell: id }))} />
                <SpellPicker spells={formSpells} slot="passive"
                  selected={formRole?.passive_spell ?? null}
                  onChange={id => updRole(pi, si, editRi, r => ({ ...r, passive_spell: id }))} />
              </div>
            </div>
          )}

          {/* ── (d) Consumíveis — comida/poção com qtd inline ── */}
          <div className="detail-section">
            <h4 className="detail-section-title"><i className="ti ti-flask" aria-hidden /> {t("consumablesLabel")}</h4>
            {(["food", "potion"] as const).map(key => (
              <div className="equip-field" key={key}>
                <label className="equip-field-label">{EQUIP_SLOTS.find(s => s.key === key)?.label}</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <ItemPicker slot={key}
                      valueId={formRole?.equip[key]?.id ?? ""}
                      valueName={formRole?.equip[key]?.name ?? ""}
                      placeholder={EQUIP_SLOTS.find(s => s.key === key)?.label}
                      onChange={(id, name) => updRole(pi, si, editRi, r => ({
                        ...r, equip: { ...r.equip, [key]: id ? { id, name } : undefined },
                      }))} />
                  </div>
                  <input type="number" className="input"
                    title={t("qtyShortLabel")}
                    style={{ width: 58, fontSize: 13, padding: "2px 6px", textAlign: "center", flexShrink: 0 }}
                    min={1} max={999}
                    value={key === "potion" ? (formRole?.potion_qty ?? 10) : (formRole?.food_qty ?? 1)}
                    onFocus={captureHistory} onBlur={releaseFocus}
                    onChange={e => {
                      const v = Math.max(1, parseInt(e.target.value) || 1);
                      updRoleQuiet(pi, si, editRi, r => key === "potion"
                        ? { ...r, potion_qty: v }
                        : { ...r, food_qty: v });
                    }} />
                </div>
              </div>
            ))}
          </div>

          {/* ── (e) Notas: playstyle + observações ────────────── */}
          <div className="detail-section">
            <h4 className="detail-section-title"><i className="ti ti-notes" aria-hidden /> {t("notesLabel")}</h4>
            <div className="equip-field">
              <label className="equip-field-label">{t("playStyleTitle")}</label>
              <textarea className="input" rows={2}
                style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                placeholder={t("playStylePlaceholder")}
                value={formRole?.play_style ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, editRi, r => ({
                  ...r, play_style: e.target.value || null,
                }))} />
            </div>
            <div className="equip-field">
              <label className="equip-field-label">{t("obsTitle")}</label>
              <textarea className="input" rows={2}
                style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                placeholder={t("obsPlaceholder")}
                value={formRole?.obs ?? ""}
                onFocus={captureHistory} onBlur={releaseFocus}
                onChange={e => updRoleQuiet(pi, si, editRi, r => ({
                  ...r, obs: e.target.value || null,
                }))} />
            </div>
          </div>

          {/* Actions — só renderiza quando há ação disponível */}
          {((editRi === 0 && role?.catalog_id !== null && role?.catalog_id !== undefined) || editRi > 0) && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            {editRi === 0 && role?.catalog_id !== null && role?.catalog_id !== undefined && (
              delConfirm === role.catalog_id ? (
                <>
                  <button className="btn"
                    style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                    onClick={() => deleteFromCatalog(role.catalog_id!, pi, si)}>
                    {t("confirmDeleteBtn")}
                  </button>
                  <button className="btn"
                    style={{ fontSize: 11, padding: "3px 8px" }}
                    onClick={() => setDelConfirm(null)}>
                    {t("cancel")}
                  </button>
                </>
              ) : (
                <button className="btn"
                  style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                  onClick={() => setDelConfirm(role.catalog_id)}>
                  <i className="ti ti-trash" aria-hidden /> {t("deleteFromCatalogBtn")}
                </button>
              )
            )}
            {editRi > 0 && (
              <button className="btn"
                style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                onClick={() => {
                  updSlot(pi, si, s => ({ ...s, roles: s.roles.filter((_, i) => i !== editRi) }));
                  setEditRi(0);
                }}>
                <i className="ti ti-x" aria-hidden /> {t("removeFlexBtn")}
              </button>
            )}
          </div>
          )}
        </div>
      </div>
    );
  }

  // ── Render (builder) ──────────────────────────────────────
  return (
    <div className="container">
      <div className="card comp-editor-card">

        {/* Header — duas faixas: identidade (voltar + nome) e toolbar
            (funções + undo + salvar). Sem toggle view/edit — só edit. */}
        <div className="comp-header comp-header-2row">
          <div className="comp-header-title">
            <button className="btn" style={{ padding: "5px 10px" }}
              onClick={onBack}
              title={t("backToListTitle")}>
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

            <button className="btn comp-tool-btn" onClick={undo} disabled={!history.length}
              title={t("undoLastTitle")}>
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

        {/* Function type CRUD panel — fn-types agora são da guilda (Guild.settings
            via API), não mais por-browser; só quem tem comps.manage edita, o
            resto vê uma lista read-only. */}
        {showFnPanel && (perms["comps.manage"] ? (
          <div className="fn-colors-panel">
            {fnTypes.map((ft, fti) => (
              <div key={ft.key} className="fn-color-row">
                <ColorPicker value={ft.color}
                  onChange={c => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, color: c } : t))} />
                <input className="fn-emoji-input" value={ft.emoji ?? ""} placeholder="😀" maxLength={4}
                  title="Emoji"
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

        {/* Parties — 1 quadrante só (accordion inline); sem painel direito. */}
        <div className="comp-layout comp-builder-layout">
          <div className="comp-body">
            {draft.parties.map((party, pi) => {
              const isCollapsed = collapsedParties.has(pi);
              return (
                <div key={pi} className="party-card">

                  {/* Party header */}
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

                  {/* Party body */}
                  {!isCollapsed && (
                    <div className="party-card-body">
                      {party.slots.map((slot, si) => {
                        const role       = slot.roles[0];
                        const isSelected = openCard?.[0] === pi && openCard?.[1] === si;
                        const chipColor  = slot.fn ? (getFnDef(slot.fn, fnTypes)?.color ?? "#888") : undefined;

                        return (
                          <div key={si}
                            className={`role-card${isSelected ? " rc-open" : ""}`}
                            style={{ "--chip-color": chipColor ?? "var(--border-strong)", cursor: "pointer" } as CSSProperties}
                            onClick={() => toggleCard(pi, si)}>

                            {/* Card head — fn dot + weapon icon + name + equip strip + flex badge + remove.
                                Função expressa pela borda esquerda larga (6px) + um dot colorido
                                no canto superior esquerdo (.rc-fn-dot). EquipStrip some no card
                                aberto (a do detail basta) — ver .role-card.rc-open .rc-head-strip. */}
                            <div className="rc-head">
                              {role ? (
                                <>
                                  <span className="rc-fn-dot"
                                    style={{ background: chipColor ?? "transparent" }}
                                    title={slot.fn ? (getFnDef(slot.fn, fnTypes)?.label ?? t("noFnTitle")) : t("noFnTitle")} />
                                  {role.equip.weapon?.id && (
                                    <span className={"rc-weapon-stack" + (slot.roles.length > 1 && slot.roles[1]?.equip.weapon?.id ? " has-alt" : "")}>
                                      {slot.roles.length > 1 && slot.roles[1]?.equip.weapon?.id && (
                                        <img className="rc-weapon-icon rc-weapon-behind"
                                          src={itemUrl(slot.roles[1].equip.weapon!.id)} alt=""
                                          onError={imgRetry(img => { img.style.opacity = "0"; })} />
                                      )}
                                      <img className="rc-weapon-icon"
                                        src={itemUrl(role.equip.weapon.id)} alt=""
                                        onError={imgRetry(img => { img.style.opacity = "0.15"; })} />
                                    </span>
                                  )}
                                  <span className="rc-name">{role.name || t("noNamePlaceholder")}</span>
                                  {slot.roles.length > 1 && (
                                    <span className="rc-flex-badge"
                                      style={{
                                        color: chipColor ?? "var(--muted)",
                                        borderColor: (chipColor ?? "#888888") + "55",
                                      }}
                                      title={`${t("cbBuildsCountTitle")}: ${slot.roles.map(r => r.name || "—").join(" · ")}`}>
                                      <i className="ti ti-stack-2" aria-hidden /> {slot.roles.length}
                                    </span>
                                  )}
                                  {role.equip_loaded && (
                                    <div className="rc-head-strip" style={{ marginLeft: 6, flexShrink: 0 }}>
                                      <EquipStrip equip={role.equip} weaponIs2H={is2H(role.equip.weapon?.id)} />
                                    </div>
                                  )}
                                </>
                              ) : (
                                <>
                                  <span className="rc-fn-dot" style={{ background: "transparent" }} />
                                  <span className="chip-empty" style={{ fontSize: 12 }}>{t("noRoleAssigned")}</span>
                                </>
                              )}
                              {/* Remove slot — no header, à direita. stopPropagation
                                  pra não disparar toggleCard. */}
                              <button className="cs-xbtn rc-card-remove"
                                onClick={e => { e.stopPropagation(); removeSlot(pi, si); }}
                                title={t("removeFromCompTitle")}>
                                <i className="ti ti-x" aria-hidden />
                              </button>
                            </div>

                            {/* Conteúdo expandido inline (accordion) — só a role
                                aberta renderiza. Substitui o antigo painel direito. */}
                            {isSelected && renderCardDetail(pi, si)}
                          </div>
                        );
                      })}

                    {/* Add role — 1 clique cria role vazia e abre o painel.
                        Copiar de outra role virou botão secundário (addCopyMenu). */}
                    {addCopyMenu === pi && (() => {
                      const seen = new Set<string>();
                      const pickableSlots: { fn: string | null; roles: DraftRole[]; primary: DraftRole }[] = [];
                      for (const p of (draft?.parties ?? [])) {
                        for (const s of p.slots) {
                          const primary = s.roles[0];
                          if (!primary) continue;
                          const key = roleIdentityKey(primary);
                          if (!seen.has(key) && (primary.catalog_id != null || primary.name.trim())) {
                            seen.add(key);
                            pickableSlots.push({ fn: s.fn, roles: s.roles, primary });
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
                                const copiedRoles: DraftRole[] = JSON.parse(JSON.stringify(item.roles));
                                copiedRoles.forEach(r => { r.catalog_id = null; });
                                upd(d => ({
                                  ...d,
                                  parties: d.parties.map((p, i) =>
                                    i !== pi ? p : { ...p, slots: [...p.slots, { fn: item.fn, roles: copiedRoles }] }
                                  ),
                                }));
                                setOpenCard([pi, newSlotIdx]);
                                setAddCopyMenu(null);
                              }}>
                              {item.primary.equip.weapon?.id
                                ? <img src={itemUrl(item.primary.equip.weapon.id)} alt=""
                                    style={{ width: 20, height: 20, flexShrink: 0 }}
                                    onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                : <span style={{ width: 20, flexShrink: 0 }} />}
                              <span className="flex-picker-name">{item.primary.name || t("noNamePlaceholder")}</span>
                              {item.fn && (() => { const ft = getFnDef(item.fn, fnTypes); return ft ? (
                                <span className="rc-fn-badge" style={{ background: ft.color + "25", color: ft.color, flexShrink: 0 }}>
                                  {fnLabel(ft)}
                                </span>
                              ) : null; })()}
                              {item.roles.length > 1 && (
                                <span style={{ fontSize: 10, color: "var(--muted)", flexShrink: 0 }}>
                                  +{item.roles.length - 1} flex
                                </span>
                              )}
                              {item.primary.equip_loaded && <EquipStrip equip={item.primary.equip} />}
                            </button>
                          ))}
                          {!pickableSlots.length && (
                            <span className="flex-picker-empty">{t("noRoleInComp")}</span>
                          )}
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
                            const primaries = (draft?.parties ?? []).flatMap(p => p.slots.map(s => s.roles[0]).filter(Boolean));
                            ensurePickableRolesLoaded(primaries);
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

          {/* Add party */}
          <button className="party-col-add" onClick={addParty}>
            <i className="ti ti-plus" aria-hidden /> {t("newPartyBtn")}
          </button>
          </div>
        </div>
      </div>
    </div>
  );
}