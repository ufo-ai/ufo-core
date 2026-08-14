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

export function holdScheme(scheme: Scheme): void {
  localStorage.setItem(HELD, scheme);
  markScheme(scheme);
}
