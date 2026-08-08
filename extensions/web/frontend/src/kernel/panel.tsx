import { useEffect, useState, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PanelState<T> =
  | { phase: "loading" }
  | { phase: "failed"; message: string; status: number }
  | { phase: "ready"; payload: T };

/** A read whose path changes — a filter picked, a page turned, a search submitted — holds the
 *  answer it already has until the next one lands. Dropping to `loading` would unmount the very
 *  controls the member is operating: the tablist is drawn from the payload, so a class toggle
 *  would take the tabs off screen under the cursor and put them back a moment later. Only a first
 *  read, which has nothing to hold, shows the skeleton. */
export function usePanelRead<T>(path: string | null, reloads: number = 0): PanelState<T> {
  const [state, setState] = useState<PanelState<T>>({ phase: "loading" });
  useEffect(() => {
    if (path === null) return;
    const superseded = new AbortController();
    let live = true;
    setState((held) => (held.phase === "ready" ? held : { phase: "loading" }));
    getJson<T>(path, superseded.signal).then((result) => {
      if (!live) return;
      setState(
        result.ok
          ? { phase: "ready", payload: result.payload }
          : { phase: "failed", message: result.message, status: result.status },
      );
    });
    return () => {
      live = false;
      superseded.abort();
    };
  }, [path, reloads]);
  return state;
}

export type PanelShape = "table" | "cards" | "form";

const SKELETON_ROWS = 5;
const SKELETON_CARDS = 4;
const SKELETON_FIELDS = 3;

/** What is coming, drawn at its own size. A centred `Loading…` sits where no record ever will,
 *  then jumps aside as the answer lands; a skeleton holds the card, the column, and the row
 *  rhythm the payload will take, so the page settles instead of rearranging. */
export function PanelSkeleton({ shape }: { shape: PanelShape }) {
  if (shape === "cards")
    return (
      <div className="grid grid-cols-2 gap-lg">
        {Array.from({ length: SKELETON_CARDS }, (_, index) => (
          <div key={index} className="rounded-panel border border-edge bg-surface p-xl">
            <Skeleton className="h-(--size-notice) w-2/5" />
            <Skeleton className="mt-lg h-(--size-notice) w-full" />
            <Skeleton className="mt-sm h-(--size-notice) w-3/5" />
          </div>
        ))}
      </div>
    );
  if (shape === "form")
    return (
      <div className="flex max-w-form flex-col gap-4xl">
        {Array.from({ length: SKELETON_FIELDS }, (_, index) => (
          <div key={index} className="flex flex-col gap-sm">
            <Skeleton className="h-(--size-notice) w-1/4" />
            <Skeleton className="h-(--size-notice) w-full" />
          </div>
        ))}
      </div>
    );
  return (
    <div className="rounded-panel border border-edge bg-surface">
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          className={cn("flex items-center gap-xl px-xl py-lg", index && "border-t border-edge")}
        >
          <Skeleton className="h-(--size-notice) w-full" />
          <Skeleton className="h-(--size-notice) w-1/6" />
        </div>
      ))}
    </div>
  );
}

export function Panel<T>({
  state,
  shape = "table",
  loading,
  failed,
  empty,
  children,
}: {
  state: PanelState<T>;
  shape?: PanelShape;
  loading?: () => ReactNode;
  failed?: (message: string, status: number) => ReactNode;
  empty?: (payload: T) => ReactNode;
  children: (payload: T) => ReactNode;
}) {
  if (state.phase === "loading")
    return loading ? loading() : <PanelSkeleton shape={shape} />;
  if (state.phase === "failed")
    return failed ? failed(state.message, state.status) : <PanelEmpty>{state.message}</PanelEmpty>;
  const nothing = empty?.(state.payload);
  if (nothing) return <PanelEmpty>{nothing}</PanelEmpty>;
  return children(state.payload);
}

function Empty({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("m-auto max-w-empty text-center opacity-(--muted-soft)", className)}>
      {children}
    </div>
  );
}

export function PanelEmpty({ children }: { children: ReactNode }) {
  return <Empty className="my-6xl mx-auto block">{children}</Empty>;
}

/** A section that holds no records yet takes the card its records would have taken — same border,
 *  same left edge, same bottom rhythm as `Table` — so a blank section stacked among filled ones
 *  reads as one more row of the column rather than a note floating between them. Nothing is aligned
 *  to, so the line and its one act sit in the middle of the card. The section heading already names
 *  what is absent, so the card states only what fills it. `PanelEmpty` stays the centred note for a
 *  passing condition: loading, a failed read, a search that matched nothing. */
export function PanelBlank({ body, action }: { body: string; action?: ReactNode }) {
  return (
    <div className="rounded-panel border border-edge bg-surface px-xl py-4xl text-center">
      <p className="m-0 mx-auto max-w-hint opacity-(--muted-soft)">{body}</p>
      {action ? <div className="mt-lg">{action}</div> : null}
    </div>
  );
}

export type NoticeState = { text: string; refused: boolean };

export const QUIET: NoticeState = { text: "", refused: false };

export function outcomeNotice(outcome: { applied: boolean; message: string }): NoticeState {
  return { text: outcome.message, refused: !outcome.applied };
}

export function OutcomeNotice({ state }: { state: NoticeState }) {
  if (!state.text) return null;
  return <Notice tone={state.refused ? "attention" : "quiet"}>{state.text}</Notice>;
}

export function Notice({
  tone = "quiet",
  children,
}: {
  tone?: "quiet" | "attention";
  children: ReactNode;
}) {
  return (
    <div
      role="status"
      className={cn(
        "mt-md min-h-(--size-notice) px-sm py-hair text-ui",
        tone === "attention" && children
          ? "w-fit rounded-sm border border-ink bg-attention [color:var(--color-attention-ink)]"
          : null,
      )}
    >
      {children}
    </div>
  );
}

/** The one owner of vertical rhythm on a settings screen: `gap-lg` between the heading, the bar,
 *  and the records, `mb-6xl` to the next section. Nothing inside carries a bottom margin — a block
 *  that spaced itself would add to the gap rather than sit in it, and a screen of six sections
 *  would drift wider apart with every band it happens to hold. */
export function Section({
  title,
  bar,
  children,
}: {
  title: string;
  bar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-6xl flex w-full max-w-section flex-col gap-lg">
      <h2 className="m-0 text-body font-strong opacity-(--muted-soft)">{title}</h2>
      {bar ? <div className="flex items-stretch gap-sm">{bar}</div> : null}
      {children}
    </section>
  );
}
