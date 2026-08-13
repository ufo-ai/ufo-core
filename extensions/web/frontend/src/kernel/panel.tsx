import { useEffect, useRef, useState, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PanelState<T> =
  | { phase: "loading" }
  | { phase: "failed"; message: string; status: number }
  | { phase: "ready"; payload: T };

const POLL_MS = 30_000;

/** A read holds its answer until the next one lands. The visible pane re-reads at a fixed interval,
 *  while a hidden tab does not poll and a failed read waits for the member's next move. Only a
 *  first read, which has nothing to hold, shows the skeleton. */
export function usePanelRead<T>(path: string | null, reloads: number = 0): PanelState<T> {
  const [state, setState] = useState<PanelState<T>>({ phase: "loading" });
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  const [pollTick, setPollTick] = useState(0);
  const reading = useRef(false);
  const failed = useRef(false);
  useEffect(() => {
    const change = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  useEffect(() => {
    if (!visible) return;
    const interval = window.setInterval(() => {
      if (!reading.current && !failed.current) setPollTick((tick) => tick + 1);
    }, POLL_MS);
    return () => window.clearInterval(interval);
  }, [visible]);
  useEffect(() => {
    if (path === null) return;
    const superseded = new AbortController();
    let live = true;
    reading.current = true;
    setState((held) => (held.phase === "ready" ? held : { phase: "loading" }));
    getJson<T>(path, superseded.signal)
      .then((result) => {
        if (!live) return;
        failed.current = !result.ok;
        setState(
          result.ok
            ? { phase: "ready", payload: result.payload }
            : { phase: "failed", message: result.message, status: result.status },
        );
      })
      .finally(() => {
        if (live) reading.current = false;
      });
    return () => {
      live = false;
      reading.current = false;
      superseded.abort();
    };
  }, [path, reloads, pollTick]);
  return state;
}

export type PanelShape = "table" | "cards" | "form";

const SKELETON_ROWS = 5;
const SKELETON_CARDS = 4;
const SKELETON_FIELDS = 3;

/** What is coming, drawn at its own size. A centred `Loading…` sits where no record ever will,
 *  then jumps aside as the answer lands; a skeleton holds the column and row rhythm the payload
 *  will take, ruled and bounded the way that payload is, so the page settles instead of
 *  rearranging. */
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
    <div>
      <Skeleton className="mx-2xl mb-lg h-(--size-notice) w-1/6" />
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          className="flex items-center gap-2xl border-b border-edge-soft px-2xl py-md"
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
  if (state.phase === "loading") return loading ? loading() : <PanelSkeleton shape={shape} />;
  if (state.phase === "failed")
    return failed ? failed(state.message, state.status) : <PanelEmpty>{state.message}</PanelEmpty>;
  const nothing = empty?.(state.payload);
  if (nothing) return <PanelEmpty>{nothing}</PanelEmpty>;
  return children(state.payload);
}

function Empty({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("m-auto max-w-empty text-center opacity-(--opacity-muted-soft)", className)}>
      {children}
    </div>
  );
}

export function PanelEmpty({ children }: { children: ReactNode }) {
  return <Empty className="my-7xl mx-auto block">{children}</Empty>;
}

/** A section that holds no records yet takes a card on the section's own left edge. A table states
 *  its extent in the rules between its records; a blank section has none, and a centred line in an
 *  unbounded gap reads as an orphan of the heading rather than as the place records will stand.
 *  Nothing is aligned to, so the line and its one act sit in the middle of the card. The section
 *  heading already names
 *  what is absent, so the card states only what fills it. `PanelEmpty` stays the centred note for a
 *  passing condition: loading, a failed read, a search that matched nothing. */
export function PanelBlank({ body, action }: { body: string; action?: ReactNode }) {
  return (
    <div className="rounded-panel border border-edge bg-surface px-xl py-4xl text-center">
      <p className="m-0 mx-auto max-w-hint opacity-(--opacity-muted-soft)">{body}</p>
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

/** The one owner of vertical rhythm on a settings screen. A heading names the block directly
 *  beneath it and its own rule is the join, so a title and the rows it heads read as one object
 *  rather than as two blocks a gap apart. What narrows the records stands `gap-6xl` clear of both,
 *  because a control belongs to neither the name above it nor the row below it, and `mb-5xl`
 *  separates one section from the next. Nothing inside carries a bottom margin — a block that
 *  spaced itself would add to the gap rather than sit in it.
 *
 *  `action` is one act on what the heading names, drawn beside it rather than under it: a fact about
 *  the whole section belongs on the heading line, where `bar` is for the controls that narrow the
 *  records under it. */
export function Section({
  title,
  note,
  bar,
  action,
  children,
}: {
  title?: ReactNode;
  note?: string;
  bar?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-5xl flex w-full max-w-section flex-col">
      {title || note ? (
        <div className="flex flex-col gap-2xs border-b border-edge-soft pb-md">
          {title ? (
            <div className="flex items-baseline gap-md">
              <h2 className="m-0 text-ui font-strong">{title}</h2>
              {action}
            </div>
          ) : null}
          {note ? <p className="m-0 text-ui text-ink-soft">{note}</p> : null}
        </div>
      ) : null}
      {bar ? (
        <div className={cn("flex items-stretch gap-sm", (title || note) && "mt-6xl")}>{bar}</div>
      ) : null}
      <div className={cn(bar && "mt-6xl")}>{children}</div>
    </section>
  );
}
