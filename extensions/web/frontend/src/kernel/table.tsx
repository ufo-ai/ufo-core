import { IconChevronRight } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { Table, TableNote, Td, Th, tableFloor } from "@/components/ui/table";
import { rowControl } from "@/kernel/row";
import { PanelBlank } from "@/kernel/panel";
import { cn } from "@/lib/cn";

export const OPEN = "Open";

export type Column =
  | string
  | { label: string; sort?: string; fact?: boolean; whole?: boolean; fill?: boolean };

export type Sort = { by: string; descending: boolean; onSort: (key: string) => void };

function label(column: Column): string {
  return typeof column === "string" ? column : column.label;
}

/** The tracks are fixed, so a width declared on a body cell arrives too late — the head is what sizes
 *  the column. A filled table runs auto, where the fill claim leaves the rest what it holds. */
function width(column: Column, measured: boolean, filled: boolean): string | undefined {
  if (isFill(column)) return "w-full";
  if (isFact(column)) return filled ? undefined : "w-(--size-fact-column)";
  if (!measured || isWhole(column)) return undefined;
  return "w-(--size-prose-column)";
}

function isFact(column: Column): boolean {
  return typeof column !== "string" && Boolean(column.fact);
}

function isWhole(column: Column): boolean {
  return typeof column !== "string" && Boolean(column.whole);
}

function isFill(column: Column): boolean {
  return typeof column !== "string" && Boolean(column.fill);
}

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

function Head({
  column,
  sort,
  measured,
  filled,
}: {
  column: Column;
  sort?: Sort;
  measured: boolean;
  filled: boolean;
}) {
  const track = width(column, measured, filled);
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
  stacks = true,
  children,
}: {
  columns: Column[];
  rows: Row[];
  rowKey: (row: Row) => string;
  empty: string;
  note?: string;
  sort?: Sort;
  open?: (row: Row) => (() => void) | null;
  current?: (row: Row) => boolean;
  act?: (row: Row) => string | null;
  stacks?: boolean;
  children: (row: Row) => ReactNode;
}) {
  if (!rows.length && !note) return <PanelBlank body={empty} />;
  const span = columns.length + (act ? 1 : 0);
  const facts = columns.filter(isFact).length;
  const filled = columns.some(isFill);
  const measured = filled || columns.some(isWhole);
  return (
    <Table
      columns={stacks ? [...columns.map(label), ...(act ? [""] : [])] : undefined}
      measured={measured}
      floor={tableFloor({
        prose: columns.length - facts,
        fact: facts,
        act: Boolean(act),
      })}
    >
      <thead>
        <tr>
          {columns.map((column, index) => (
            <Head
              key={label(column) + index}
              column={column}
              sort={sort}
              measured={measured}
              filled={filled}
            />
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
