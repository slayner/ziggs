import { useEffect, useState } from "react";
import { api, type Permissions, type WeaponOut } from "../api";
import { useT } from "../i18n";

import { CompEditor } from "./comp/CompEditor";
import { CompList } from "./comp/CompList";
import { EquipGrid } from "./comp/EquipGrid";
import { PriceHistoryChart } from "./comp/PriceHistoryChart";
import type { CompCode, Draft, DraftEquip } from "./comp/types";

export type { DraftEquip };
export { EquipGrid, PriceHistoryChart };

// Container fino: decide entre a lista de comps e o editor de uma comp
// específica. Todo o estado de edição (draft, undo, fn-types, etc.) vive em
// CompEditor — aqui só o que precisa sobreviver à troca lista↔editor
// (compList, offline, weapons, cache de armas por sessão).
export default function CompBuilder({ perms, onOpenChange }: {
  perms: Permissions;
  // Avisa o pai (ManagementPage) se uma comp está aberta pra edição/visualização
  // — ele usa isso pra esconder a barra de abas lateral e dar a largura toda
  // pro editor (o master-detail já é largo, não cabe espremido).
  onOpenChange?: (open: boolean) => void;
}) {
  const t = useT();
  const [compList, setCompList] = useState<{ id: number; name: string }[] | null>(null);
  const [offline, setOffline] = useState(false);
  const [weapons, setWeapons] = useState<WeaponOut[]>([]);
  const [active, setActive] = useState<{ id: number; draft: Draft; importCode: CompCode | null } | null>(null);

  useEffect(() => {
    api.listComps()
      .then(list => setCompList(list))
      .catch(() => { setCompList([{ id: 1, name: t("demoCompName") }]); setOffline(true); });
    api.listWeapons().then(setWeapons).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    onOpenChange?.(active !== null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active !== null]);

  if (!active) {
    return (
      <CompList perms={perms} offline={offline} compList={compList} setCompList={setCompList}
        onOpen={(id, draft, _startEditing, importCode) => setActive({ id, draft, importCode: importCode ?? null })} />
    );
  }

  return (
    <CompEditor initialDraft={active.draft} initialImportCode={active.importCode}
      perms={perms} offline={offline} weapons={weapons}
      onBack={() => setActive(null)} />
  );
}
