import { useT } from "../i18n";

interface Props {
  onDemo: () => void;
}

// Login SÓ por Discord. O botão real manda pro backend (/auth/discord/login);
// o "modo demonstração" deixa ver o site sem backend/Postgres no ar.
export default function Login({ onDemo }: Props) {
  const t = useT();
  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="logo-big">
          <i className="ti ti-shield-half" aria-hidden="true" />
        </div>
        <h1 style={{ fontSize: 24, margin: 0 }}>Ziggs</h1>
        <p className="muted" style={{ fontSize: 13, margin: "6px 0 22px" }}>
          {t("loginTagline")}
        </p>

        <a className="btn discord" href="/auth/discord/login">
          <i className="ti ti-brand-discord" style={{ fontSize: 20 }} aria-hidden="true" />
          {t("loginDiscord")}
        </a>

        <button
          className="btn"
          style={{ width: "100%", justifyContent: "center", marginTop: 10 }}
          onClick={onDemo}
        >
          <i className="ti ti-eye" aria-hidden="true" />
          {t("loginDemo")}
        </button>

        <p className="hint" style={{ marginTop: 16 }}>
          {t("loginHint")}
        </p>
      </div>
    </div>
  );
}
