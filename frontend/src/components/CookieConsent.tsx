import { useState } from "react";
import { useT } from "../i18n";

// Banner de consentimento de cookies. Leve: grava em localStorage, sem CMP
// de terceiros. O AdBanner lê `adsConsent()` antes de carregar o script do
// AdSense — sem consentimento, só serve placeholder/não-personalizado.
//
// Valores: "all" (aceitou tudo, anúncios personalizados) | "nec" (só
// necessários, sem cookies de terceiro) | null (ainda não decidiu).

const KEY = "ziggs-cookie-consent";
export const CONSENT_EVENT = "ziggs:consent";

export function adsConsent(): "all" | "nec" | null {
  const v = localStorage.getItem(KEY);
  if (v === "all" || v === "nec") return v;
  return null;
}

export default function CookieConsent() {
  const t = useT();
  const [visible, setVisible] = useState(() => adsConsent() === null);

  if (!visible) return null;

  function choose(v: "all" | "nec") {
    localStorage.setItem(KEY, v);
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
