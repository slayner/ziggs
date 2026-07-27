import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n";

// Barra de progresso de carga lenta (perfil de jogador frio, guilda/aliança):
// o backend marca a etapa corrente num endpoint de load-progress e a página
// faz polling enquanto o fetch principal não volta — sem feedback a página
// parece quebrada e o usuário vai embora. São as etapas REAIS do pipeline.
// O delay de 400ms evita o flash nas cargas que resolvem na hora.
export type LoadStep = { key: string; label: string };

export default function LoadProgress({ steps, stage, retrying, hint, queue }: {
  steps: LoadStep[]; stage: string | null; retrying: boolean; hint: string;
  // Nº de requests esperando um slot do gate da Albion (rate limit 1/5s) — quando
  // > 0 a carga está PARADA na fila, não travada. Distingue "na fila" de "buscando".
  queue?: number;
}) {
  const t = useT();
  const [show, setShow] = useState(false);
  // Exibição monotônica: cargas concorrentes do mesmo perfil (StrictMode em
  // dev, dois visitantes em prod) compartilham a chave de progresso e o stage
  // pode "voltar" — a barra nunca regride, só acompanha o máximo já visto.
  // Passe `key={entidade}` no call site pra resetar ao trocar de perfil.
  const maxIdxRef = useRef(-1); // hooks todos ANTES do return condicional
  useEffect(() => {
    const id = setTimeout(() => setShow(true), 400);
    return () => clearTimeout(id);
  }, []);

  // Tempo decorrido: cargas frias/refresh são lentas (rate limit da Albion),
  // então um contador visível deixa claro que está trabalhando, não travado.
  // Conta do mount real (não do `show`), então já mostra o tempo certo.
  const startRef = useRef(Date.now());
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(iv);
  }, []);

  const rawIdx = steps.findIndex(s => s.key === stage); // -1 = conectando/carga rápida
  if (rawIdx > maxIdxRef.current) maxIdxRef.current = rawIdx;
  const idx = maxIdxRef.current;

  // Dentro da etapa a barra rasteja assintoticamente rumo ao piso da próxima
  // (estilo nprogress): etapas longas (ex.: "membros" numa guilda grande)
  // mostram movimento contínuo sem nunca completar o trecho antes da etapa
  // real acabar. Piso salta quando a etapa avança; teto = próximo piso - 1.
  const [pct, setPct] = useState(6);
  useEffect(() => {
    const floor = idx < 0 ? 6 : ((idx + 1) / (steps.length + 1)) * 100;
    const ceiling = ((idx + 2) / (steps.length + 1)) * 100 - 1;
    setPct(p => Math.max(p, floor));
    const iv = setInterval(() => setPct(p => p + (ceiling - p) * 0.015), 300);
    return () => clearInterval(iv);
  }, [idx, steps.length]);

  if (!show) return null;

  return (
    <div className="mb-4">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500 tabular-nums">
          {idx < 0 ? t("plpConnecting") : `${idx + 1}/${steps.length}`}
        </span>
        {steps.map((s, i) => (
          <span
            key={s.key}
            className={`text-[10px] uppercase tracking-wide ${
              i < idx ? "text-zinc-400" : i === idx ? "text-amber-400 animate-pulse" : "text-zinc-700"
            }`}
          >
            {i < idx && <i className="ti ti-check mr-1" aria-hidden="true" />}
            {s.label}
          </span>
        ))}
      </div>
      <div className="dash-progress">
        <div className="dash-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      {/* retry só quando não há etapa em andamento AGORA (rawIdx, não o
          monotônico) — se o stage voltou, a consulta nova já está progredindo */}
      {retrying && rawIdx < 0 ? (
        <p className="mt-2 text-[11px] text-amber-400">
          <i className="ti ti-refresh mr-1 inline-block animate-spin" aria-hidden="true" />
          {t("plpRetrying")}
          <span className="tabular-nums text-amber-400/60"> · {elapsed}s</span>
        </p>
      ) : (
        <p className="mt-2 text-[11px] text-zinc-600">
          {hint}
          <span className="tabular-nums text-zinc-500"> · {elapsed}s</span>
          {queue != null && queue > 0 && (
            <span className="text-amber-400/70"> · {t("plpAlbionQueue").replace("{n}", String(queue))}</span>
          )}
        </p>
      )}
    </div>
  );
}
