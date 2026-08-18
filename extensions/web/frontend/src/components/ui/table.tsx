import type { ComponentProps, CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A table is a column of records on the section's own ground, not a card set into it: no border,
 *  no fill, no shadow. The rules between rows are the whole of its chrome, and they run the full
 *  width of the section while the cells sit a padding inside it — a row is a band, so it can fill
 *  under the pointer the way a pressable row does, and the rule states how far that band reaches.
 *  The head is parted from the records by space rather than by a rule, so every line in the table
 *  divides one record from the next. Every row is one pitch tall, head included, so the eye runs
 *  down a fixed rhythm and a cell that happens to hold more words cannot break it.
 *
 *  The tracks are fixed rather than measured from the content: a column carrying one short fact
 *  states its own width and the columns carrying prose share what is left in equal parts, so the
 *  same fact lands on the same line whatever the rows happen to say. Measured tracks make a table's
 *  shape a function of its longest cell, which is why two screens of the same records never lined
 *  up — and it is what makes `truncate` cut a cell rather than widen it. */
export function Table({
  className,
  columns,
  floor,
  style,
  ...props
}: ComponentProps<"table"> & {
  /** The head labels, in the order the cells stand in, with an empty label for a column that holds
   *  acts rather than a fact. A table that states them stacks into a column of records at a phone
   *  width, where no fixed track fits; a table that states none keeps its tracks and scrolls. */
  columns?: string[];
  /** What the table cannot be read below, from `tableFloor`. It rides a custom property rather than
   *  `min-width` itself, because a stacked table has no tracks to protect and an inline width is
   *  past overriding. */
  floor?: string;
}) {
  return (
    <div data-slot="table-container" className="shrink-0 overflow-x-auto">
      <table
        data-slot="table"
        data-stacks={columns ? "" : undefined}
        style={{ ...labelProperties(columns), "--table-floor": floor, ...style } as CSSProperties}
        className={cn(
          "w-full min-w-(--table-floor) table-fixed border-collapse [&_tr]:h-(--size-record)",
          "[&_:where(th,td):first-child]:pl-0 [&_:where(th,td):last-child]:pr-0",
          className,
        )}
        {...props}
      />
    </div>
  );
}

/** The head labels restated as custom properties on the table, one per column, so a stacked cell can
 *  draw its own label beside its value: generated content is a cell's own, and it cannot reach the
 *  head row for the word above it. */
function labelProperties(columns: string[] | undefined): Record<string, string> {
  if (!columns) return {};
  return Object.fromEntries(
    columns.map((label, index) => ["--table-label-" + (index + 1), JSON.stringify(label)]),
  );
}

/** What a table cannot be read below. The tracks are stated in pixels, so a table set in a column
 *  narrower than their sum gives the shared columns what is left of nothing — every cell collapses
 *  to its padding and the head above it truncates to a letter and an ellipsis. Stating the sum as
 *  the table's own floor keeps every column its own track and lets the container scroll instead,
 *  and it binds only there: wherever the table has the room, `w-full` is the wider of the two and
 *  the shared columns divide the rest exactly as they did. Below the narrow breakpoint the floor
 *  lifts, because a stacked record has no tracks to keep. */
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

/** The rule sits under a cell, not over it, so the head keeps its own space and the last row still
 *  closes the column. Collapsed borders fold two rows' rules into one. A cell holds one line: a
 *  table is read down a column, and a row as tall as whatever prose it happens to carry breaks the
 *  pitch the eye is running on — the record's own screen carries the rest. */
const CELL = cn(
  "border-b border-edge px-2xl text-left align-middle tabular-nums",
  "text-label text-ink-soft",
);

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, "truncate", className)} {...props} />;
}

/** A column holding one short fact the eye compares straight down — a model id, a state. It is
 *  sized rather than left to the content, so the same fact lands on the same line in every row and
 *  the two flexible columns beside it take whatever is left. */
export function TdFact({ className, ...props }: ComponentProps<"td">) {
  return <Td className={cn("w-(--size-fact-column)", className)} {...props} />;
}

/** The cell a row's own controls stand in — a cluster of acts rather than the one verb `DataTable`
 *  draws for a row that opens a record. It is the plain cell without the clip: a row is one pitch
 *  tall, so a cluster that wrapped would lose its second line behind the row's own edge, and the
 *  controls are the thing the member came to press. It drops `truncate` rather than overriding it,
 *  because `overflow-visible` beside `truncate` is settled by which rule the sheet emits last. */
export function TdActs({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, className)} {...props} />;
}

/** The row of controls that cell holds. It does not wrap, for the same reason the cell does not
 *  clip: the row's height is the table's pitch, not a function of how many acts a record happens to
 *  carry. */
export const ACTS = "flex flex-nowrap items-center justify-end gap-xs";

/** A filter that matches nothing leaves the table standing and says so in a row. Dropping the card
 *  and centring a note collapses the column the member is reading down, so every toggle of the
 *  filter would move the page under them. */
export function TableNote({ span, children }: { span: number; children: ReactNode }) {
  return (
    <tr>
      <Td colSpan={span} className="text-ink-soft">
        {children}
      </Td>
    </tr>
  );
}
