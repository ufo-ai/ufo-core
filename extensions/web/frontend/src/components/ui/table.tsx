import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="shrink-0 overflow-x-auto rounded-panel border border-edge bg-surface">
      <table className={cn("w-full border-collapse", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      scope="col"
      className={cn(
        "text-left align-baseline px-xl py-md border-b border-edge-soft tabular-nums",
        "text-small font-strong opacity-(--muted)",
        className,
      )}
      {...props}
    />
  );
}

/** The rule sits on top of a cell, not under it, so the last row meets the card's own edge with
 *  no second line beside it. Collapsed borders fold it into the header's. */
export function Td({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      className={cn("text-left align-baseline px-xl py-lg border-t border-edge-soft tabular-nums", className)}
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
      <Td colSpan={span} className="opacity-(--muted-soft)">
        {children}
      </Td>
    </tr>
  );
}
