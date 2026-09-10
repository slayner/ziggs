export const DEFAULT_ZOOM = 1;
export const MIN_ZOOM = 0.8;
export const MAX_ZOOM = 1.5;
export const ZOOM_STEP = 0.1;

export function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 10) / 10));
}

export function nextZoom(current: number, direction: -1 | 1): number {
  return clampZoom(current + direction * ZOOM_STEP);
}

export function isZoomShortcut(event: KeyboardEvent): -1 | 0 | 1 {
  if (!event.ctrlKey && !event.metaKey) return 0;
  if (event.key === "-" || event.code === "NumpadSubtract") return -1;
  if (event.key === "+" || event.key === "=" || event.code === "NumpadAdd" || event.code === "Equal") return 1;
  return 0;
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function readZoomPreference(): number {
  const stored = localStorage.getItem("ziggs-companion-zoom");
  if (stored === null || stored.trim() === "") return DEFAULT_ZOOM;
  const value = Number(stored);
  return Number.isFinite(value) ? clampZoom(value) : DEFAULT_ZOOM;
}

export function saveZoomPreference(value: number): void {
  localStorage.setItem("ziggs-companion-zoom", String(clampZoom(value)));
}
