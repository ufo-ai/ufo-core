import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

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
