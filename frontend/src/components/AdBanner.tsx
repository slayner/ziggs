import { useEffect, useState } from "react";
import { useT } from "../i18n";

// Detecção de adblock via elemento-isca: classes que a maioria das listas de
// filtro (EasyList etc.) esconde via CSS. O elemento nunca é visto pelo
// usuário — só serve pra saber se um bloqueador colapsou ele (offsetHeight 0).
function useAdblockDetected(): boolean | null {
  const [blocked, setBlocked] = useState<boolean | null>(null);

  useEffect(() => {
    const bait = document.createElement("div");
    bait.className = "adsbox ad-banner adsbygoogle advertisement";
    bait.style.cssText = "position:absolute;top:-9999px;left:-9999px;width:2px;height:2px;";
    document.body.appendChild(bait);
    const timer = setTimeout(() => {
      setBlocked(bait.offsetParent === null || bait.offsetHeight === 0);
      bait.remove();
    }, 200);
    return () => { clearTimeout(timer); bait.remove(); };
  }, []);

  return blocked;
}

const SIZES = {
  leaderboard: { height: 90 },
  rectangle: { height: 250 },
} as const;

// Slot de anúncio, sempre presente (montado uma vez no shell do App e em
// alguns gutters vazios de páginas específicas — ver App.tsx/ManagementPage/
// Dashboard). Sem rede de anúncio plugada ainda: mostra um placeholder no
// lugar do <ins class="adsbygoogle"> real; adblock detectado troca pro
// pedido de desativar.
//
// Sem maxWidth próprio: preenche 100% do container (.ad-slot), que já se
// encaixa na largura de cada página (site inteiro no App.tsx/Dashboard,
// sidebar de 220px no ManagementPage) — largura vem sempre do pai, nunca
// travada aqui.
export default function AdBanner({ size = "leaderboard" }: { size?: keyof typeof SIZES }) {
  const t = useT();
  const blocked = useAdblockDetected();
  const { height } = SIZES[size];

  return (
    <div className="ad-slot" style={{ height }}>
      {blocked === null ? null : blocked ? (
        <div className="ad-slot-blocked">
          <i className="ti ti-shield-off" aria-hidden="true" />
          <span>{t("adblockMessage")}</span>
        </div>
      ) : (
        // TODO: colar aqui a tag da rede de anúncio escolhida (ex.: <ins
        // className="adsbygoogle" .../> do Google AdSense) no lugar do texto.
        <div className="ad-slot-placeholder">{t("adPlaceholder")}</div>
      )}
    </div>
  );
}
