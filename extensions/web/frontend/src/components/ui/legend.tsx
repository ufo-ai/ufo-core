import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type LegendTone = "primary" | "secondary" | "muted";

const TONES: Record<LegendTone, string> = {
  primary: "bg-live",
  secondary: "bg-blocked",
  muted: "bg-ink-faint",
};

export function Legend({ className, ...props }: ComponentProps<"ul">) {
  return (
    <ul
      data-slot="legend"
      className={cn("flex min-w-0 flex-wrap gap-2xl", className)}
      {...props}
    />
  );
}

export function LegendItem({
  tone,
  children,
  className,
  ...props
}: ComponentProps<"li"> & { tone: LegendTone }) {
  return (
    <li
      data-slot="legend-item"
      className={cn("flex min-w-0 items-center gap-2xs text-small text-ink-soft", className)}
      {...props}
    >
      <span
        aria-hidden
        data-slot="legend-swatch"
        data-tone={tone}
        className={cn("size-sm shrink-0 rounded-full", TONES[tone])}
      />
      {children}
    </li>
  );
}
