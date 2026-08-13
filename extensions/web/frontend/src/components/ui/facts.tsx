import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Fact = { label: string; value: ReactNode };

/** What a pane states about its own subject: one row a fact, the label soft on the left and the
 *  value in ink against the right edge, each row ruled off the next. It takes the shape the
 *  records take — a column on the section's own ground rather than a card set into it — so a
 *  subject's facts and a listing of subjects read as one surface. Setting the value right puts
 *  every one of them on a second edge the eye runs down, which is what makes a column of facts
 *  scannable where the same facts joined into a sentence are parsed. A value wraps inside its own
 *  half, and is cut with an ellipsis where the panel is narrower than what it says — a fact that
 *  wrapped would push every fact under it down a drawer the member is scanning. */
export function Facts({ rows }: { rows: Fact[] }) {
  return (
    <dl data-slot="facts" className="m-0">
      {rows.map((row) => (
        <div
          key={row.label}
          className={cn(
            "flex min-h-(--size-control) items-center justify-between gap-2xl",
            "border-b border-edge-soft py-2xs",
          )}
        >
          <dt className="shrink-0 truncate text-ink-soft">{row.label}</dt>
          <dd className="m-0 min-w-0 truncate text-right">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
