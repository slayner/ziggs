import { useEffect, useMemo, useState } from "react";
import { useT } from "../i18n";
import { buildRenderUrl } from "../render";

type RenderImageProps = {
  apiBaseUrl: string;
  kind: "spell" | "item";
  candidates: string[];
  alt: string;
  size?: number;
};

const RETRY_DELAY_MS = 1_000;
const MAX_RETRY_ROUNDS = 2;

export function RenderImage({ apiBaseUrl, kind, candidates, alt, size = 64 }: RenderImageProps) {
  const t = useT();
  const candidateKey = useMemo(() => JSON.stringify(candidates), [candidates]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [retryRound, setRetryRound] = useState(0);
  const [status, setStatus] = useState<"loading" | "retrying" | "loaded">("loading");
  const candidate = candidates[candidateIndex];

  useEffect(() => {
    setCandidateIndex(0);
    setRetryRound(0);
    setStatus("loading");
  }, [candidateKey]);

  useEffect(() => {
    if (candidate || candidates.length === 0 || retryRound >= MAX_RETRY_ROUNDS) return;

    const retry = window.setTimeout(() => {
      setCandidateIndex(0);
      setRetryRound(round => round + 1);
      setStatus("retrying");
    }, RETRY_DELAY_MS);
    return () => window.clearTimeout(retry);
  }, [candidate, candidates.length, retryRound]);

  if (!candidate) {
    return (
      <span
        aria-label={`${alt}: ${t("renderUnavailable")}`}
        className="render-image render-image-unavailable"
        data-render-status="unavailable"
        role="img"
        style={{ width: size, height: size }}
      >
        —
      </span>
    );
  }

  return (
    <img
      alt={alt}
      className="render-image"
      data-render-status={status}
      height={size}
      onError={() => {
        setCandidateIndex(index => index + 1);
        setStatus("retrying");
      }}
      onLoad={() => setStatus("loaded")}
      src={buildRenderUrl(apiBaseUrl, kind, candidate, kind === "item" ? { size } : undefined)}
      width={size}
    />
  );
}
