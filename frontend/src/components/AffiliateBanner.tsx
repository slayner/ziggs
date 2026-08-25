import { useState } from "react";
import { useT, type TKey } from "../i18n";

// Affiliate banner — estilo ad tradicional, largura máxima do conteúdo,
// imagem de fundo com overlay + CTA. Posicionado antes do footer, fora
// do fluxo das ferramentas, então não interfere no uso do site.
//
// Cada visita mostra um banner aleatório dos disponíveis. Ao clicar abre
// em nova aba com rel=noopener+sponsored (boa prática FTC/SEO).

interface AffiliateAd {
  id: string;
  name: string;
  headline: TKey;      // chave i18n: frase curta chamativa
  desc: TKey;          // chave i18n: uma linha de apoio
  ctaLabel: TKey;      // chave i18n: texto do botão
  url: string;         // link de afiliado real
  gradient: string;    // gradiente CSS de fundo
  accent: string;      // cor do CTA e elementos de destaque
}

// Banners ativos. Adicionar/remover conforme programas forem aprovados.
// gradient + accent definem o visual; o banner usa essas cores como base.
const BANNERS: AffiliateAd[] = [
  {
    id: "exitlag",
    name: "ExitLag",
    headline: "affExitlagHeadline",
    desc: "affExitlagDesc",
    ctaLabel: "affExitlagCta",
    url: "https://www.exitlag.com/refer/10344555",
    gradient: "linear-gradient(135deg, #1a0d05 0%, #2d1508 50%, #1a0d05 100%)",
    accent: "#ff6b2b",
  },
  // Placeholders — preencher quando os links estiverem aprovados:
  // {
  //   id: "nordvpn",
  //   name: "NordVPN",
  //   headline: "affNordvpnHeadline",
  //   desc: "affNordvpnDesc",
  //   ctaLabel: "affNordvpnCta",
  //   url: "https://nordvpn.com",
  //   gradient: "linear-gradient(135deg, #0d1a2e 0%, #1a2d4a 50%, #0d1a2e 100%)",
  //   accent: "#4687ff",
  // },
  // {
  //   id: "surfshark",
  //   name: "Surfshark",
  //   headline: "affSurfsharkHeadline",
  //   desc: "affSurfsharkDesc",
  //   ctaLabel: "affSurfsharkCta",
  //   url: "https://surfshark.com",
  //   gradient: "linear-gradient(135deg, #0d2018 0%, #1a3d2e 50%, #0d2018 100%)",
  //   accent: "#2ee2a8",
  // },
];

export default function AffiliateBanner() {
  const t = useT();
  // Índice fixo por sessão (não rotaciona — cada visita mostra um banner).
  // const [idx] = useState(() => Math.floor(Math.random() * BANNERS.length));
  // Por agora, só ExitLag está ativo.
  const [idx] = useState(0);
  const ad = BANNERS[idx % BANNERS.length];
  if (!ad) return null;

  return (
    <div className="aff-banner" style={{ background: ad.gradient, borderLeftColor: ad.accent }}>
      {/* Overlay decorativo — linhas diagonais sutis */}
      <div className="aff-banner-overlay" aria-hidden="true" />

      <div className="aff-banner-inner">
        {/* Lado esquerdo: nome + mensagem */}
        <div className="aff-banner-info">
          <span className="aff-banner-brand" style={{ color: ad.accent }}>{ad.name}</span>
          <h3 className="aff-banner-headline">{t(ad.headline)}</h3>
          <p className="aff-banner-desc">{t(ad.desc)}</p>
        </div>

        {/* Lado direito: CTA */}
        <a
          href={ad.url}
          target="_blank"
          rel="noopener sponsored"
          className="aff-banner-btn"
          style={{ background: ad.accent }}
        >
          {t(ad.ctaLabel)}
        </a>
      </div>

      {/* Badge discreto no canto — disclosure, obrigatório por lei */}
      <span className="aff-banner-tag">afiliado</span>
    </div>
  );
}
