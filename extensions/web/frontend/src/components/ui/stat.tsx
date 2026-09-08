import { IconArrowDownRight, IconArrowUpRight, type TablerIcon } from "@tabler/icons-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export function Stat({
  unarmed,
  className,
  ...props
}: ComponentProps<"div"> & { unarmed?: boolean }) {
  return (
    <div
      data-slot="stat"
      data-unarmed={unarmed ? "" : undefined}
      className={cn(
        "group/stat flex min-w-0 flex-col items-start gap-2xl",
        "text-ink-soft data-unarmed:text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}

export function StatHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="stat-header"
      className={cn("flex h-(--size-glyph) w-full items-center gap-sm", className)}
      {...props}
    />
  );
}

export function StatMedia({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="stat-media"
      className={cn(
        "flex size-(--size-glyph) shrink-0 items-center justify-center *:size-full",
        className,
      )}
      {...props}
    />
  );
}

export function StatLabel({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="stat-label"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink-quiet", className)}
      {...props}
    />
  );
}

export function StatValue({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="stat-value"
      className={cn(
        "flex w-full items-baseline gap-sm text-figure font-medium text-ink",
        "group-data-unarmed/stat:text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}

export type StatTone = "default" | "up" | "down";

const DELTA_TONES: Record<StatTone, string> = {
  default: "text-ink-soft",
  up: "text-link",
  down: "text-attention-ink",
};

const DELTA_GLYPHS: Record<StatTone, TablerIcon | null> = {
  default: null,
  up: IconArrowUpRight,
  down: IconArrowDownRight,
};

export function StatDelta({
  tone = "default",
  className,
  children,
  ...props
}: ComponentProps<"span"> & { tone?: StatTone }) {
  const Glyph = DELTA_GLYPHS[tone];
  return (
    <span
      data-slot="stat-delta"
      className={cn(
        "inline-flex items-baseline gap-2xs text-small font-medium",
        DELTA_TONES[tone],
        "group-data-unarmed/stat:text-ink-faint",
        className,
      )}
      {...props}
    >
      {Glyph ? <Glyph aria-hidden className="size-(--size-glyph) shrink-0 self-center" /> : null}
      {children}
    </span>
  );
}

export function StatDescription({ className, ...props }: ComponentProps<"p">) {
  return (
    <p data-slot="stat-description" className={cn("m-0 w-full text-small", className)} {...props} />
  );
}
