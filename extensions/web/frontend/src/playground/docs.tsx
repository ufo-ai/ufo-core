import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/** How wide one state needs to read. A bubble says nothing at a button's width, and fifteen buttons
 *  at a composer's width are a column nobody can scan. */
export type Spread = "tile" | "wide" | "full";

const SPREAD: Record<Spread, string> = {
  tile: "grid-cols-[repeat(auto-fill,minmax(var(--container-choice),1fr))]",
  wide: "grid-cols-[repeat(auto-fill,minmax(var(--container-card),1fr))]",
  full: "grid-cols-1",
};

const PROP_CHIP = "rounded-control bg-fill px-2xs py-hair font-mono text-small text-ink";

export function DocPage({
  title,
  modules,
  description,
  children,
}: {
  title: string;
  modules: string[];
  description: string;
  children: ReactNode;
}) {
  return (
    <article className="flex min-w-0 flex-col gap-8xl">
      <header className="flex min-w-0 flex-col gap-2xl">
        <div className="flex min-w-0 flex-col gap-sm">
          <h1 className="m-0 text-title font-strong text-ink">{title}</h1>
          <p className="m-0 max-w-hint text-body leading-reading text-ink-soft">{description}</p>
        </div>
        <ul className="m-0 flex list-none flex-wrap gap-2xs p-0">
          {modules.map((module) => (
            <li
              key={module}
              className="rounded-control bg-fill px-sm py-hair font-mono text-mono text-ink-soft"
            >
              {module.includes("/") ? "src/" + module : "src/components/ui/" + module}
            </li>
          ))}
        </ul>
      </header>
      <div className="flex min-w-0 flex-col gap-8xl">{children}</div>
    </article>
  );
}

export function DocSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-2xl">
      <header className="flex min-w-0 flex-col gap-2xs border-b border-edge pb-sm">
        <h2 className="m-0 text-subtitle font-strong text-ink">{title}</h2>
        {description ? (
          <p className="m-0 max-w-hint text-label leading-chrome text-ink-soft">{description}</p>
        ) : null}
      </header>
      {children}
    </section>
  );
}

export function States({ spread, children }: { spread: Spread; children: ReactNode }) {
  return <div className={"grid min-w-0 gap-2xl " + SPREAD[spread]}>{children}</div>;
}

/** A one-shot animation has finished before a reader reaches the page, so a state that is only a
 *  motion needs replaying to be a state at all. Remounting the subtree is what restarts it. */
export function State({
  title,
  replay = false,
  children,
}: {
  title: string;
  replay?: boolean;
  children: ReactNode;
}) {
  const [run, again] = useState(0);
  return (
    <figure className="m-0 flex min-w-0 flex-col rounded-card border border-edge bg-raised">
      <div className="relative flex min-h-(--size-tile) min-w-0 items-center p-2xl">
        <div key={run} className="min-w-0 flex-1">
          {children}
        </div>
        {replay ? (
          <span className="absolute end-sm top-sm">
            <Button variant="outline" size="bar" onClick={() => again((shown) => shown + 1)}>
              Play again
            </Button>
          </span>
        ) : null}
      </div>
      <figcaption className="min-w-0 border-t border-edge px-2xl py-lg">
        <span className="font-mono text-label font-medium wrap-anywhere text-ink">{title}</span>
      </figcaption>
    </figure>
  );
}

export type PropRow = { name: string; type: string; fallback?: string; description: string };

export function PropsTable({ of, rows }: { of: string; rows: PropRow[] }) {
  return (
    <div className="flex min-w-0 max-w-page flex-col gap-sm">
      <h3 className="m-0 font-mono text-ui font-medium text-ink">{of}</h3>
      <div className="min-w-0 overflow-x-auto">
        <table className="w-full min-w-page border-collapse text-label">
          <thead>
            <tr className="border-b border-edge text-ink-quiet">
              <th className="py-sm pe-2xl text-start text-small font-medium">Prop</th>
              <th className="py-sm pe-2xl text-start text-small font-medium">Type</th>
              <th className="py-sm pe-2xl text-start text-small font-medium">Default</th>
              <th className="py-sm text-start text-small font-medium">What it decides</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name} className="border-b border-edge align-top">
                <td className="py-lg pe-2xl whitespace-nowrap">
                  <code className={PROP_CHIP}>{row.name}</code>
                </td>
                <td className="py-lg pe-2xl font-mono text-small text-ink-soft">{row.type}</td>
                <td className="py-lg pe-2xl font-mono text-small whitespace-nowrap text-ink-quiet">
                  {row.fallback ?? "—"}
                </td>
                <td className="py-lg leading-chrome text-ink-soft">{row.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
