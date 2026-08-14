import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Fact = { label: string; value: ReactNode };

/** The heading over one group in a record's pane, drawn at the pitch of the rows beneath it and
 *  joined to them by its own rule, so a group reads as one ruled column rather than as a title and
 *  a table a gap apart. It is the same height as a fact, because a pane read down one rhythm is
 *  scanned and a pane of headings at their own size is parsed.
 *
 *  The heading stands outside the list it heads: a definition list holds terms and definitions, so
 *  a title dropped among them is markup a reader that builds pairs out of the list has no place
 *  for. That is why this wraps `Facts` rather than being a branch inside it. */
export function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col">
      <p className="m-0 flex h-(--size-record) items-center border-b border-edge-faint text-label">
        {title}
      </p>
      {children}
    </section>
  );
}

/** What a pane states about its own subject: one row a fact, the label soft on the left and the
 *  value in ink against the right edge, each row ruled off the next. It takes the shape the
 *  records take — a column on the section's own ground rather than a card set into it — so a
 *  subject's facts and a listing of subjects read as one surface. Setting the value right puts
 *  every one of them on a second edge the eye runs down, which is what makes a column of facts
 *  scannable where the same facts joined into a sentence are parsed. A row is one pitch tall and
 *  its value is cut with an ellipsis rather than wrapped, because a fact that grew would push every
 *  fact under it down a drawer the member is scanning. */
export function Facts({ rows }: { rows: Fact[] }) {
  return (
    <dl data-slot="facts" className="m-0">
      {rows.map((row) => (
        <div
          key={row.label}
          className={cn(
            "flex h-(--size-record) items-center justify-between gap-2xl",
            "border-b border-edge-faint text-label",
          )}
        >
          <dt className="shrink-0 truncate text-ink-soft">{row.label}</dt>
          <dd className="m-0 min-w-0 truncate text-right">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
