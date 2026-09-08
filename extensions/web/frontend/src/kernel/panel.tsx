import { IconLoader2 } from "@tabler/icons-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { aborted, getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PanelState<T> =
  | { phase: "loading" }
  | { phase: "failed"; message: string; status: number }
  | { phase: "ready"; payload: T };

const POLL_MS = 30_000;

/** What a read holds is one path's answer, so another path shows the skeleton: a pane handed one path's
 *  payload under another's frame draws a record nobody asked for. A read that answered keeps its interval. */
export function usePanelRead<T>(
  path: string | null,
  reloads: number = 0,
  everyMs: number = POLL_MS,
): PanelState<T> {
  const [state, setState] = useState<PanelState<T>>({ phase: "loading" });
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  const [pollTick, setPollTick] = useState(0);
  const reading = useRef(false);
  const failed = useRef(false);
  const answered = useRef(false);
  const asked = useRef<string | null>(null);
  useEffect(() => {
    const change = () => {
      const shown = document.visibilityState !== "hidden";
      setVisible(shown);
      if (shown && !reading.current && (!failed.current || answered.current)) {
        setPollTick((tick) => tick + 1);
      }
    };
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  useEffect(() => {
    if (!visible) return;
    const interval = window.setInterval(() => {
      if (!reading.current && (!failed.current || answered.current)) {
        setPollTick((tick) => tick + 1);
      }
    }, everyMs);
    return () => window.clearInterval(interval);
  }, [visible, everyMs]);
  useEffect(() => {
    if (path === null) return;
    const superseded = new AbortController();
    let live = true;
    reading.current = true;
    const again = asked.current === path;
    asked.current = path;
    setState((held) => (held.phase === "ready" && again ? held : { phase: "loading" }));
    getJson<T>(path, superseded.signal)
      .then((result) => {
        if (!live) return;
        failed.current = !result.ok;
        answered.current = answered.current || result.ok;
        setState(
          result.ok
            ? { phase: "ready", payload: result.payload }
            : { phase: "failed", message: result.message, status: result.status },
        );
      })
      /* The cleanup aborts the read it supersedes, and that abort rejects this chain — the effect's own act
         on a read nothing waits for, so it ends here rather than reaching the browser as an error. */
      .catch((error: unknown) => {
        if (!live || aborted(error)) return;
        failed.current = true;
        setState({ phase: "failed", message: "Network error — try again.", status: 0 });
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

export function Loading() {
  return (
    <div
      role="status"
      className="animate-waiting flex min-h-full w-full flex-1 items-center justify-center gap-2xs self-stretch text-ink-soft"
    >
      <IconLoader2 aria-hidden className="size-4 shrink-0 animate-spin motion-reduce:animate-none" />
      <span>Loading…</span>
    </div>
  );
}

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
          className="flex items-center gap-2xl border-b border-edge px-2xl py-md"
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

export function Empty({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("m-auto max-w-empty text-center text-ink-soft", className)}>
      {children}
    </div>
  );
}

export function PanelEmpty({ children }: { children: ReactNode }) {
  return <Empty className="my-7xl mx-auto block">{children}</Empty>;
}

export function PanelBlank({ body, action }: { body: string; action?: ReactNode }) {
  return (
    <div className="rounded-panel border border-edge bg-surface px-xl py-4xl text-center">
      <p className="m-0 mx-auto max-w-hint text-ink-soft">{body}</p>
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
    <section className="flex w-full flex-col gap-6xl">
      {title || note ? (
        <div className="flex w-full items-center gap-2xl">
          <div className="flex min-w-0 flex-1 flex-col gap-2xs">
            {title ? <h2 className="m-0 text-subtitle font-medium">{title}</h2> : null}
            {note ? <p className="m-0 max-w-hint text-label text-ink-quiet">{note}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-sm">{action}</div> : null}
        </div>
      ) : null}
      {bar ? <div className="flex items-stretch gap-sm">{bar}</div> : null}
      <div>{children}</div>
    </section>
  );
}
