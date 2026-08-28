import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** What a measure is made of, as a column of named parts each carrying its share: the part's mark
 *  and name at the leading edge, its figure at the trailing one. The figures sit on one right edge
 *  the eye runs down, which is what makes a column of shares scannable where the same shares in a
 *  sentence are parsed.
 *
 *  Its rows are a tighter idiom than `Item`'s: a share is one line of a reference column, drawn at
 *  the glyph pitch with no inset, where a row in an `ItemGroup` is a record a member opens and
 *  carries the padding a pointer needs. Two shapes, because a column of forty shares set at a
 *  record's pitch is a page of scrolling. */
export function Breakdown({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown"
      className={cn("flex min-w-0 flex-col gap-2xl", className)}
      {...props}
    />
  );
}

/** The line over the column: what the parts are parts of at the leading edge, the act that changes
 *  what is counted at the trailing one. One glyph deep, so the first share sits at the same height
 *  whether or not the column carries an act. */
export function BreakdownHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-header"
      className={cn("flex h-(--size-glyph) w-full items-center gap-2xl", className)}
      {...props}
    />
  );
}

/** What the parts are parts of, cut at the line's width so the act beside it keeps its end. */
export function BreakdownLabel({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breakdown-label"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink-soft", className)}
      {...props}
    />
  );
}

/** The shares themselves, at their own pitch rather than the column's: a share is a line, and the
 *  gap between two lines is tighter than the gap between the column and its heading. */
export function BreakdownRows({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-rows"
      className={cn("flex w-full min-w-0 flex-col gap-sm", className)}
      {...props}
    />
  );
}

/** One part and its share, held apart so the figure lands on the column's right edge. The row is
 *  one glyph deep and stays one glyph deep, so a column of shares keeps one rhythm however long a
 *  name runs — the name is cut instead. */
export function BreakdownRow({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="breakdown-row"
      className={cn("flex h-(--size-glyph) w-full items-center justify-between gap-sm", className)}
      {...props}
    />
  );
}

/** The part's mark and its name, read as one thing at the row's leading edge. */
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

/** The mark before the name, held at the glyph square whatever it is given, so no column states
 *  that size itself and one oversized mark cannot set the row's height. */
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

/** The part's share. It never shrinks and never wraps, so a crowded column cuts the name rather
 *  than the figure the column exists to state. */
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
