import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export function Breakdown({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown"
      className={cn("flex min-w-0 flex-col gap-2xl", className)}
      {...props}
    />
  );
}

export function BreakdownHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-header"
      className={cn("flex h-(--size-glyph) w-full items-center gap-2xl", className)}
      {...props}
    />
  );
}

export function BreakdownLabel({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breakdown-label"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink-soft", className)}
      {...props}
    />
  );
}

export function BreakdownRows({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-rows"
      className={cn("flex w-full min-w-0 flex-col gap-sm", className)}
      {...props}
    />
  );
}

export function BreakdownRow({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-row"
      className={cn("flex h-(--size-glyph) w-full items-center justify-between gap-sm", className)}
      {...props}
    />
  );
}

export function BreakdownName({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breakdown-name"
      className={cn(
        "flex min-w-0 items-center gap-sm truncate text-label text-ink",
        "[&_[data-slot=breakdown-mark]]:shrink-0",
        className,
      )}
      {...props}
    />
  );
}

export function BreakdownMark({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breakdown-mark"
      className={cn(
        "flex size-(--size-glyph) shrink-0 items-center justify-center *:size-full",
        className,
      )}
      {...props}
    />
  );
}

export function BreakdownValue({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breakdown-value"
      className={cn(
        "shrink-0 tabular-nums whitespace-nowrap text-label font-medium text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}
