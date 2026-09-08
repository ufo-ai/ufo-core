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
  "7xl",
  "8xl",
];

const RADIUS = ["sm", "control", "row", "answer", "panel", "bubble", "menu", "avatar", "card", "key"];

const TEXT = ["fine", "mono", "small", "label", "ui", "body", "subtitle", "title", "figure"];

const WEIGHT = ["strong"];

const merge = extendTailwindMerge({
  extend: { theme: { spacing: SPACING, radius: RADIUS, text: TEXT, "font-weight": WEIGHT } },
});

/** tailwind-merge needs our role-named scales or it keeps both sides of a conflict and the cascade order
 *  decides — a size spelled `text-…` reads as a colour and is dropped. A test holds these to theme.css. */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
