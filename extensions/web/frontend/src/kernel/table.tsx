import type { ReactNode } from "react";

import { Table, TableNote, Th } from "@/components/ui/table";
import { PanelBlank } from "@/kernel/panel";
import { cn } from "@/lib/cn";

/** A column head. A plain string names a column the read cannot order by; the object form carries
 *  the key the read orders on, which is what makes the head pressable. */
export type Column = string | { label: string; sort: string };

export type Sort = { by: string; descending: boolean; onSort: (key: string) => void };

function label(column: Column): string {
  return typeof column === "string" ? column : column.label;
}

/** Drawn inline, like the chevron and the tick in `select.tsx`. Three glyphs still do not earn an
 *  icon package, and this one takes `currentColor` so it needs no token. */
function Caret({ descending }: { descending: boolean }) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden className="size-(--spacing-lg) shrink-0">
      <path
        d={descending ? "M3 5l3 3 3-3" : "M3 7l3-3 3 3"}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** The order lives on the head of the column it orders, never in a picker beside the search. The
 *  member reading a column down is already pointing at the thing they want ordered, and a picker
 *  states the same field names a second time, in a control that pushes the act off the bar. */
function Head({ column, sort }: { column: Column; sort?: Sort }) {
  if (typeof column === "string" || !sort) return <Th>{label(column)}</Th>;
  const active = sort.by === column.sort;
  return (
    <Th aria-sort={active ? (sort.descending ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        onClick={() => sort.onSort(column.sort)}
        className={cn(
          "flex items-center gap-2xs border-0 bg-transparent p-0 text-left font-inherit text-inherit",
          "transition-[opacity] duration-100 ease-control hover:opacity-(--opacity-muted-faint)",
        )}
      >
        {column.label}
        {active ? <Caret descending={sort.descending} /> : null}
      </button>
    </Th>
  );
}

/** `note` is what a *narrowed* table says when nothing is left: the card and its header hold, so
 *  the control the member is pressing does not move under them. `empty` is the other case — the
 *  records were never there — and it takes the blank card instead. */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  empty,
  note,
  sort,
  children,
}: {
  columns: Column[];
  rows: Row[];
  rowKey: (row: Row) => string;
  empty: string;
  note?: string;
  sort?: Sort;
  children: (row: Row) => ReactNode;
}) {
  if (!rows.length && !note) return <PanelBlank body={empty} />;
  return (
    <Table>
      <thead>
        <tr>
          {columns.map((column, index) => (
            <Head key={label(column) + index} column={column} sort={sort} />
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length ? (
          rows.map((row) => <tr key={rowKey(row)}>{children(row)}</tr>)
        ) : (
          <TableNote span={columns.length}>{note}</TableNote>
        )}
      </tbody>
    </Table>
  );
}
