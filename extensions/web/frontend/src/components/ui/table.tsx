import type { ComponentProps, CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** The tracks are fixed rather than measured: measured tracks make a table's shape a function of its
 *  longest cell, and are what make `truncate` cut a cell rather than widen it. */
export function Table({
  className,
  columns,
  floor,
  measured,
  style,
  ...props
}: ComponentProps<"table"> & {
  columns?: string[];
  floor?: string;
  measured?: boolean;
}) {
  return (
    <div data-slot="table-container" className="shrink-0 overflow-x-auto">
      <table
        data-slot="table"
        data-stacks={columns ? "" : undefined}
        data-measured={measured ? "" : undefined}
        style={{ ...labelProperties(columns), "--table-floor": floor, ...style } as CSSProperties}
        className={cn(
          "w-full min-w-(--table-floor) border-collapse [&_tr]:h-(--size-record)",
          measured ? "table-auto" : "table-fixed",
          "[&_:where(th,td):first-child]:pl-0 [&_:where(th,td):last-child]:pr-0",
          className,
        )}
        {...props}
      />
    </div>
  );
}

function labelProperties(columns: string[] | undefined): Record<string, string> {
  if (!columns) return {};
  return Object.fromEntries(
    columns.map((label, index) => ["--table-label-" + (index + 1), JSON.stringify(label)]),
  );
}

/** The tracks are stated in pixels, so a table set in a column narrower than their sum gives the shared
 *  columns what is left of nothing. The floor lifts below the narrow breakpoint, where records stack. */
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

const CELL = cn(
  "border-b border-edge px-2xl text-left align-middle tabular-nums",
  "text-label text-ink-soft",
);

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, "truncate", className)} {...props} />;
}

export function TdWhole({ className, ...props }: ComponentProps<"td">) {
  return (
    <td data-slot="table-cell" className={cn(CELL, "whitespace-nowrap", className)} {...props} />
  );
}

/** The bound rides an inner block because `max-width` does not apply to a cell. */
export function Clip({ children }: { children: ReactNode }) {
  return <span className="block max-w-(--size-prose-column) truncate">{children}</span>;
}

export function TdFact({ className, ...props }: ComponentProps<"td">) {
  return <Td className={cn("w-(--size-fact-column)", className)} {...props} />;
}

/** It drops `truncate` rather than overriding it, because `overflow-visible` beside `truncate` is
 *  settled by which rule the sheet emits last. */
export function TdActs({ className, ...props }: ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn(CELL, className)} {...props} />;
}

export const ACTS = "flex flex-nowrap items-center justify-end gap-xs";

export function Lede({
  mark,
  whole,
  children,
}: {
  mark: ReactNode;
  whole?: boolean;
  children: ReactNode;
}) {
  return (
    <span className="flex min-w-0 items-center gap-md">
      <span
        className={cn(
          "flex size-(--size-lede) shrink-0 items-center justify-center",
          "overflow-hidden rounded-control bg-fill text-ink-soft",
        )}
      >
        {mark}
      </span>
      <span className={whole ? "whitespace-nowrap" : "min-w-0 truncate"}>{children}</span>
    </span>
  );
}

export function TableNote({ span, children }: { span: number; children: ReactNode }) {
  return (
    <tr>
      <Td colSpan={span} className="text-ink-soft">
        {children}
      </Td>
    </tr>
  );
}
