import { Fragment, type ReactNode } from "react";

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
