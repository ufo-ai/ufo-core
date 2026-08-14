import type { ComponentProps, ReactNode } from "react";

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
export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="shrink-0 overflow-x-auto">
      <table
        data-slot="table"
        className={cn(
          "w-full table-fixed border-collapse [&_tr]:h-(--size-record)",
          "[&_:where(th,td):first-child]:pl-0 [&_:where(th,td):last-child]:pr-0",
          className,
        )}
        {...props}
      />
    </div>
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
  "border-b border-edge-faint px-2xl text-left align-middle tabular-nums",
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
      <Td colSpan={span} className="opacity-(--opacity-muted-soft)">
        {children}
      </Td>
    </tr>
  );
}
