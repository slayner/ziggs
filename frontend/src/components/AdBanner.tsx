import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n";

// Detecção de adblock via elemento-isca: classes que a maioria das listas de
// filtro (EasyList etc.) esconde via CSS. O elemento nunca é visto pelo
// usuário — só serve pra saber se um bloqueador colapsou ele (offsetHeight 0).
function useAdblockDetected(): boolean | null {
  const [blocked, setBlocked] = useState<boolean | null>(null);

  useEffect(() => {
    const bait = document.createElement("div");
    bait.className = "adsbox ad-banner advertisement";
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

// Tamanhos padrão IAB — o que a rede de anúncio realmente entrega.
export type AdVariant = "leaderboard" | "mediumRectangle" | "largeRectangle" | "skyscraper" | "mobileBanner";

const AD_SIZES: Record<AdVariant, { w: number; h: number; size: string }> = {
  leaderboard: { w: 728, h: 90, size: "728x90" },
  mediumRectangle: { w: 300, h: 250, size: "300x250" },
  largeRectangle: { w: 336, h: 280, size: "300x250" },
  skyscraper: { w: 160, h: 600, size: "300x250" },
  mobileBanner: { w: 320, h: 50, size: "728x90" },
};

const MOBILE_QUERY = "(max-width: 767px)";

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

// Chaves da Adsterra por tamanho — só 1 key por tamanho (limite da Adsterra).
// Anúncios diferentes a cada troca de aba vêm do cache-bust: o invoke.js é
// recarregado com um query param único a cada mount, forçando nova impressão.
const ADSTERRA_KEYS: Record<string, string> = {
  "728x90": "349d923ad542f5d656d1fcfb46f22eb6",
  "300x250": "67b53d8ceb5bbe360fbf869679d47b70",
};

// Injeta o anúncio da Adsterra dentro de um iframe srcdoc isolado. Cada
// iframe tem seu próprio window.atOptions — sem isso, 2 banners da mesma
// key na mesma página colidem no global e só o primeiro renderiza.
function AdSlot({ slot, variant }: { slot: string; variant: AdVariant }) {
  const { w, h, size } = AD_SIZES[variant];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const key = ADSTERRA_KEYS[size];
    if (!key) return;
    // Cache-bust: query param único a cada mount. O Adsterra trata como
    // impressão nova e serve um anúncio diferente do inventário.
    const bust = `${slot}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;overflow:hidden;width:${w}px;height:${h}px}</style></head><body><script>atOptions={'key':'${key}','format':'iframe','height':${h},'width':${w},'params':{}};<\/script><script src="https://www.highperformanceformat.com/${key}/invoke.js?z=${bust}" async><\/script></body></html>`;
    const iframe = document.createElement("iframe");
    iframe.srcdoc = html;
    iframe.width = String(w);
    iframe.height = String(h);
    iframe.style.cssText = `border:0;width:${w}px;height:${h}px;overflow:hidden`;
    iframe.setAttribute("loading", "eager");
    iframe.setAttribute("scrolling", "no");
    el.appendChild(iframe);
    return () => { el.innerHTML = ""; };
  }, [slot, size, w, h]);
  return <div ref={ref} style={{ width: w, height: h, overflow: "hidden" }} />;
}

// Slot de anúncio. `slot` é só um id único pra forçar remount (key do React)
// — a key da Adsterra é por tamanho, não por slot. O cache-bust no invoke.js
// faz o anúncio ser diferente a cada mount. Adblock → pedido pra desativar;
// dev → placeholder.
export default function AdBanner({ slot, variant, mobileVariant }: {
  slot: string; variant: AdVariant; mobileVariant?: AdVariant;
}) {
  const t = useT();
  const blocked = useAdblockDetected();
  const isMobile = useIsMobile();
  const resolved = isMobile && mobileVariant ? mobileVariant : variant;
  const { w, h } = AD_SIZES[resolved];
  const live = !import.meta.env.DEV;

  return (
    <div className="ad-slot">
      <div className="ad-box" style={{ width: w, height: h }}>
        {blocked === null ? null : blocked ? (
          <div className="ad-slot-blocked">
            <i className="ti ti-shield-off" aria-hidden="true" />
            <span>{t("adblockMessage")}</span>
          </div>
        ) : live ? (
          <AdSlot key={slot} slot={slot} variant={resolved} />
        ) : (
          <div className="ad-slot-placeholder">{t("adPlaceholder")} ({w}×{h})</div>
        )}
      </div>
    </div>
  );
}