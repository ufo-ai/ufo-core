import { IconChevronRight } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { Table, TableNote, Td, Th, tableFloor } from "@/components/ui/table";
import { rowControl } from "@/kernel/row";
import { PanelBlank } from "@/kernel/panel";
import { cn } from "@/lib/cn";

/** What a row's act says where the row opens a record of its own. One word, in one place: three
 *  screens draw this act and a member reads the same verb on each. */
export const OPEN = "Open";

/** A column head. A plain string names a column carrying prose, which the read cannot order by and
 *  which shares the width left over. The object form carries the key the read orders on — which is
 *  what makes the head pressable — and `fact` marks a column holding one short value, which states
 *  its own width so the same fact lands on the same line in every row. */
export type Column = string | { label: string; sort?: string; fact?: boolean };

export type Sort = { by: string; descending: boolean; onSort: (key: string) => void };

function label(column: Column): string {
  return typeof column === "string" ? column : column.label;
}

/** The tracks are fixed, so a width declared on a body cell arrives too late — the head is what
 *  sizes the column. */
function width(column: Column): string | undefined {
  return typeof column === "string" || !column.fact ? undefined : "w-(--size-fact-column)";
}

function isFact(column: Column): boolean {
  return typeof column !== "string" && Boolean(column.fact);
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
  const track = width(column);
  const key = typeof column === "string" ? undefined : column.sort;
  if (!key || !sort) return <Th className={track}>{label(column)}</Th>;
  const active = sort.by === key;
  return (
    <Th
      className={track}
      aria-sort={active ? (sort.descending ? "descending" : "ascending") : "none"}
    >
      <button
        type="button"
        onClick={() => sort.onSort(key)}
        className={cn(
          "flex items-center gap-2xs border-0 bg-transparent p-0 text-left font-sans text-inherit",
          "transition-[opacity] duration-100 ease-control hover:opacity-(--opacity-muted-faint)",
        )}
      >
        {label(column)}
        {active ? <Caret descending={sort.descending} /> : null}
      </button>
    </Th>
  );
}

/** The act a row carries, at the table's one right edge. It names what it does rather than standing
 *  as a bare glyph: a chevron alone says a row leads somewhere and the member learns where only by
 *  pressing it. A row that carries no act still holds the column, so the rows a member cannot act
 *  on keep the rhythm instead of letting the edge ripple down the page. */
function Act({ verb }: { verb: string | null }) {
  if (!verb) return null;
  return (
    <span className="flex items-center justify-end gap-2xs text-ink-soft">
      {verb}
      <IconChevronRight className="size-icon shrink-0" aria-hidden />
    </span>
  );
}

/** `note` is what a *narrowed* table says when nothing is left: the card and its header hold, so
 *  the control the member is pressing does not move under them. `empty` is the other case — the
 *  records were never there — and it takes the blank card instead.
 *
 *  The act column is the table's own, never a caller's: every screen that drew its own trailing
 *  chevron drew it at a different width, so one table's rows ended where the next one's did not.
 *  `act` names the verb a row's own act commits, and the head above it is blank because the column
 *  holds acts rather than a fact the records share.
 *
 *  `current` names the row whose contents are standing in the column beside the table, and the mark
 *  is the `tr` itself: `aria-current` on the row a reader already navigates as a row, and the same
 *  fill the row takes under the pointer, so the band reaches the rules that divide the records
 *  rather than stopping at a cell. The band is square-cornered because it is the whole width of the
 *  table and because collapsed borders drop a radius on every part of one. */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  empty,
  note,
  sort,
  open,
  current,
  act,
  children,
}: {
  columns: Column[];
  rows: Row[];
  rowKey: (row: Row) => string;
  empty: string;
  note?: string;
  sort?: Sort;
  /** What a row opens, where the record has a page of its own — the whole row is the control that
   *  reaches it, and a row that opens nothing is handed none. */
  open?: (row: Row) => (() => void) | null;
  /** Which row is standing in the column beside the table, or none while the track is empty. */
  current?: (row: Row) => boolean;
  /** What the row's own act says, or null where the row carries none. */
  act?: (row: Row) => string | null;
  children: (row: Row) => ReactNode;
}) {
  if (!rows.length && !note) return <PanelBlank body={empty} />;
  const span = columns.length + (act ? 1 : 0);
  const facts = columns.filter(isFact).length;
  return (
    <Table
      columns={[...columns.map(label), ...(act ? [""] : [])]}
      floor={tableFloor({
        prose: columns.length - facts,
        fact: facts,
        act: Boolean(act),
      })}
    >
      <thead>
        <tr>
          {columns.map((column, index) => (
            <Head key={label(column) + index} column={column} sort={sort} />
          ))}
          {act ? <Th className="w-(--size-act)">{""}</Th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.length ? (
          rows.map((row) => {
            const press = open?.(row) ?? null;
            const control = press ? rowControl(press, true) : null;
            const standing = current?.(row) ?? false;
            return (
              <tr
                key={rowKey(row)}
                {...control}
                aria-current={standing || undefined}
                className={cn(
                  control && "hover:bg-fill",
                  control?.className,
                  standing && "bg-fill",
                )}
              >
                {children(row)}
                {act ? (
                  <Td className="w-(--size-act)">
                    <Act verb={act(row)} />
                  </Td>
                ) : null}
              </tr>
            );
          })
        ) : (
          <TableNote span={span}>{note}</TableNote>
        )}
      </tbody>
    </Table>
  );
}
