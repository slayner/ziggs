import { useT, type TKey } from "../i18n";

// Banner de afiliado inline — renderizado como elemento HTML estilizado
// (sem imagem carregada de servidor externo), então adblock não bloqueia.
// Quando AdSense for aprovado, esse banner pode pairar abaixo do leaderboard
// do AdSense como redundância anti-adblock.
//
// Formato: leaderboard 728x90 (padrão IAB), responsivo no mobile.

interface AffiliateAd {
  id: string;
  name: string;
  headline: TKey;
  desc: TKey;
  cta: TKey;
  url: string;
  colors: { bg: string; bgAlt: string; accent: string; text: string; btnText: string };
}

const ADS: AffiliateAd[] = [
  {
    id: "exitlag",
    name: "ExitLag",
    headline: "affExitlagHeadline",
    desc: "affExitlagDesc",
    cta: "affCta",
    url: "https://www.exitlag.com/refer/10344555",
    colors: { bg: "#1a0d05", bgAlt: "#2d1508", accent: "#ff6b2b", text: "#ffffff", btnText: "#1a0d05" },
  },
  // Adicionar outros aqui quando os links estiverem aprovados.
  // NordVPN: bg #0d1a2e, accent #4687ff
  // Surfshark: bg #0d2018, accent #2ee2a8
];

export default function AffiliateBanner() {
  const t = useT();
  const ad = ADS[0]; // ExitLag por enquanto
  if (!ad) return null;

  const c = ad.colors;
  return (
    <div className="aff-leaderboard">
      <a href={ad.url} target="_blank" rel="noopener sponsored" className="aff-leaderboard-link">
        <div
          className="aff-leaderboard-inner"
          style={{ background: `linear-gradient(90deg, ${c.bg} 0%, ${c.bgAlt} 50%, ${c.bg} 100%)` }}
        >
          {/* Marca d'água com primeira letra */}
          <span className="aff-leaderboard-watermark" style={{ color: c.accent }} aria-hidden="true">
            {ad.name.charAt(0)}
          </span>

          <div className="aff-leaderboard-text">
            <span className="aff-leaderboard-brand" style={{ color: c.accent }}>{ad.name}</span>
            <span className="aff-leaderboard-headline" style={{ color: c.text }}>{t(ad.headline)}</span>
            <span className="aff-leaderboard-desc" style={{ color: `${c.text}99` }}>{t(ad.desc)}</span>
          </div>

          <span className="aff-leaderboard-cta" style={{ background: c.accent, color: c.btnText }}>
            {t(ad.cta)}
          </span>
        </div>

        {/* Badge "afiliado" — disclosure, aparece sutil no canto */}
        <span className="aff-leaderboard-badge" style={{ color: `${c.text}33` }}>afiliado</span>
      </a>
    </div>
  );
}
