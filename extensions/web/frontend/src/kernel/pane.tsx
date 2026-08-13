import { IconX } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** The measure a page is read at, centred in whatever width the shell leaves. `--container-page`
 *  is a section plus the gutters it is read inside, so a section reaches exactly its own
 *  `--container-section` and no screen is wider than the text it holds. Every band under the top
 *  bar takes it, so the title, the records and the notice between them share one pair of margins. */
export const COLUMN = "mx-auto w-full max-w-page";

/** The pane a destination draws in. It runs the full width the shell leaves, because the top bar's
 *  rule separates the page's header from its body and a rule that stops two thirds of the way
 *  across states a boundary the surface does not have. */
export function Pane({ className, ...props }: ComponentProps<"main">) {
  return <main {...props} className={cn("flex min-h-0 min-w-0 flex-col", className)} />;
}

/** A record opened beside the list it came from: a column of the pane rather than a sheet laid
 *  over it, so the list stays readable and closing the record is a press rather than a way back.
 *  Under `--breakpoint-narrow` there is no room for two columns, so it covers the pane instead. */
export function RecordPanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <aside
      aria-label={title}
      className={cn(
        "flex min-h-0 min-w-0 flex-col gap-2xl overflow-y-auto border-l border-edge p-2xl",
        "max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:border-l-0",
        "max-narrow:bg-surface",
      )}
    >
      <header className="flex h-(--size-control) items-center justify-between gap-md">
        <h2 className="m-0 truncate text-title font-strong">{title}</h2>
        <Button size="icon" aria-label="Close" onClick={onClose}>
          <IconX className="size-icon" aria-hidden />
        </Button>
      </header>
      {children}
    </aside>
  );
}

/** The band a page is headed by: what the page is on the left, and on the right the acts that
 *  reach the whole of it — the search over every record and the one that makes another. What
 *  narrows the records to a family stays with the records, so the head of the page and the head
 *  of the table each carry the controls that answer to them. */
export function PageHeader({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="pt-8xl pb-2xl">
      <div className={cn(COLUMN, "flex h-(--size-control) items-center gap-md px-2xl")}>
        {children}
        {aside ? <div className="ml-auto flex items-center gap-sm">{aside}</div> : null}
      </div>
    </div>
  );
}
