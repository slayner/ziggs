import { useEffect, useState } from "react";
import { useT } from "../i18n";
import { adsConsent, CONSENT_EVENT } from "./CookieConsent";

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

// Tamanhos padrão IAB — o que a rede de anúncio realmente entrega. Antes o
// AdBanner só fixava a ALTURA e deixava a largura esticar 100% do pai, que
// nunca bate com o retângulo real de nenhuma rede (AdSense inclusive).
export type AdVariant = "leaderboard" | "mediumRectangle" | "largeRectangle" | "skyscraper" | "mobileBanner";

const AD_SIZES: Record<AdVariant, { w: number; h: number }> = {
  leaderboard: { w: 728, h: 90 },
  mediumRectangle: { w: 300, h: 250 },
  largeRectangle: { w: 336, h: 280 },
  skyscraper: { w: 160, h: 600 },
  mobileBanner: { w: 320, h: 50 },
};

const ADS_CLIENT = import.meta.env.VITE_ADSENSE_CLIENT as string | undefined;
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

declare global {
  interface Window { adsbygoogle?: unknown[] }
}

// index.html não tem como incluir o script condicionalmente (sem template
// engine — só troca %VITE_X% por string fixa, quebraria a URL se a env var
// não existisse), então o loader entra sob demanda aqui: só quando um slot
// AO VIVO de verdade monta (ADS_CLIENT setado, fora de dev), uma vez só.
let adsenseLoaderInjected = false;
function ensureAdsenseLoader(): void {
  if (adsenseLoaderInjected || !ADS_CLIENT) return;
  adsenseLoaderInjected = true;
  const s = document.createElement("script");
  s.async = true;
  s.crossOrigin = "anonymous";
  s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADS_CLIENT}`;
  document.head.appendChild(s);
}

// <ins> isolado num componente próprio, remontado via key={variant} pelo
// chamador — o AdSense injeta conteúdo direto no <ins> depois do push() e
// nunca mais "re-renderiza" ele; um <ins> que sobrevive a uma troca de
// variante (breakpoint mobile) fica com o tamanho/formato antigo pra sempre.
function LiveAdSlot({ variant, slot }: { variant: AdVariant; slot?: string }) {
  const { w, h } = AD_SIZES[variant];
  useEffect(() => {
    ensureAdsenseLoader();
    try {
      (window.adsbygoogle = window.adsbygoogle || []).push({});
    } catch {
      // AdSense ainda não carregou ou foi bloqueado — a UI de adblock cobre o caso comum
    }
  }, []);
  return (
    <ins
      className="adsbygoogle"
      style={{ display: "inline-block", width: w, height: h }}
      data-ad-client={ADS_CLIENT}
      data-ad-slot={slot}
    />
  );
}

// Slot de anúncio dimensionado por variante (tamanhos padrão IAB), com
// fallback pra mobile (`mobileVariant`, resolvido por media query). Em dev ou
// sem VITE_ADSENSE_CLIENT configurado, mostra um placeholder do tamanho REAL
// (dá pra validar o layout sem depender do AdSense estar rodando); adblock
// detectado troca pro pedido de desativar; em prod com client configurado,
// renderiza o <ins class="adsbygoogle"> de verdade (loader injetado sob
// demanda, ver ensureAdsenseLoader).
export default function AdBanner({ variant, mobileVariant, slot }: {
  variant: AdVariant; mobileVariant?: AdVariant; slot?: string;
}) {
  const t = useT();
  const blocked = useAdblockDetected();
  const isMobile = useIsMobile();
  const [consent, setConsent] = useState(adsConsent);
  useEffect(() => {
    const update = () => setConsent(adsConsent());
    window.addEventListener(CONSENT_EVENT, update);
    return () => window.removeEventListener(CONSENT_EVENT, update);
  }, []);
  const resolved = isMobile && mobileVariant ? mobileVariant : variant;
  const { w, h } = AD_SIZES[resolved];
  // Sem consentimento ("all") → não carrega o script do AdSense nem cookies
  // de terceiro. Mostra placeholder. LGPD/EEA compliance.
  const live = !import.meta.env.DEV && !!ADS_CLIENT && consent === "all";

  return (
    <div className="ad-slot">
      <div className="ad-box" style={{ width: w, height: h }}>
        {blocked === null ? null : blocked ? (
          <div className="ad-slot-blocked">
            <i className="ti ti-shield-off" aria-hidden="true" />
            <span>{t("adblockMessage")}</span>
          </div>
        ) : live ? (
          <LiveAdSlot key={resolved} variant={resolved} slot={slot} />
        ) : (
          <div className="ad-slot-placeholder">{t("adPlaceholder")} ({w}×{h})</div>
        )}
      </div>
    </div>
  );
}
