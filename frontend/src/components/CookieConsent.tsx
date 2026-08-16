import { useState } from "react";
import { useT } from "../i18n";

// Banner de consentimento de cookies (restaurado ago/2026 — existia e foi
// perdido num sync; o AdSense/UE exige consentimento pra cookies de anúncio).
// Leve: grava em localStorage, sem CMP de terceiros.
//
// Valores: "all" (aceitou tudo, anúncios personalizados) | "nec" (só
// necessários, anúncios NÃO personalizados) | null (ainda não decidiu).

const KEY = "ziggs-cookie-consent";
export const CONSENT_EVENT = "ziggs:consent";

export function adsConsent(): "all" | "nec" | null {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "all" || v === "nec") return v;
  } catch { /* localStorage bloqueado — trata como sem decisão */ }
  return null;
}

export default function CookieConsent() {
  const t = useT();
  const [visible, setVisible] = useState(() => adsConsent() === null);

  if (!visible) return null;

  function choose(v: "all" | "nec") {
    try { localStorage.setItem(KEY, v); } catch { /* segue sem persistir */ }
    window.dispatchEvent(new Event(CONSENT_EVENT));
    setVisible(false);
  }

  return (
    <div className="cookie-consent" role="dialog" aria-label={t("consentTitle")}>
      <div className="cookie-consent-inner">
        <div className="cookie-consent-text">
          <i className="ti ti-cookie" aria-hidden="true" />
          <span>
            {t("consentText")}{" "}
            <a href="/cookies" className="cookie-consent-link">{t("consentLearn")}</a>
          </span>
        </div>
        <div className="cookie-consent-actions">
          <button className="btn cookie-consent-nec" onClick={() => choose("nec")}>
            {t("consentNec")}
          </button>
          <button className="btn cookie-consent-all" onClick={() => choose("all")}>
            {t("consentAll")}
          </button>
        </div>
      </div>
    </div>
  );
}
