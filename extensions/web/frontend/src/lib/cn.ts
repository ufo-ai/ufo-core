import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

const SPACING = [
  "hair",
  "2xs",
  "xs",
  "sm",
  "md",
  "lg",
  "xl",
  "2xl",
  "3xl",
  "4xl",
  "5xl",
  "6xl",
];

const RADIUS = ["sm", "control", "panel", "bubble", "menu", "card"];

const TEXT = ["mono", "small", "label", "ui", "body", "subtitle", "title"];

const WEIGHT = ["strong"];

const merge = extendTailwindMerge({
  extend: { theme: { spacing: SPACING, radius: RADIUS, text: TEXT, "font-weight": WEIGHT } },
});

/** The spacing, radius and type scales are named by role, so tailwind-merge cannot recognise
 *  `px-3xl` as a padding, `rounded-panel` as a radius or `text-label` as a size without being told
 *  the names. Untold, it keeps both sides of a conflict and the cascade order decides — which makes
 *  every override in a `className` prop silently positional.
 *
 *  The type scale is worse than positional: a size and a colour are both spelled `text-…`, so an
 *  unrecognised `text-label` reads as a colour and is dropped by the `text-ink-soft` beside it. Every
 *  size stated next to a colour on one element was being discarded, silently and everywhere.
 *
 *  The weight scale carries one name of our own, and it loses the same way: `font-strong` beside a
 *  `font-normal` an override passes in reads as two unrelated classes, so both survive and the
 *  cascade keeps the heavier one — which is how a soft label ends up bold. */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
