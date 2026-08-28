import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** The rule between two stretches of one screen — a band and the rows beneath it, a group of acts
 *  and the next. The rule is its own element carrying one hairline, so neither side of it owns a
 *  border: a section that moves, or draws nothing at all, cannot leave a line hanging under an
 *  empty page or double one against its neighbour's.
 *
 *  It names the division rather than hiding it, so a member reading the page with a screen reader
 *  is given the same break the eye is. */
export function Separator({
  orientation = "horizontal",
  className,
  ...props
}: ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      data-slot="separator"
      data-orientation={orientation}
      role="separator"
      aria-orientation={orientation}
      className={cn(
        "shrink-0",
        orientation === "horizontal"
          ? "w-full border-t border-edge"
          : "self-stretch border-l border-edge",
        className,
      )}
      {...props}
    />
  );
}
