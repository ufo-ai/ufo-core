import { useEffect, useRef, useState, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PanelState<T> =
  | { phase: "loading" }
  | { phase: "failed"; message: string; status: number }
  | { phase: "ready"; payload: T };

const POLL_MS = 30_000;

/** A read holds its answer until the next one lands. What it holds is that one path's answer, so a
 *  read of another path shows the skeleton rather than the answer to the question before it: a pane
 *  handed one path's payload under another path's frame draws a record the member did not ask for.
 *  The visible pane re-reads on its own interval, while a hidden tab does not poll. Only a first
 *  read, which has nothing to hold, shows the skeleton.
 *
 *  A read that has never answered waits for the member's next move once it fails: there is nothing
 *  on the screen for a retry to correct, and a route that refused the first ask will refuse the
 *  next. A read that has answered keeps its interval, because what failed there is a refresh of
 *  something the member is already looking at — the deploy that dropped one poll is over by the
 *  next one, and a pane that gave up would state the workspace had gone quiet.
 *
 *  A tab that is looked at again reads at once rather than serving what it held until the next
 *  tick: the member left to do something the answer depends on, and coming back is the move that
 *  asks for it. That is what `everyMs` is for — a pane waiting on an act taking place off the page
 *  reads at its own rate until the act lands, and states the wait rather than a stale answer. */
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

/** The one line a screen states while it has nothing else, and the only place the words are
 *  written. It reserves its line from the first frame and appears on the theme's threshold, so a
 *  read that answers before then leaves no trace on the way past — the timing is the theme's, and
 *  the same threshold governs every placeholder on the surface. */
export function Waiting() {
  return <span className="animate-waiting">Loading…</span>;
}

/** What is coming, drawn at its own size. A centred sentence sits where no record ever will, then
 *  jumps aside as the answer lands; a skeleton holds the column and row rhythm the payload will
 *  take, ruled and bounded the way that payload is, so the page settles instead of rearranging. */
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

/** Draws a read by its phase: a skeleton while loading, the refusal when failed, the empty
 * sentence when the payload holds nothing, and `children(payload)` once it is ready. */
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

/** A sentence standing where records would: centred, bounded to a measure a line reads at, and in
 *  the register a fact is stated in. Every screen that answers with a sentence draws this one, so a
 *  loading note, a refusal and a screen with nothing on it read alike wherever the member meets
 *  them. `className` is the space around it, which is the page's to set and not the note's. */
export function Empty({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("m-auto max-w-empty text-center text-ink-soft", className)}>
      {children}
    </div>
  );
}

/** The empty sentence at a section's own height: what a section holding no records says. */
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

/** A band of records inside a page, stacked at the page's own rhythm: `Page` sets the gap between
 *  bands, so a section carries no margin of its own — a block that spaced itself would add to that
 *  gap rather than sit in it, and the distance between two sections would become a sum of whatever
 *  bands each happened to hold. It takes the full measure the page leaves for the same reason the
 *  page does: the act on each row sits at one right edge, and a section that stayed narrow while
 *  its pane grew would strand that edge in the middle of the screen.
 *
 *  A heading is drawn only where it names something the pane does not already say. A screen reached
 *  by pressing a tab has been named by that tab, and a second heading repeating the word under it
 *  states there are two things where there is one.
 *
 *  `action` is one act on what the heading names, held at the band's far edge and centred against
 *  the heading and its note together — so a column of bands puts every act on one right edge the eye
 *  runs down, whether or not the band above carried a note. It is one act, where `bar` is for the
 *  controls that narrow the records under it. The heading takes the width the act leaves and the act
 *  never shrinks, so a long heading is what gives way. */
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
