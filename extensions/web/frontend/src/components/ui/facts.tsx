import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Fact = { label: string; value: ReactNode; block?: boolean };

/** `action` is what the group does to itself, standing at the far end of its own name: the way into
 *  editing what the group states, and the act that commits it once open. It sits on the name's line
 *  rather than under the rows, so a group the member only reads carries nothing extra. */
export function Group({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-px">
      <p className="m-0 flex h-(--size-record) items-center gap-md border-b border-edge text-label font-medium">
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {action}
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
 *  fact under it down a drawer the member is scanning. The row also clips: a value taller than the
 *  pitch would otherwise paint outside its own band and over the facts above and below it.
 *
 *  A `block` fact is the one value that does not fit that shape — prompt text, a paragraph, an
 *  identifier longer than the value column. It stands under its label rather than beside it, set
 *  left and wrapped, and the row grows to hold all of it, because a fact the member came to read is
 *  worth the pitch it breaks. */
export function Facts({ rows }: { rows: Fact[] }) {
  return (
    <dl data-slot="facts" className="m-0 flex flex-col gap-px">
      {rows.map((row) =>
        row.block ? (
          <div
            key={row.label}
            className={cn(
              "flex min-h-(--size-record) flex-col justify-center gap-2xs py-sm",
              "border-b border-edge text-label",
            )}
          >
            <dt className="text-ink-soft">{row.label}</dt>
            <dd className="m-0 whitespace-pre-wrap wrap-anywhere">{row.value}</dd>
          </div>
        ) : (
          <div
            key={row.label}
            className={cn(
              "flex h-(--size-record) items-center justify-between gap-2xl overflow-hidden",
              "border-b border-edge text-label",
            )}
          >
            <dt className="shrink-0 truncate text-ink-soft">{row.label}</dt>
            <dd className="m-0 min-w-0 truncate text-right">{row.value}</dd>
          </div>
        ),
      )}
    </dl>
  );
}
