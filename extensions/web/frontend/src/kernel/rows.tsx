import type { ReactNode } from "react";

import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

/** A list of records whose one sentence is scanned past rather than read: the name on its own
 *  line, the short fields and the prose under it as one truncated meta line, the moment held to
 *  the right.
 *
 *  `open` hands the row to `rowControl`, the one thing in the portal that makes a record's row
 *  the control that opens it — an index the member came to open has one target per row, and a
 *  target that is a word inside the row is invisible until hover and reads as a different
 *  affordance on every screen. `open` returning null leaves the row inert, with no role and no
 *  tab stop, and its meta says why; that is the only place a row's eligibility is decided, so a
 *  row the member may not open cannot be reached by an incidental press. */
export function RowLines<Row>({
  rows,
  rowKey,
  primary,
  meta,
  when,
  open,
  action,
}: {
  rows: Row[];
  rowKey: (row: Row) => string;
  primary: (row: Row) => ReactNode;
  meta: (row: Row) => ReactNode[];
  when?: (row: Row) => ReactNode;
  open?: (row: Row) => (() => void) | null;
  action?: (row: Row) => ReactNode;
}) {
  return (
    <ul className="m-0 list-none p-0">
      {rows.map((row) => {
        const press = open?.(row) ?? null;
        const control = press ? rowControl(press) : null;
        return (
          <li
            key={rowKey(row)}
            {...control}
            className={cn(
              "flex items-baseline gap-md border-b border-edge-soft py-md last:border-b-0",
              control?.className,
              press && "hover:bg-fill-hover",
            )}
          >
            <div className="min-w-0 flex-1">
              <div data-part="primary" className="truncate text-body">
                {primary(row)}
              </div>
              <MetaLine parts={meta(row)} />
            </div>
            {when ? (
              <div
                data-part="when"
                className="whitespace-nowrap font-mono text-small tabular-nums opacity-(--opacity-muted)"
              >
                {when(row)}
              </div>
            ) : null}
            {action ? <div>{action(row)}</div> : null}
          </li>
        );
      })}
    </ul>
  );
}

function MetaLine({ parts }: { parts: ReactNode[] }) {
  const shown = parts.filter((entry) => entry !== null && entry !== undefined && entry !== "");
  if (!shown.length) return null;
  return (
    <div data-part="meta" className="truncate text-small opacity-(--opacity-muted)">
      {shown.map((entry, index) => (
        <span key={index}>
          {index ? " · " : ""}
          {entry}
        </span>
      ))}
    </div>
  );
}
