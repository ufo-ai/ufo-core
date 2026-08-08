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

const merge = extendTailwindMerge({ extend: { theme: { spacing: SPACING } } });

/** The spacing scale is named by role, so tailwind-merge cannot recognise `px-3xl` as a padding
 *  without being told the names. Untold, it keeps both sides of a conflict and the cascade order
 *  decides — which makes every override in a `className` prop silently positional. */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
