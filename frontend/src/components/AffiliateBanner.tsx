import { useT, type TKey } from "../i18n";

// Banner de afiliado ExitLag — usa assets oficiais servidos localmente
// (não hotlink), então adblock não bloqueia.
//
// Dois modos:
//   "leaderboard" — 728x90, texto + CTA na horizontal (top da página)
//   "rectangle"   — 300x250, texto + CTA empilhados verticalmente (rail/sidebar)
//
// Substitui os antigos iframes do Adsterra (/ad/300x250.html e /ad/728x90.html)
// que estavam vazios desde que abandonamos o Adsterra.

interface AffiliateAd {
  id: string;
  name: string;
  headline: TKey;
  desc: TKey;
  cta: TKey;
  url: string;
  bg: string;
  logo: string;
  accent: string;
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

type Variant = "leaderboard" | "rectangle";

export default function AffiliateBanner({ variant = "leaderboard" }: { variant?: Variant } = {}) {
  const t = useT();
  const ad = ADS[0];
  if (!ad) return null;

  if (variant === "rectangle") {
    return (
      <div className="aff-rect">
        <a href={ad.url} target="_blank" rel="noopener sponsored" className="aff-rect-link">
          <div className="aff-rect-bg" style={{ backgroundImage: `url(${ad.bg})` }} />
          <div className="aff-rect-content">
            <img src={ad.logo} alt={ad.name} className="aff-rect-logo" />
            <span className="aff-rect-headline">{t(ad.headline)}</span>
            <span className="aff-rect-desc">{t(ad.desc)}</span>
            <span className="aff-rect-cta" style={{ background: ad.accent }}>{t(ad.cta)}</span>
          </div>
        </a>
      </div>
    );
  }

  return (
    <div className="aff-leaderboard">
      <a href={ad.url} target="_blank" rel="noopener sponsored" className="aff-leaderboard-link">
        <div className="aff-leaderboard-inner" style={{ backgroundImage: `url(${ad.bg})` }}>
          <div className="aff-leaderboard-text">
            <img src={ad.logo} alt={ad.name} className="aff-leaderboard-logo" />
            <span className="aff-leaderboard-headline">{t(ad.headline)}</span>
            <span className="aff-leaderboard-desc">{t(ad.desc)}</span>
          </div>
          <span className="aff-leaderboard-cta" style={{ background: ad.accent }}>
            {t(ad.cta)}
          </span>
        </div>
      </a>
    </div>
  );
}