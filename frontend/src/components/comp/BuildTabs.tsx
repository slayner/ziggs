import { imgRetry } from "../../api";
import { useT } from "../../i18n";
import { itemUrl } from "./helpers";
import type { DraftRole } from "./types";

// O elemento central do redesign: em vez de empilhar as builds com um "ou"
// tímido, cada build vira uma aba (ícone da arma + nome). Usada no view
// (troca a build exibida) e na edição (troca a build editada + aba "+").
export function BuildTabs({ roles, active, onSelect, onAdd, addOpen }: {
  roles: DraftRole[];
  active: number;
  onSelect: (i: number) => void;
  onAdd?: () => void;
  addOpen?: boolean;
}) {
  const t = useT();
  return (
    <div className="build-tabs" role="tablist">
      {roles.map((r, i) => (
        <button key={i} type="button" role="tab" aria-selected={active === i}
          className={"build-tab" + (active === i ? " act" : "")}
          onClick={e => { e.stopPropagation(); onSelect(i); }}
          title={i === 0 ? t("mainTabLabel") : (r.flex_of ? `${t("flexOfPrefix")} ${r.flex_of}` : "Flex")}>
          {r.equip.weapon?.id
            ? <img src={itemUrl(r.equip.weapon.id)} alt=""
                onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
            : <i className="ti ti-swords" aria-hidden style={{ opacity: 0.4, fontSize: 13 }} />}
          <span className="build-tab-name">{r.name || (i === 0 ? t("mainTabLabel") : `Flex ${i}`)}</span>
          {i === 0
            ? <i className="ti ti-star-filled build-tab-main-star" aria-hidden />
            : <span className="build-tab-flex-pill">flex</span>}
        </button>
      ))}
      {onAdd && (
        <button type="button" className={"build-tab build-tab-add" + (addOpen ? " act" : "")}
          onClick={e => { e.stopPropagation(); onAdd(); }}
          title={t("cbAddBuildTab")}>
          <i className="ti ti-plus" aria-hidden />
        </button>
      )}
    </div>
  );
}
