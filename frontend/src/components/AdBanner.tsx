import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import { adsConsent, CONSENT_EVENT } from "./CookieConsent";

// Google AdSense. Diferente da Adsterra (iframe + invoke.js), o AdSense é uma
// tag <ins> no DOM da própria página + push no array window.adsbygoogle.
//
// ⚠️ CONTA AINDA NÃO APROVADA: os data-ad-slot abaixo só veiculam depois da
// aprovação. Enquanto ADSENSE_SLOTS não tiver o ID da unidade criada no painel
// (AdSense → Anúncios → Por unidade de anúncio → Display), o slot NÃO renderiza
// (nada de placeholder ocupando espaço — a página fica limpa pra revisão).
//
// A aprovação em si não depende das unidades: o Google avalia o SITE (script
// global no index.html + conteúdo real). Preencher os slots é o passo seguinte
// à aprovação.
const ADSENSE_CLIENT = "ca-pub-5616694158578299";

// slot ID do AdSense por posição — preencher após criar as unidades no painel.
// Chave = prop `slot` do AdBanner (posição na página), não o tamanho: o AdSense
// serve tamanho responsivo pelo formato horizontal/rectangle.
const ADSENSE_SLOTS: Record<string, string> = {
  // "top-dashboard": "",
  // "top-battles": "",
  // "dashboard": "",
  // "craft": "",
  // "management": "",
};

declare global {
  interface Window { adsbygoogle?: unknown[] }
}

// Tamanhos padrão IAB — reservam o espaço ANTES do anúncio carregar (evita
// layout shift, que o AdSense penaliza via Core Web Vitals).
export type AdVariant = "leaderboard" | "mediumRectangle" | "largeRectangle" | "skyscraper" | "mobileBanner";

const AD_SIZES: Record<AdVariant, { w: number; h: number; format: string }> = {
  leaderboard: { w: 728, h: 90, format: "horizontal" },
  mediumRectangle: { w: 300, h: 250, format: "rectangle" },
  largeRectangle: { w: 336, h: 280, format: "rectangle" },
  skyscraper: { w: 160, h: 600, format: "vertical" },
  mobileBanner: { w: 320, h: 50, format: "horizontal" },
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

// O <ins> do AdSense. O push no adsbygoogle precisa acontecer UMA vez por ins
// depois dele entrar no DOM — em React, no useEffect com guarda de duplicidade
// (StrictMode dev monta 2×; push duplo lança exceção "All ins elements...").
//
// CONSENTIMENTO (GDPR/UE): "nec" (só necessários) → ativa anúncios NÃO
// personalizados (NPA) ANTES do primeiro push; "all" → personalizados; null
// (ainda não decidiu) → o banner não renderiza anúncio nenhum. A escolha é
// reavaliada quando o banner de consentimento emite CONSENT_EVENT.
function AdIns({ slotId, variant }: { slotId: string; variant: AdVariant }) {
  const { format } = AD_SIZES[variant];
  const ref = useRef<HTMLModElement>(null);
  const pushed = useRef(false);
  const [consent, setConsent] = useState<"all" | "nec" | null>(() => adsConsent());
  useEffect(() => {
    const onChange = () => setConsent(adsConsent());
    window.addEventListener(CONSENT_EVENT, onChange);
    return () => window.removeEventListener(CONSENT_EVENT, onChange);
  }, []);
  useEffect(() => {
    if (pushed.current || consent === null) return;
    pushed.current = true;
    if (consent === "nec") {
      // NPA: sinaliza anúncios não personalizados na queue do AdSense
      // (requestNonPersonalizedAds = 1) sem depender de tipagem do array.
      const q = (window.adsbygoogle = window.adsbygoogle || []) as unknown as { requestNonPersonalizedAds?: number };
      q.requestNonPersonalizedAds = 1;
    }
    try {
      (window.adsbygoogle = window.adsbygoogle || []).push({});
    } catch {
      /* script do AdSense ainda não carregou — ele mesmo processa o queue */
    }
  }, [consent]);
  if (consent === null) return null;
  return (
    <ins
      ref={ref}
      className="adsbygoogle"
      style={{ display: "block" }}
      data-ad-client={ADSENSE_CLIENT}
      data-ad-slot={slotId}
      data-ad-format={format}
      data-full-width-responsive="true"
    />
  );
}

// Slot de anúncio do AdSense. `slot` é o id da POSIÇÃO (chave em ADSENSE_SLOTS)
// e também a key do React pra remount entre views. Sem slot configurado →
// null (sem reserva de espaço); adblock → pedido pra desativar.
export default function AdBanner({ slot, variant, mobileVariant }: {
  slot: string; variant: AdVariant; mobileVariant?: AdVariant;
}) {
  const t = useT();
  const blocked = useAdblockDetected();
  const isMobile = useIsMobile();
  const resolved = isMobile && mobileVariant ? mobileVariant : variant;
  const slotId = ADSENSE_SLOTS[slot];

  if (!slotId) return null;

  return (
    <div className="ad-slot">
      <div className="ad-box" style={{ minHeight: 1 }}>
        {blocked === null ? null : blocked ? (
          <div className="ad-slot-blocked">
            <i className="ti ti-shield-off" aria-hidden="true" />
            <span>{t("adblockMessage")}</span>
          </div>
        ) : (
          <AdIns key={slot} slotId={slotId} variant={resolved} />
        )}
      </div>
    </div>
  );
}
