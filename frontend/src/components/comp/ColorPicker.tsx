import { useEffect, useRef, useState } from "react";
import { useT } from "../../i18n";

export function ColorPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [hex, setHex] = useState(value.replace("#", ""));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setHex(value.replace("#", "")); }, [value]);
  useEffect(() => {
    if (!open) return;
    function close(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function applyHex(raw: string) {
    const v = raw.replace(/[^0-9a-fA-F]/g, "").slice(0, 6);
    setHex(v);
    if (v.length === 6) onChange(`#${v}`);
  }

  function randomize() {
    const r = Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, "0");
    onChange(`#${r}`);
    setHex(r);
  }

  return (
    <div ref={ref} style={{ position: "relative", flexShrink: 0 }}>
      <button className="color-swatch" style={{ background: value, width: 22, height: 22, flexShrink: 0 }}
        onClick={e => { e.stopPropagation(); setOpen(o => !o); }} />
      {open && (
        <div className="color-picker-popup" onClick={e => e.stopPropagation()}>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span style={{ color: "var(--muted)", fontSize: 12, fontFamily: "monospace" }}>#</span>
            <input className="input" value={hex} maxLength={6} placeholder="1399FF"
              style={{ width: 68, fontFamily: "monospace", fontSize: 12, padding: "2px 6px" }}
              onChange={e => applyHex(e.target.value)} />
            <label style={{ width: 26, height: 26, cursor: "pointer", borderRadius: 4, overflow: "hidden",
              background: value, border: "1px solid var(--border-strong)", flexShrink: 0, display: "block" }}>
              <input type="color" value={value} style={{ opacity: 0, width: "100%", height: "100%", cursor: "pointer" }}
                onChange={e => { onChange(e.target.value); setHex(e.target.value.replace("#", "")); }} />
            </label>
            <button className="cs-xbtn" title={t("randomColorTitle")}
              onClick={e => { e.stopPropagation(); randomize(); }}>
              <i className="ti ti-dice" aria-hidden />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
