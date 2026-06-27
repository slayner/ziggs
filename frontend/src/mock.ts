import type { ApiComp, ApiRole } from "./api";

// Fallback usado SÓ se o backend estiver offline. Mesmo formato da API.
function role(p: Partial<ApiRole> & { id: number; name: string }): ApiRole {
  return {
    weapon_id: null, invisible_function: null, offhand: null, helmet: null,
    armor: null, boots: null, cape: null, food: null, abilities: null,
    play_style: null, obs: null, ...p,
  };
}

const bruxa = role({ id: 1, name: "Bruxa", invisible_function: "tank",
  helmet: "8.4 Capuz de Aço", armor: "8.4 Armadura de Guardião",
  boots: "8.4 Botas de Aço", cape: "8.3 Capa de Lymhurst",
  food: "8.0 Ensopado de Carne", play_style: "Segura a frente" });
const locus = role({ id: 2, name: "Locus", invisible_function: "support",
  helmet: "8.4 Capuz do Asceta", armor: "8.4 Túnica do Clérigo",
  boots: "8.4 Sandálias do Clérigo", cape: "8.3 Capa de Thetford",
  food: "8.0 Torta de Peixe", play_style: "Fica atrás do grupo" });
const oculto = role({ id: 3, name: "Oculto", invisible_function: "support" });
const sagrado = role({ id: 4, name: "Cajado Sagrado", invisible_function: "healer",
  helmet: "8.4 Capuz do Clérigo", armor: "8.4 Túnica do Clérigo",
  boots: "8.4 Sandálias do Clérigo", cape: "8.3 Capa de Martlock",
  food: "8.0 Sopa de Algas", play_style: "Cura a zerg" });
const aguia = role({ id: 5, name: "Águia", invisible_function: "dps_ranged",
  helmet: "8.4 Capuz de Mago", armor: "8.4 Túnica de Mago",
  boots: "8.4 Sapatos de Mago", cape: "8.3 Capa de Bridgewatch",
  food: "8.0 Omelete", play_style: "Foca aglomerados" });

let sid = 0;
const slot = (label: string, ...roles: ApiRole[]) => ({
  id: ++sid, position: 0, label, notes: null, roles,
});

export const MOCK_API_COMP: ApiComp = {
  id: 1, name: "ZvZ Padrão (offline)", description: "fallback", archived: false,
  parties: [
    { id: 1, position: 0, name: "Party 1", slots: [
      slot("Tank", bruxa), slot("Suporte (flex)", locus, oculto),
      slot("Healer", sagrado), slot("DPS", aguia),
    ] },
    { id: 2, position: 1, name: "Party 2", slots: [
      slot("Tank", bruxa), slot("Suporte (flex)", oculto, locus),
      slot("Healer", sagrado), slot("DPS (flex)", aguia, oculto),
    ] },
  ],
};
