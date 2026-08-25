import { useT, type TKey } from "../i18n";

// Banner de afiliado ExitLag — leaderboard 728x90, usando assets oficiais
// servidos localmente (não hotlink), então adblock não bloqueia.
// Background: imagem de campanha oficial do ExitLag (CDN deles).
// Logo: SVG oficial com wordmark "EXITLAG" branco/vermelho.
//
// Sem texto "afiliado" — fica visualmente idêntico a um anúncio de display.
// Quando AdSense for aprovado, esse banner fica como redundância abaixo do
// leaderboard do AdSense (anti-adblock).

interface AffiliateAd {
  id: string;
  name: string;
  headline: TKey;
  desc: TKey;
  cta: TKey;
  url: string;
  bg: string;       // URL local do background
  logo: string;     // URL local do logo SVG
  accent: string;   // cor do CTA
}

const ADS: AffiliateAd[] = [
  {
    id: "exitlag",
    name: "ExitLag",
    headline: "affExitlagHeadline",
    desc: "affExitlagDesc",
    cta: "affCta",
    url: "https://www.exitlag.com/refer/10344555",
    bg: "/aff/exitlag-bg.webp",
    logo: "/aff/exitlag-logo.svg",
    accent: "#C42121",
  },
];

export default function AffiliateBanner() {
  const t = useT();
  const ad = ADS[0];
  if (!ad) return null;

  return (
    <div className="aff-leaderboard">
      <a href={ad.url} target="_blank" rel="noopener sponsored" className="aff-leaderboard-link">
        <div className="aff-leaderboard-inner" style={{ backgroundImage: `url(${ad.bg})` }}>
          {/* Esquerda: logo + copy */}
          <div className="aff-leaderboard-text">
            <img src={ad.logo} alt={ad.name} className="aff-leaderboard-logo" />
            <span className="aff-leaderboard-headline">{t(ad.headline)}</span>
            <span className="aff-leaderboard-desc">{t(ad.desc)}</span>
          </div>

          {/* Direita: CTA */}
          <span className="aff-leaderboard-cta" style={{ background: ad.accent }}>
            {t(ad.cta)}
          </span>
        </div>
      </a>
    </div>
  );
}
