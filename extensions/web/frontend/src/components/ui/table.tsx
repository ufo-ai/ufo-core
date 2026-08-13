import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A table is a column of records on the section's own ground, not a card set into it: no border,
 *  no fill, no shadow. The rules between rows are the whole of its chrome, and they run the full
 *  width of the section while the cells sit a padding inside it — a row is a band, so it can fill
 *  under the pointer the way a pressable row does, and the rule states how far that band reaches.
 *  The head is parted from the records by space rather than by a rule, so every line in the table
 *  divides one record from the next. A table is read down its first column, so that column carries
 *  the ink and every column beside it — what the record says about itself — is set softer. */
export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="shrink-0 overflow-x-auto">
      <table
        data-slot="table"
        className={cn(
          "w-full border-collapse",
          "[&_:where(th,td):first-child]:pl-0 [&_:where(th,td):last-child]:pr-0",
          "[&_tbody_td:not(:first-child)]:text-ink-soft",
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
        "truncate text-left align-middle px-2xl py-md tabular-nums",
        "text-ui font-normal text-ink-soft",
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
export function Td({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "truncate text-left align-middle px-2xl py-md border-b border-edge-soft tabular-nums",
        className,
      )}
      {...props}
    />
  );
}

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
