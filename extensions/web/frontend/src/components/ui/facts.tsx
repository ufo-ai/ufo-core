import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Fact = { label: string; value: ReactNode };

/** What a pane states about its own subject, drawn in the card its records would take: one row a
 *  fact, the label in the weight and width a table gives its column heading. A run of facts joined
 *  into one line is a sentence the member has to parse before they can find the one they came for;
 *  a column of them is scanned. */
export function Facts({ rows }: { rows: Fact[] }) {
  return (
    <dl className="m-0 rounded-panel border border-edge bg-surface">
      {rows.map((row, index) => (
        <div
          key={row.label}
          className={cn(
            "flex flex-wrap items-baseline gap-x-xl px-xl py-lg",
            index && "border-t border-edge-soft",
          )}
        >
          <dt className="w-(--size-fact) shrink-0 text-small font-strong opacity-(--muted)">
            {row.label}
          </dt>
          <dd className="m-0 min-w-0 flex-1">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
