import { Fragment, type ReactNode } from "react";

import { Card } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { rowControl } from "@/kernel/row";
import { cn } from "@/lib/cn";

/** A list of records read as one card of ruled rows: the name on its own line, the short fields
 *  and the prose under it as one truncated meta line, the moment and the row's own acts held to
 *  the right. It is the shape every index of records takes, so a directory of connectors and a
 *  directory of credential slots read as one surface rather than as two designs.
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
  mark,
  primary,
  meta,
  when,
  open,
  action,
  whole,
}: {
  rows: Row[];
  rowKey: (row: Row) => string;
  mark?: (row: Row) => ReactNode;
  primary: (row: Row) => ReactNode;
  meta: (row: Row) => ReactNode[];
  when?: (row: Row) => ReactNode;
  open?: (row: Row) => (() => void) | null;
  action?: (row: Row) => ReactNode;
  whole?: boolean;
}) {
  return (
    <Card rows>
      <ItemGroup>
        {rows.map((row, index) => {
        const press = open?.(row) ?? null;
        const control = press ? rowControl(press) : null;
        const acts = action?.(row);
        return (
          <Fragment key={rowKey(row)}>
            {index ? <ItemSeparator /> : null}
            <Item {...control} className={cn(control?.className, press && "hover:bg-fill")}>
              {mark ? mark(row) : null}
              <ItemContent>
                <ItemTitle>{primary(row)}</ItemTitle>
                <MetaLine parts={meta(row)} whole={whole} />
              </ItemContent>
              {when ? (
                <div
                  data-part="when"
                  className="whitespace-nowrap font-mono text-small tabular-nums text-ink-soft"
                >
                  {when(row)}
                </div>
              ) : null}
              {acts ? <ItemActions>{acts}</ItemActions> : null}
            </Item>
          </Fragment>
        );
        })}
      </ItemGroup>
    </Card>
  );
}

function MetaLine({ parts, whole }: { parts: ReactNode[]; whole?: boolean }) {
  const shown = parts.filter((entry) => entry !== null && entry !== undefined && entry !== "");
  if (!shown.length) return null;
  return (
    <ItemDescription whole={whole}>
      {shown.map((entry, index) => (
        <span key={index}>
          {index ? " · " : ""}
          {entry}
        </span>
      ))}
    </ItemDescription>
  );
}
