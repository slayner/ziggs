import { useEffect, useRef } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  isEditableTarget,
  isZoomShortcut,
  nextZoom,
  readZoomPreference,
  saveZoomPreference,
} from "./zoom";

export function useWebviewZoom(): void {
  const appliedZoom = useRef(readZoomPreference());
  const requestedZoom = useRef(appliedZoom.current);
  const savedZoom = useRef(appliedZoom.current);
  const applyZoom = useRef(Promise.resolve());

  useEffect(() => {
    const webview = getCurrentWebview();
    applyZoom.current = webview.setZoom(appliedZoom.current).catch(() => {});

    const onKeyDown = (event: KeyboardEvent) => {
      const direction = isZoomShortcut(event);
      if (!direction || isEditableTarget(event.target)) return;

      event.preventDefault();
      const next = nextZoom(requestedZoom.current, direction);
      requestedZoom.current = next;
      applyZoom.current = applyZoom.current.then(() => webview.setZoom(next).then(() => {
        appliedZoom.current = next;
        if (requestedZoom.current === next) {
          saveZoomPreference(next);
          savedZoom.current = next;
        }
      }).catch(() => {
        if (requestedZoom.current === next) {
          requestedZoom.current = appliedZoom.current;
          if (savedZoom.current !== appliedZoom.current) {
            saveZoomPreference(appliedZoom.current);
            savedZoom.current = appliedZoom.current;
          }
        }
      }));
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, []);
}
