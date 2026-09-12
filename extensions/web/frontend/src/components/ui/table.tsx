import type { ComponentProps, CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** The tracks are fixed rather than measured: measured tracks make a table's shape a function of its
 *  longest cell, and are what make `truncate` cut a cell rather than widen it. */
export function Table({
  className,
  columns,
  floor,
  measured,
  style,
  ...props
}: ComponentProps<"table"> & {
  columns?: string[];
  floor?: string;
  measured?: boolean;
}) {
  return (
    <div data-slot="table-container" className="shrink-0 overflow-x-auto">
      <table
        data-slot="table"
        data-stacks={columns ? "" : undefined}
        data-measured={measured ? "" : undefined}
        style={{ ...labelProperties(columns), "--table-floor": floor, ...style } as CSSProperties}
        className={cn(
          "w-full min-w-(--table-floor) border-collapse [&_tr]:h-(--size-record)",
          measured ? "table-auto" : "table-fixed",
          "[&_:where(th,td):first-child]:pl-0 [&_:where(th,td):last-child]:pr-0",
          className,
        )}
        {...props}
      />
    </div>
  );
}

function labelProperties(columns: string[] | undefined): Record<string, string> {
  if (!columns) return {};
  return Object.fromEntries(
    columns.map((label, index) => ["--table-label-" + (index + 1), JSON.stringify(label)]),
  );
}

/** The tracks are stated in pixels, so a table set in a column narrower than their sum gives the shared
 *  columns what is left of nothing. The floor lifts below the narrow breakpoint, where records stack. */
export function tableFloor({
  prose,
  fact = 0,
  act = false,
}: {
  prose: number;
  fact?: number;
  act?: boolean;
}): string {
  return (
    "calc(" +
    fact +
    " * var(--size-fact-column) + " +
    prose +
    " * var(--size-prose-column) + " +
    (act ? 1 : 0) +
    " * var(--size-act))"
  );
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      scope="col"
      className={cn(
        "truncate px-2xl text-left align-middle tabular-nums",
        "text-label font-normal text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}

const CELL = cn(
  "border-b border-edge px-2xl text-left align-middle tabular-nums",
  "text-label text-ink-soft",
);

/** One table cell: the ruled, truncating cell every column is drawn with. */
export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, "truncate", className)} {...props} />;
}

/** The cell of the column that takes whatever width the columns beside it leave. An auto-layout
 *  table sizes a column from what it holds, so `max-width: 0` is what makes the cell yield to the
 *  head's `width: 100%` track instead of widening the table; `truncate` then cuts the line. The
 *  stacked rule lifts the bound, where a cell is a flex row rather than a track. */
export function TdFill({ className, ...props }: ComponentProps<"td">) {
  return <Td className={cn("max-w-0", className)} {...props} />;
}

/** A cell whose value is read entire — the name a member finds a record by. It holds one line, like
 *  every other cell, but the line is not cut: the column it stands in is measured from the rows, so
 *  the cell is as wide as it needs and the container scrolls sideways to reach the rest of the
 *  table. It drops `truncate` rather than overriding it, for the reason `TdActs` does. */
export function TdWhole({ className, ...props }: ComponentProps<"td">) {
  return (
    <td data-slot="table-cell" className={cn(CELL, "whitespace-nowrap", className)} {...props} />
  );
}

/** Prose a measured table still cuts. A measured track is the widest thing in its column, so a cell
 *  left to say as much as it likes would make the Details column the table's width; bounding the run
 *  of prose itself is what keeps that column the track every other table gives it. The bound rides
 *  an inner block because `max-width` does not apply to a cell. */
export function Clip({ children }: { children: ReactNode }) {
  return <span className="block max-w-(--size-prose-column) truncate">{children}</span>;
}

/** A column holding one short fact the eye compares straight down — a model id, a state. It is
 *  sized rather than left to the content, so the same fact lands on the same line in every row and
 *  the two flexible columns beside it take whatever is left. */
export function TdFact({ className, ...props }: ComponentProps<"td">) {
  return <Td className={cn("w-(--size-fact-column)", className)} {...props} />;
}

/** It drops `truncate` rather than overriding it, because `overflow-visible` beside `truncate` is
 *  settled by which rule the sheet emits last. */
export function TdActs({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, className)} {...props} />;
}

export const ACTS = "flex flex-nowrap items-center justify-end gap-xs";

/** What a record's first cell holds: the mark the record is recognised by, then its name. The mark
 *  box keeps its size whether or not the record has a picture to draw in it — a record with none
 *  shows the glyph for its kind in the same square — so every name down the column starts on one
 *  line rather than sliding left in the rows that have nothing to show. The name is cut to the
 *  track instead of wrapping, because a row is one pitch tall and a second line would be drawn
 *  behind the row's own edge. */
export function Lede({
  mark,
  whole,
  children,
}: {
  mark: ReactNode;
  whole?: boolean;
  children: ReactNode;
}) {
  return (
    <span className="flex min-w-0 items-center gap-md">
      <span
        className={cn(
          "flex size-(--size-lede) shrink-0 items-center justify-center",
          "overflow-hidden rounded-control bg-fill text-ink-soft",
        )}
      >
        {mark}
      </span>
      <span className={whole ? "whitespace-nowrap" : "min-w-0 truncate"}>{children}</span>
    </span>
  );
}

export function TableNote({ span, children }: { span: number; children: ReactNode }) {
  return (
    <tr>
      <Td colSpan={span} className="text-ink-soft">
        {children}
      </Td>
    </tr>
  );
}
