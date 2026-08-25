import { useEffect, useMemo, useRef, useState } from "react";
import { useT, type TKey } from "../i18n";

// Affiliate banner rotativo. Cada visita mostra um anúncio aleatório,
// ciclando entre os disponíveis. Links são abertos em nova aba (target=_blank)
// com rel=noopener+sponsored (boa prática + FTC).
//
// Não precisa de aprovação de rede — os links vão direto pro programa de
// afiliado (ExitLag, NordVPN, etc.). Banners de display (AdSense) podem
// coexistir: o AffiliateBanner é mais leve, mais relevante pro nicho, e não
// depende de aprovação.

interface AffiliateAd {
  id: string;
  name: string;
  icon: string;      // tabler icons class (e.g. "ti-shield")
  title: TKey;       // chave do i18n
  desc: TKey;        // chave do i18n
  url: string;
  accent: string;    // cor de destaque borda/fundo
}

// Links de afiliado — preencher quando as contas forem aprovadas.
// URLs placeholder redirecionam pra homepage do produto sem tracking.
// Quando tiver os links de afiliado reais, substituir aqui.
const AFFILIATES: AffiliateAd[] = [
  {
    id: "exitlag",
    name: "ExitLag",
    icon: "ti-bolt",
    title: "affExitlagTitle",
    desc: "affExitlagDesc",
    url: "https://www.exitlag.com/refer/10344555",
    accent: "#ff6b2b",
  },
  {
    id: "nordvpn",
    name: "NordVPN",
    icon: "ti-shield",
    title: "affNordvpnTitle",
    desc: "affNordvpnDesc",
    url: "https://nordvpn.com",
    accent: "#4687ff",
  },
  {
    id: "surfshark",
    name: "Surfshark",
    icon: "ti-wifi",
    title: "affSurfsharkTitle",
    desc: "affSurfsharkDesc",
    url: "https://surfshark.com",
    accent: "#2ee2a8",
  },
];

// Embaralha o array Fisher-Yates pra cada visita mostrar uma ordem diferente.
function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export default function AffiliateBanner() {
  const t = useT();
  // Uma ordem aleatória por montagem (não muda durante a sessão).
  const ads = useMemo(() => shuffle(AFFILIATES), []);
  const [idx, setIdx] = useState(0);
  const ad = ads[idx % ads.length];

  // Rotação automática a cada 8s. Reseta se o usuário clicar.
  const timer = useRef<number>();
  useEffect(() => {
    timer.current = window.setInterval(() => setIdx(i => i + 1), 8000);
    return () => window.clearInterval(timer.current);
  }, []);

  if (AFFILIATES.length === 0) return null;

  return (
    <div className="aff-banner" style={{ borderLeftColor: ad.accent }}>
      <div className="aff-banner-body">
        <span className="aff-banner-icon" style={{ color: ad.accent }}>
          <i className={`ti ${ad.icon}`} aria-hidden="true" />
        </span>
        <div className="aff-banner-text">
          <span className="aff-banner-name">{ad.name}</span>
          <span className="aff-banner-title">{t(ad.title)}</span>
          <span className="aff-banner-desc">{t(ad.desc)}</span>
        </div>
      </div>
      <a
        href={ad.url}
        target="_blank"
        rel="noopener sponsored"
        className="aff-banner-cta"
        onClick={() => {
          // Reseta o timer quando o usuário clica (dá um tempo extra pra ler).
          window.clearInterval(timer.current);
          timer.current = window.setInterval(() => setIdx(i => i + 1), 8000);
        }}
      >
        {t("affCta")}
      </a>
    </div>
  );
}
