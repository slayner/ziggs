export interface Role {
  id: string;
  name: string;
  fn: string; // função invisível (Tank, Suporte, Healer, DPS...)
  weapon?: string;
  offhand?: string;
  helmet?: string;
  armor?: string;
  boots?: string;
  cape?: string;
  food?: string;
  abilities?: string;
  style?: string;
  /** campos que o motor de sugestão consegue preencher para esta função */
  sug?: string[];
}

export interface Slot {
  label: string;
  roleIds: string[]; // várias funções = slot flexível
}

export interface Party {
  name: string;
  slots: Slot[];
}

export interface Comp {
  name: string;
  parties: Party[];
}
