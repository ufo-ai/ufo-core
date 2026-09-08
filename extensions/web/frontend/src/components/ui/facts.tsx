import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Fact = { label: string; value: ReactNode; block?: boolean };

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
