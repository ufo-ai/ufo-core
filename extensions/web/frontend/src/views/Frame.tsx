import type { ReactNode } from "react";

import mark from "@brand/ufo-mark.svg";
import { cn } from "@/lib/cn";

export function Head({ actions, at, steps }: { actions?: ReactNode; at?: number; steps?: number }) {
  return (
    <div className="grid h-8xl grid-cols-3 items-center px-7xl max-narrow:px-2xl">
      <span
        role="img"
        aria-label="ufo"
        className="block size-(--size-glyph) shrink-0 bg-current"
        style={{ mask: `url(${mark}) center / contain no-repeat` }}
      />
      {at !== undefined && steps !== undefined ? (
        <div
          role="progressbar"
          aria-label="Step"
          aria-valuemin={1}
          aria-valuemax={steps}
          aria-valuenow={at + 1}
          className="flex items-center gap-2xs justify-self-center"
        >
          {Array.from({ length: steps }, (_, index) => (
            <span
              key={index}
              aria-hidden
              className={cn(
                "h-2xs rounded-full",
                index === at ? "w-(--size-step-on) bg-ink" : "w-(--size-step-off) bg-edge",
              )}
            />
          ))}
        </div>
      ) : null}
      <div className="col-start-3 flex items-center justify-self-end gap-sm">{actions}</div>
    </div>
  );
}

export function Frame({
  title,
  note,
  lead,
  actions,
  at,
  steps,
  children,
}: {
  title?: ReactNode;
  note?: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  at?: number;
  steps?: number;
  children: ReactNode;
}) {
  return (
    <main className="grid h-dvh grid-rows-[auto_1fr] overflow-y-auto">
      <Head actions={actions} at={at} steps={steps} />
      <div className="mx-auto flex min-h-0 w-full max-w-section flex-col items-center justify-center gap-6xl px-2xl py-6xl">
        {title ? (
          <div className="flex max-w-form flex-col items-center gap-2xl text-center">
            {lead}
            <div className="flex flex-col gap-sm">
              <h1 className="m-0 text-subtitle font-medium text-ink">{title}</h1>
              {note ? <p className="m-0 text-label text-ink-soft">{note}</p> : null}
            </div>
          </div>
        ) : null}
        {children}
      </div>
    </main>
  );
}
