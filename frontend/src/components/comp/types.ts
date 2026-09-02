// Tipos compartilhados do comp builder — extraído de CompBuilder.tsx (WS6 fase 1).
export type FnTypeDef = { key: string; label: string; color: string; emoji?: string };

export type EquipItem = { id: string; name: string };

export type DraftEquip = {
  weapon?: EquipItem; offhand?: EquipItem; helmet?: EquipItem;
  armor?: EquipItem;  boots?: EquipItem;  cape?: EquipItem; food?: EquipItem;
  potion?: EquipItem;
  offhand_alt?: EquipItem[]; helmet_alt?: EquipItem[]; armor_alt?: EquipItem[];
  boots_alt?: EquipItem[];   cape_alt?: EquipItem[];
};

export type DraftRole = {
  catalog_id:    number | null;
  name:          string;
  fn:            string | null;
  color:         string | null;
  weapon_db_id:  number | null;
  equip:         DraftEquip;
  equip_loaded:  boolean;
  play_style:    string | null;
  abilities:     string | null;
  obs:           string | null;
  q_spell:       string | null;
  w_spell:       string | null;
  passive_spell: string | null;
  gear_spells:   Record<string, string | null>;
  potion_qty:    number;
  food_qty:      number;
};
export type DraftSlot  = { id?: number; fn: string | null; role: DraftRole };
export type DraftParty = { name: string; slots: DraftSlot[] };
export type Draft      = { id: number; name: string; parties: DraftParty[] };

// ── Código de composição (tipo código de mira do CS) ─────────────
// Um texto que descreve a comp INTEIRA (todas as parties/slots/roles, cada
// uma com arma, gear, spells, consumíveis, play style) — copia e cola pra
// reproduzir a MESMA composição em qualquer comp, de qualquer guilda. Base64
// do JSON: nada de formato binário próprio pra manter/versionar à toa.
export type CompCodeRole = {
  name: string;
  fn: string | null;
  equip: DraftEquip;
  q_spell: string | null;
  w_spell: string | null;
  passive_spell: string | null;
  gear_spells: Record<string, string | null>;
  play_style: string | null;
  abilities: string | null;
  potion_qty: number;
  food_qty: number;
};
export type CompCodeSlot = { fn: string | null; role: CompCodeRole };
export type CompCodeParty = { name: string; slots: CompCodeSlot[] };
export type CompCode = { v: 1; parties: CompCodeParty[] };
