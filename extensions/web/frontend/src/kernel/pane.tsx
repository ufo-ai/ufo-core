import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** The measure a page is read at, centred in whatever width the shell leaves. `--container-page`
 *  is a section plus the gutters it is read inside, so a section reaches exactly its own
 *  `--container-section` and no screen is wider than the text it holds. Every band under the top
 *  bar takes it, so the title, the records and the notice between them share one pair of margins. */
export const COLUMN = "mx-auto w-full max-w-page";

/** The pane a destination draws in. It runs the full width the shell leaves, because the top bar's
 *  rule separates the page's header from its body and a rule that stops two thirds of the way
 *  across states a boundary the surface does not have. */
export function Pane({ className, ...props }: ComponentProps<"main">) {
  return <main {...props} className={cn("flex min-h-0 min-w-0 flex-col", className)} />;
}
