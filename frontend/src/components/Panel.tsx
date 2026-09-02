import { useState, type ReactNode } from "react";

// ── Primitivas do design v2 "war room" ─────────────────────────────────────
// Nascidas na home (Dashboard) e compartilhadas com as demais páginas — ver
// docs/PLANO-DESIGN-V2.md. Estilos em styles.css, seção "Início (dashboard
// v2)". O `dash-root` (grid de fundo) vive no wrapper de conteúdo do App
// (WS5); o esquadramento é global no styles.css.

const GLOW_PERIOD_S = 7; // igual à duração de dash-glow-breathe no CSS

// Painel com 2 cantos fixos (TL+BR, via ::before/::after no CSS) que douram
// no hover, e um brilho radial em cada canto respirando entre baixo e alto —
// animação CSS pura (só opacity, GPU), fluida por construção. O delay
// aleatório por painel dessincroniza os quadrantes; guardado em useState pra
// não sortear de novo a cada re-render (o brilho "pularia" toda vez que o
// pai atualizasse estado). Use em containers de seção; nunca em itens de
// lista (invariante do plano).
export function Panel({ className = "", children }: { className?: string; children: ReactNode }) {
  const [delays] = useState(() => [Math.random() * GLOW_PERIOD_S, Math.random() * GLOW_PERIOD_S]);
  return (
    <div className={`dash-panel ${className}`}>
      <span className="dash-cglowwrap" aria-hidden>
        <span className="dash-cglow dash-cglow-tl" style={{ animationDelay: `-${delays[0].toFixed(2)}s` }} />
        <span className="dash-cglow dash-cglow-br" style={{ animationDelay: `-${delays[1].toFixed(2)}s` }} />
      </span>
      {children}
    </div>
  );
}

// Header editorial dos painéis: título uppercase + régua + ação/filtros à
// direita.
export function PanelHeader({ title, tooltip, action, onAction, children }: {
  title: string; tooltip?: string;
  action?: string; onAction?: () => void; children?: ReactNode;
}) {
  return (
    <div className="dash-panel-h">
      <h2 className="dash-panel-title">{title}</h2>
      {tooltip && <i className="ti ti-info-circle text-zinc-600" title={tooltip} />}
      <span className="dash-panel-rule" />
      {children}
      {action && (
        <button onClick={onAction} className="dash-panel-link">{action}</button>
      )}
    </div>
  );
}
