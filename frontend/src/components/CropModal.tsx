import { useEffect, useState } from "react";
import Cropper from "react-easy-crop";
import type { Area } from "react-easy-crop";
import { useT } from "../i18n";
import type { CropRect } from "../api";

// Modal de crop de avatar/banner. O <img> interno do react-easy-crop é nativo,
// então GIF animado anima sozinho durante o crop — nenhum código extra.
// O crop real acontece no SERVIDOR (frações 0..1 via CropRect); aqui o usuário
// só escolhe o retângulo. Renderizado dentro do dropdown do usuário: position
// fixed escapa do overflow:hidden do menu, e por ficar no subtree DOM do menu
// os cliques aqui não disparam o outside-click que fecharia o dropdown.
export default function CropModal({ url, aspect, round = false, onCancel, onConfirm }: {
  url: string;
  aspect: number;
  round?: boolean;
  onCancel: () => void;
  onConfirm: (crop: CropRect) => void;
}) {
  const t = useT();
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  // 1º arg do onCropComplete já vem em PORCENTAGENS da imagem original —
  // é exatamente o contrato do backend (÷100), sem precisar das dimensões.
  const [areaPct, setAreaPct] = useState<Area | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-[300] flex items-center justify-center bg-black/70 p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="w-full max-w-lg overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3">
          <h3 className="text-sm font-semibold text-zinc-200">{t("cropTitle")}</h3>
          <button onClick={onCancel} className="text-zinc-600 hover:text-zinc-300" aria-label={t("cancel")}>
            <i className="ti ti-x" style={{ fontSize: 16 }} />
          </button>
        </div>

        <div className="relative h-72 bg-zinc-950">
          <Cropper
            image={url}
            crop={crop}
            zoom={zoom}
            aspect={aspect}
            cropShape={round ? "round" : "rect"}
            showGrid={!round}
            onCropChange={setCrop}
            onZoomChange={setZoom}
            onCropComplete={area => setAreaPct(area)}
          />
        </div>

        <div className="flex items-center gap-3 px-4 pt-3">
          <i className="ti ti-zoom-out text-zinc-600" style={{ fontSize: 15 }} />
          <input
            type="range" min={1} max={4} step={0.02} value={zoom}
            onChange={e => setZoom(Number(e.target.value))}
            className="flex-1 accent-amber-400"
            aria-label="Zoom"
          />
          <i className="ti ti-zoom-in text-zinc-600" style={{ fontSize: 15 }} />
        </div>
        <p className="px-4 pt-1.5 text-center text-[10px] text-zinc-600">{t("cropHint")}</p>

        <div className="flex gap-2 p-4">
          <button onClick={onCancel}
            className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-700">
            {t("cancel")}
          </button>
          <button
            disabled={!areaPct}
            onClick={() => areaPct && onConfirm({
              x: areaPct.x / 100, y: areaPct.y / 100,
              w: areaPct.width / 100, h: areaPct.height / 100,
            })}
            className="flex-1 rounded-lg border border-amber-700/50 bg-amber-950/30 px-3 py-2 text-xs font-medium text-amber-300 hover:bg-amber-950/50 disabled:opacity-40">
            {t("uploadImage")}
          </button>
        </div>
      </div>
    </div>
  );
}
