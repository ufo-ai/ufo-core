import { IconX } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** The measure a conversation is read at, centred in whatever width the shell leaves. A transcript
 *  is prose, so it is held to a line length the eye can return along; a screen of records is not,
 *  and takes `Page` instead. */
export const COLUMN = "mx-auto w-full max-w-page";

/** The pane a destination draws in. It runs the full width the shell leaves, because the top bar's
 *  rule separates the page's header from its body and a rule that stops two thirds of the way
 *  across states a boundary the surface does not have. */
export function Pane({ className, ...props }: ComponentProps<"main">) {
  return <main {...props} className={cn("flex min-h-0 min-w-0 flex-col", className)} />;
}

/** Every screen is this: one scrolling column, a gutter either side, and a stack of bands one gap
 *  apart. The page owns that rhythm rather than each band carrying its own margins — a band that
 *  spaced itself would add to the gap instead of sitting in it, so the distance between a title and
 *  its records would be a sum of whatever the two happened to declare. The measure is what the
 *  shell leaves rather than a fixed column, because the record opened beside a list is what decides
 *  how much width a list has, and a list that stayed narrow while its pane grew would leave the act
 *  on each row stranded in the middle of the screen. */
export function Page({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      {...props}
      className={cn(
        BANDS,
        "min-h-0 min-w-0 flex-1 overflow-y-auto scrollbar-gutter-stable",
        "px-(--size-page-gutter) py-(--size-page-top) max-narrow:px-2xl",
        className,
      )}
    />
  );
}

/** The one gap a stack of bands is set at, and the only place it is written. A band carries no
 *  margin of its own, so whatever stacks bands has to state this — `Page` for a screen, the tab
 *  panel for the views a strip switches between, the record drawer for a record's groups. A
 *  container that stacks bands and forgets it renders them flush, which is a fault the bands
 *  themselves cannot see. */
export const BANDS = "flex flex-col gap-6xl";

/** The band a page is headed by: what the page is on the left, and on the right the acts that reach
 *  the whole of it — the search over every record and the one that makes another. What narrows the
 *  records to a family stays with the records, so the head of the page and the head of the table
 *  each carry the controls that answer to them. The title takes the body face rather than the
 *  display one: it stands on a line with two controls, and a serif set beside a pill reads as a
 *  masthead over the page instead of as the first item in a row. */
export function PageHeader({
  title,
  search,
  action,
}: {
  /** Absent where the shell above already named the page — the band then carries the acts alone,
   *  rather than a second heading saying the word the member just pressed. */
  title?: string;
  search?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-(--size-control) shrink-0 items-center gap-sm">
      {title ? (
        <h1 className="m-0 flex-1 truncate font-sans text-title font-medium">{title}</h1>
      ) : (
        <span className="flex-1" />
      )}
      {search}
      {action}
    </div>
  );
}

/** The band that narrows the records: which family of them to show, on the page's own left edge and
 *  a band clear of both the title above and the records below. The order they are in is not here —
 *  it sits on the head of the column it orders, where the member is already pointing. */
export function PageToolbar({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-(--size-control) shrink-0 items-center gap-sm">{children}</div>
  );
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
        "flex min-h-0 min-w-0 flex-col gap-(--size-record-gutter)",
        "border-l border-edge-soft py-2xl",
        "max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:border-l-0",
        "max-narrow:bg-surface",
      )}
    >
      <header className="flex h-(--size-control) shrink-0 items-center gap-md px-(--size-record-gutter)">
        <h2 className="m-0 flex-1 truncate font-sans text-subtitle font-medium">{title}</h2>
        <Button size="icon" aria-label="Close" onClick={onClose}>
          <IconX className="size-icon" aria-hidden />
        </Button>
      </header>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-(--size-record-gutter)",
          "overflow-y-auto scrollbar-gutter-stable px-(--size-record-gutter)",
        )}
      >
        {children}
      </div>
    </aside>
  );
}
