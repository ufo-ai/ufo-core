import { useSyncExternalStore } from "react";

/** Which of the theme's two palettes paints the portal. `theme.css` declares every colour as a
 *  `light-dark()` pair read off `color-scheme`, so a class on the root document is the whole
 *  mechanism: `system` carries none and leaves the choice with the browser. The boot entry marks
 *  the root before the first render, so a member who pinned dark never sees a light frame. */
export type Scheme = "light" | "dark" | "system";

export const SCHEME_OPTIONS: { scheme: Scheme; label: string }[] = [
  { scheme: "light", label: "Light" },
  { scheme: "dark", label: "Dark" },
  { scheme: "system", label: "System" },
];

const HELD = "scheme";

export function heldScheme(): Scheme {
  const held = localStorage.getItem(HELD);
  return held === "light" || held === "dark" ? held : "system";
}

export function markScheme(scheme: Scheme): void {
  document.documentElement.classList.toggle("light", scheme === "light");
  document.documentElement.classList.toggle("dark", scheme === "dark");
}

/** The one preference every control that states it reads. The choice belongs to the browser rather
 *  than to a screen: the sidebar's glyph and the account menu's submenu are two views of it, and a
 *  pick in either is the same pick — two copies of it in component state would leave one saying the
 *  palette the member had left behind. */
let held: Scheme | null = null;
const listeners = new Set<() => void>();

function scheme(): Scheme {
  held ??= heldScheme();
  return held;
}

export function useScheme(): Scheme {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, scheme);
}

/** Take the pick, hold it in this browser, and paint it. Radix names a radio value with a string,
 *  and anything but the two pinned palettes is the browser's own choice. */
export function pickScheme(value: string): void {
  const next: Scheme = value === "light" || value === "dark" ? value : "system";
  localStorage.setItem(HELD, next);
  markScheme(next);
  held = next;
  for (const listener of listeners) listener();
}

export function resetScheme(): void {
  held = null;
}
