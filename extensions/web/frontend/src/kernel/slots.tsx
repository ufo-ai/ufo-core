import {
  IconArrowsDiagonal,
  IconArrowsDiagonalMinimize2,
  IconArticle,
  IconList,
} from "@tabler/icons-react";
import {
  animate,
  motionValue,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  type MotionValue,
} from "motion/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { Banded, Header } from "@/kernel/pane";
import { cn } from "@/lib/cn";
import { GLYPH_STROKE } from "@/lib/glyph";
import { soundEnded, soundMoved } from "@/lib/sound";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";
import { useNarrow } from "@/lib/narrow";

export type SlotKind = "index" | "reading" | "panel";

type Entry = { id: string; kind: SlotKind; title: string | undefined };

/** The track after an act taken in `from` opened `id`: every lane after `from` shuts and `id`
 *  stands at its right. The track is the path the member walked, not a shelf of everything they
 *  have pressed — three documents opened one after another from the same list are three tries at
 *  one question, and the two they left behind are what makes the third hard to read.
 *
 *  `from` undefined is the pane's own list, the root, so an act taken there leaves exactly one lane
 *  standing. A `from` that no longer stands is the root too: a lane that has been shut raised
 *  nothing. A lane already standing further up the path moves rather than doubling — the track is
 *  keyed by id, and the same id twice is two hosts fighting over one record.
 *
 *  Re-opening the lane that already stands to `from`'s right hands back the very array it was
 *  given, not a copy of it. The caller places what comes back, and a fresh array of the same ids
 *  is a new placement: a history entry for a press that changed nothing, and a remount of the
 *  record the member is in the middle of reading.
 *
 *  A press from the lane at the far end grows the track, so it stops at `TRACK_MAX_SLOTS` like any
 *  other: the track comes back untouched, and the member reaches the record by pressing from a lane
 *  further up the path, which shortens the track before it stands. */
export function opened(track: string[], id: string, from?: string): string[] {
  const at = from === undefined ? -1 : track.indexOf(from);
  if (track[at + 1] === id) return track;
  const next = [...track.slice(0, at + 1).filter((held) => held !== id), id];
  return next.length > TRACK_MAX_SLOTS ? track : next;
}

/** The track after a deliberate open-beside: `id` stands at the far end and nothing shuts. This is
 *  how a member holds two unrelated things at once, having asked for it.
 *
 *  Nothing shuts here, so a full track has nowhere to put the lane and comes back untouched. The
 *  press cannot be answered by dropping the lane at the head: that lane is what the path was opened
 *  from, and an act whose whole meaning is that nothing shuts cannot shut the one lane the rest of
 *  the row was reached through. The row says it is full while it is, so the press that does nothing
 *  is a press the member can already see the answer to. */
export function appended(track: string[], id: string): string[] {
  if (track.includes(id) || track.length >= TRACK_MAX_SLOTS) return track;
  return [...track, id];
}

/** The track after `id` is shut, which shuts what was opened from it: a lane reached through
 *  another says nothing once the lane it was reached through is gone. */
export function closed(track: string[], id: string): string[] {
  const at = track.indexOf(id);
  if (at < 0) return track;
  return track.slice(0, at);
}

function lifted(track: string[], id: string, onto: string): string[] {
  const at = track.indexOf(id);
  const to = track.indexOf(onto);
  if (at < 0 || to < 0 || at === to) return track;
  const next = [...track];
  next.splice(at, 1);
  next.splice(to, 0, id);
  return next;
}

const MIDDLE_BUTTON = 1;

/** Whether a press asked for its target to stand beside the track rather than replace the path
 *  under the lane it was raised in. It is the gesture the browser already handed the member for a
 *  second tab — command or control held, or the middle button — so there is nothing here to teach.
 *  A plain press and this one arrive as the same kind of event, so a call site that never reads it
 *  throws away the distinction the member drew with their hand. */
export function beside(event: { metaKey: boolean; ctrlKey: boolean; button?: number }): boolean {
  return event.metaKey || event.ctrlKey || event.button === MIDDLE_BUTTON;
}

type Handoff = { from: string; to: string; rightward: boolean };

type Track = {
  hosted: boolean;
  over: boolean;
  hosts: Map<string, HTMLElement>;
  place: (entry: Entry, after: string | undefined) => void;
  drop: (id: string) => void;
  move: ((id: string, onto: string) => void) | undefined;
  arrivals: MutableRefObject<Set<string>>;
  sought: MutableRefObject<Set<string>>;
  typing: string | undefined;
  handoff: Handoff | undefined;
  alone: boolean;
  watch: (id: string, panel: HTMLElement | null) => void;
  near: (id: string) => boolean;
  shows: (id: string) => void;
  expandable: boolean;
  expanded: string | undefined;
  expand: (id: string | undefined) => void;
};

const TrackContext = createContext<Track>({
  hosted: false,
  over: false,
  hosts: new Map(),
  place: () => {},
  drop: () => {},
  move: undefined,
  arrivals: { current: new Set() },
  sought: { current: new Set() },
  typing: undefined,
  handoff: undefined,
  alone: true,
  watch: () => {},
  near: () => true,
  shows: () => {},
  expandable: false,
  expanded: undefined,
  expand: () => {},
});

export type Seek = { id: string; expansion: "switch" | "restore" };

const NEAREST: ScrollIntoViewOptions = { block: "nearest", inline: "nearest" };

/** Focus that is not also a scroll: a browser scrolling again to what it just focused fights the row's
 *  snap and leaves it resting between two lanes. */
const KEEP: FocusOptions = { preventScroll: true };

const NEAR_MARGIN = "0px 100%";

/** A row of no width has been laid out by nothing — jsdom's, and a row measured before its first layout
 *  — so no lane in it is far. */
function standsNear(port: DOMRect, lane: DOMRect): boolean {
  return (
    port.width === 0 || (lane.right > port.left - port.width && lane.left < port.right + port.width)
  );
}

const FAR = "min-h-0 flex-1 bg-surface";

export const LANE_PRIOR = "[";
export const LANE_NEXT = "]";

const ROW_MARK = "[data-slot=slot-track]";

export const CLAIMED = "[role=listbox],[role=menu],[data-state=open]";

export const DIALOG = "dialog[open],[role=dialog]";

export const TYPING = "input,textarea,select,[contenteditable]:not([contenteditable=false])";

const HereContext = createContext<string | undefined>(undefined);

const LIFTED = "text/plain";

const ROW = "flex min-h-0 gap-px bg-edge";
const TRACK = "relative flex-1 overflow-x-auto max-narrow:snap-x max-narrow:snap-mandatory " + ROW;

const OVER =
  "absolute inset-0 z-10 overflow-hidden border-x border-edge " +
  "max-narrow:overflow-x-auto max-narrow:snap-x max-narrow:snap-mandatory " +
  ROW;

const PAGED =
  "max-narrow:min-w-full max-narrow:basis-full max-narrow:grow-0 max-narrow:shrink-0 " +
  "max-narrow:snap-start";

const WIDTHS: Record<SlotKind, string> = {
  index: "grow-0 shrink-0 basis-(--container-index)",
  reading: "grow shrink basis-0 min-w-(--size-slot-min)",
  panel: "grow-0 shrink basis-(--container-record) min-w-(--size-slot-min)",
};

const SLOT_FLOOR = "--size-slot-min";

const HAIRLINE = 1;

type Fit = { lanes: number; width: number };

function fitting(row: HTMLElement): Fit | null {
  const floor = Number.parseFloat(getComputedStyle(row).getPropertyValue(SLOT_FLOOR));
  const width = row.getBoundingClientRect().width;
  if (!Number.isFinite(floor) || floor <= 0 || width <= 0) return null;
  return { lanes: Math.max(1, Math.floor(width / floor)), width };
}

const REACH = 1;

type Rolling = {
  rolls: boolean;
  step: number;
  lanes: number;
  ids: string[];
  still: boolean;
};

function far(row: Rolling): number {
  return Math.max(0, (row.ids.length - row.lanes) * row.step);
}

/** A spring at a damping ratio of 0.87, which crosses the distance at once and stops on the boundary
 *  rather than passing it and coming back. */
const GLIDE = { type: "spring", stiffness: 300, damping: 30 } as const;

const SETTLE = 120;

const SLOT = "relative flex min-h-0 flex-col bg-surface";

const SPREAD = "lane-share will-change-transform " + PAGED;

const LIT = "group/lane outline-none";

const FOCUS = "block h-1 shrink-0 bg-ring transition-transform";
const FOCUS_OFF = "scale-x-0 duration-200 ease-leave";
const FOCUS_ON = "scale-x-100 duration-200 ease-enter";
const FOCUS_WALKED = "group-focus-visible/lane:scale-x-100";

const FOCUS_LEAVING = "scale-x-0 duration-150 ease-in";
const FOCUS_ARRIVING = "scale-x-100 delay-100 duration-200 ease-out";
const FOCUS_FROM: Record<"left" | "right", string> = {
  left: "origin-left",
  right: "origin-right",
};

function Focus({
  on,
  from,
  alone,
}: {
  on: boolean;
  from: "left" | "right" | undefined;
  alone: boolean;
}) {
  return (
    <span
      aria-hidden
      data-slot="focus"
      data-on={on ? "" : undefined}
      className={cn(
        FOCUS,
        from === undefined
          ? on
            ? FOCUS_ON
            : FOCUS_OFF
          : [on ? FOCUS_ARRIVING : FOCUS_LEAVING, FOCUS_FROM[from]],
        !on && !alone && FOCUS_WALKED,
      )}
    />
  );
}

const BODY = "flex min-h-0 flex-col bg-surface " + WIDTHS.reading + " " + PAGED;

const FULL =
  "m-0 flex min-h-0 items-center bg-surface px-2xl text-small text-ink-soft " +
  WIDTHS.index +
  " " +
  PAGED;

const FULL_NOTE = "This screen holds " + TRACK_MAX_SLOTS + " slots. Close one to open another.";

const GLYPHS: Record<SlotKind, typeof IconList | null> = {
  index: IconList,
  reading: IconArticle,
  panel: null,
};

function inOrder(entries: Entry[], opens: string[]): Entry[] {
  const held = new Map(entries.map((entry) => [entry.id, entry]));
  const named: Entry[] = [];
  for (const id of opens) {
    const entry = held.get(id);
    if (entry && !named.includes(entry)) named.push(entry);
  }
  if (named.length < 2) return entries;
  const wanted = new Set(named);
  let next = 0;
  const standing = entries.map((entry) => (wanted.has(entry) ? named[next++] : entry));
  return standing.every((entry, at) => entry === entries[at]) ? entries : standing;
}

/** Lanes fill through portals into one always-mounted `display: contents` wrapper: a wrapper that
 *  appeared would remount the list. The track measures itself, since an observer's first batch is late. */
export function SlotTrack({
  over = false,
  opens,
  onMove,
  seek,
  onActive,
  children,
}: {
  over?: boolean;
  seek?: Seek;
  onActive?: (id: string | undefined) => void;
  children: ReactNode;
} & (
  | { opens: string[]; onMove: (opens: string[]) => void }
  | { opens?: undefined; onMove?: undefined }
)) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [hosts, setHosts] = useState<Map<string, HTMLElement>>(new Map());
  const place = useCallback(
    (entry: Entry, after: string | undefined) =>
      setEntries((held) => {
        const at = held.findIndex((slot) => slot.id === entry.id);
        if (at < 0) {
          const parent = held.findIndex((slot) => slot.id === after);
          const next = [...held];
          next.splice(parent < 0 ? held.length : parent + 1, 0, entry);
          return next;
        }
        if (held[at].kind === entry.kind && held[at].title === entry.title) return held;
        const next = [...held];
        next[at] = entry;
        return next;
      }),
    [],
  );
  const drop = useCallback((id: string) => {
    setEntries((held) => held.filter((slot) => slot.id !== id));
    setActive((held) => (held === id ? undefined : held));
  }, []);
  const move = useMemo(
    () =>
      opens === undefined || onMove === undefined || opens.length < 2
        ? undefined
        : (id: string, onto: string) => {
            const next = lifted(opens, id, onto);
            if (next !== opens) onMove(next);
          },
    [opens, onMove],
  );
  const [expanded, setExpanded] = useState<string | undefined>(undefined);
  const expand = useCallback((id: string | undefined) => setExpanded(id), []);
  const hold = useCallback(
    (id: string, host: HTMLElement | null) =>
      setHosts((held) => {
        const next = new Map(held);
        if (host) next.set(id, host);
        else next.delete(id);
        return next;
      }),
    [],
  );
  const arrivals = useRef<Set<string>>(new Set());
  const sought = useRef<Set<string>>(new Set());
  const prior = useRef<Set<string> | null>(null);
  const panels = useRef<Map<string, HTMLElement>>(new Map());
  const named = useRef<Map<Element, string>>(new Map());
  const order = useRef<string[]>([]);
  const alone = useRef<string | undefined>(undefined);
  const watcher = useRef<IntersectionObserver | null>(null);
  const [row, setRow] = useState<HTMLElement | null>(null);
  const narrow = useNarrow();
  const [fit, setFit] = useState<Fit | null>(null);
  const [nearby, setNearby] = useState<ReadonlySet<string>>(() => new Set());
  const culls = typeof IntersectionObserver !== "undefined";
  useLayoutEffect(() => {
    if (!culls || !row || (over && rolling.current.rolls)) return;
    const watching = new IntersectionObserver(
      (seen) =>
        setNearby((held) => {
          const next = new Set(held);
          for (const entry of seen) {
            const id = named.current.get(entry.target);
            if (id === undefined) continue;
            if (entry.isIntersecting) next.add(id);
            else next.delete(id);
          }
          return next.size === held.size && [...next].every((id) => held.has(id)) ? held : next;
        }),
      { root: row, rootMargin: NEAR_MARGIN },
    );
    const port = row.getBoundingClientRect();
    const seen = new Set<string>();
    for (const [id, panel] of panels.current) {
      watching.observe(panel);
      if (standsNear(port, panel.getBoundingClientRect())) seen.add(id);
    }
    watcher.current = watching;
    setNearby(seen);
    return () => {
      watching.disconnect();
      watcher.current = null;
    };
  }, [culls, row, over, narrow, fit, entries.length]);
  const watch = useCallback(
    (id: string, panel: HTMLElement | null) => {
      if (panel) {
        panels.current.set(id, panel);
        named.current.set(panel, id);
        watcher.current?.observe(panel);
        const port = watcher.current && row ? row.getBoundingClientRect() : undefined;
        if (port && standsNear(port, panel.getBoundingClientRect()))
          setNearby((held) => (held.has(id) ? held : new Set(held).add(id)));
        return;
      }
      const stood = panels.current.get(id);
      if (!stood) return;
      watcher.current?.unobserve(stood);
      panels.current.delete(id);
      named.current.delete(stood);
      setNearby((held) => {
        if (!held.has(id)) return held;
        const next = new Set(held);
        next.delete(id);
        return next;
      });
    },
    [row],
  );
  const [cursor, setCursor] = useState<{ typing: string | undefined; from: string | undefined }>({
    typing: undefined,
    from: undefined,
  });
  const typing = cursor.typing;
  const setTyping = useCallback(
    (next: string | undefined) =>
      setCursor((held) => (held.typing === next ? held : { typing: next, from: held.typing })),
    [],
  );
  const [active, setActive] = useState<string | undefined>(undefined);
  const holding = useCallback((held: EventTarget | null) => {
    if (!(held instanceof HTMLElement)) return undefined;
    for (const [id, panel] of panels.current) if (panel.contains(held)) return id;
    return undefined;
  }, []);
  const writing = useCallback(
    (held: EventTarget | null) => {
      if (!(held instanceof HTMLElement)) return undefined;
      if (!held.closest(TYPING) && !held.isContentEditable) return undefined;
      return holding(held);
    },
    [holding],
  );
  useEffect(() => {
    if (!row) return;
    const entered = (event: FocusEvent) => {
      setTyping(writing(event.target));
      const stood = holding(event.target);
      if (stood !== undefined) setActive(stood);
    };
    const left = (event: FocusEvent) => setTyping(writing(event.relatedTarget));
    const pressed = (event: PointerEvent) => {
      const stood = holding(event.target);
      if (stood !== undefined) setActive(stood);
    };
    row.addEventListener("focusin", entered);
    row.addEventListener("focusout", left);
    row.addEventListener("pointerdown", pressed);
    return () => {
      row.removeEventListener("focusin", entered);
      row.removeEventListener("focusout", left);
      row.removeEventListener("pointerdown", pressed);
    };
  }, [row, writing, holding]);
  useLayoutEffect(() => {
    if (!over || !row || typeof ResizeObserver === "undefined") return;
    const sizes = new ResizeObserver(() => setFit(fitting(row)));
    sizes.observe(row);
    setFit(fitting(row));
    return () => sizes.disconnect();
  }, [over, row]);
  const offset = useMotionValue(0);
  const still = useReducedMotion();
  const rolling = useRef<Rolling>({ rolls: false, step: 0, lanes: 0, ids: [], still: false });
  const aimed = useRef(0);
  const spots = useRef<Map<string, MotionValue<number>>>(new Map());
  const seated = useRef<Map<string, number>>(new Map());
  const [ticking, setTicking] = useState<ReadonlySet<string>>(() => new Set());
  const paint = useCallback(() => {
    if (!over) return;
    const { step, lanes, ids } = rolling.current;
    const drawn = new Set<string>();
    for (const [at, id] of ids.entries()) {
      const panel = panels.current.get(id);
      if (step === 0) {
        if (panel) panel.style.transform = "";
        drawn.add(id);
        continue;
      }
      const spot = spots.current.get(id);
      const x = (spot === undefined ? at * step : spot.get()) - offset.get();
      if (panel) panel.style.transform = "translateX(" + Math.round((x - at * step) * 100) / 100 + "px)";
      if (x >= -REACH * step && x <= (lanes + REACH) * step) drawn.add(id);
    }
    if (offset.isAnimating() || [...spots.current.values()].some((spot) => spot.isAnimating()))
      return;
    setTicking((was) =>
      was.size === drawn.size && [...drawn].every((id) => was.has(id)) ? was : drawn,
    );
  }, [offset, over]);
  const settle = useCallback(
    (flight: unknown) => {
      void Promise.resolve(flight).then(paint);
    },
    [paint],
  );
  useMotionValueEvent(offset, "change", paint);
  const paced = useRef(0);
  useLayoutEffect(() => {
    if (!over) return;
    const { rolls, step, ids, still: fixed } = rolling.current;
    const was = paced.current;
    const resized = was !== step;
    paced.current = step;
    const churned = ids.length !== spots.current.size || ids.some((id) => !spots.current.has(id));
    const arriving = churned && !fixed && step > 0 && (spots.current.size > 0 || ids.length === 1);
    if (!rolls) {
      aimed.current = 0;
      if (offset.get() !== 0) offset.jump(0);
    } else {
      if (resized && was > 0) aimed.current = Math.round(aimed.current / was) * step;
      aimed.current = Math.min(Math.max(aimed.current, 0), far(rolling.current));
      if (aimed.current !== offset.get()) offset.jump(aimed.current);
    }
    const held = new Set(ids);
    for (const [id, spot] of spots.current) {
      if (held.has(id)) continue;
      spot.destroy();
      spots.current.delete(id);
      seated.current.delete(id);
    }
    for (const [at, id] of ids.entries()) {
      const to = at * step;
      const spot = spots.current.get(id);
      if (spot === undefined) {
        const made = motionValue(arriving ? to - step : to);
        made.on("change", paint);
        spots.current.set(id, made);
        seated.current.set(id, to);
        if (arriving) settle(animate(made, to, GLIDE));
        continue;
      }
      if (resized && !churned) {
        spot.jump(to);
        seated.current.set(id, to);
        continue;
      }
      if (seated.current.get(id) === to) continue;
      seated.current.set(id, to);
      if (fixed) spot.jump(to);
      else settle(animate(spot, to, GLIDE));
    }
    paint();
  });
  useEffect(
    () => () => {
      for (const spot of spots.current.values()) spot.destroy();
      spots.current.clear();
      seated.current.clear();
    },
    [],
  );
  const glide = useCallback(
    (to: number) => {
      const held = Math.min(Math.max(to, 0), far(rolling.current));
      if (held === aimed.current) return;
      aimed.current = held;
      if (rolling.current.still) offset.jump(held);
      else settle(animate(offset, held, GLIDE));
    },
    [offset, settle],
  );
  const carry = useCallback(
    (at: number) => {
      const { step, lanes } = rolling.current;
      if (step <= 0) return;
      const first = Math.round(aimed.current / step);
      if (at < first) glide(at * step);
      else if (at > first + lanes - 1) glide((at - lanes + 1) * step);
    },
    [glide],
  );
  const shows = useCallback(
    (id: string) => {
      const { rolls, step, ids } = rolling.current;
      if (!rolls) {
        panels.current.get(id)?.scrollIntoView(NEAREST);
        return;
      }
      const at = ids.indexOf(id);
      if (at >= 0) glide(at * step);
    },
    [glide],
  );
  const standing = opens === undefined ? entries : inOrder(entries, opens);
  const ids = standing.map((entry) => entry.id);
  const focused =
    expanded !== undefined && ids.length > 1 && ids.includes(expanded) ? expanded : undefined;
  const near = useCallback(
    (id: string) =>
      focused !== undefined
        ? id === focused
        : over && rolling.current.rolls
          ? ticking.has(id)
          : !culls || nearby.has(id),
    [focused, over, ticking, culls, nearby, narrow, fit],
  );
  useEffect(() => {
    if (!seek) return;
    setActive(seek.id);
    setExpanded((held) =>
      seek.expansion === "restore" || held === undefined ? undefined : seek.id,
    );
    if (!panels.current.has(seek.id)) {
      sought.current.add(seek.id);
      return;
    }
    shows(seek.id);
  }, [seek, shows]);
  useEffect(() => {
    if (!row) return;
    const rotate = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey) return;
      const way = event.key === LANE_NEXT ? 1 : event.key === LANE_PRIOR ? -1 : 0;
      const ids = order.current;
      if (way === 0 || ids.length < 2) return;
      const cursor = document.activeElement;
      if (cursor?.closest(TYPING) || (cursor instanceof HTMLElement && cursor.isContentEditable))
        return;
      if (cursor?.closest(CLAIMED) || document.querySelector(DIALOG)) return;
      const nearest = cursor?.closest(ROW_MARK) ?? null;
      const answers = nearest === null ? !row.parentElement?.closest(ROW_MARK) : nearest === row;
      if (!answers) return;
      const wide = alone.current === undefined ? -1 : ids.indexOf(alone.current);
      const stood = ids.findIndex((id) => panels.current.get(id)?.contains(cursor));
      const port = row.getBoundingClientRect();
      const seen = ids.filter((id) => {
        const lane = panels.current.get(id)?.getBoundingClientRect();
        return lane !== undefined && lane.right > port.left && lane.left < port.right;
      });
      const edge = seen.length === 0 ? 0 : ids.indexOf(way > 0 ? seen[0] : seen[seen.length - 1]);
      const at = (wide >= 0 ? wide : stood < 0 ? edge : stood) + way;
      event.preventDefault();
      if (at < 0 || at >= ids.length) {
        soundEnded();
        return;
      }
      if (wide >= 0) {
        expand(ids[at]);
        setActive(ids[at]);
        soundMoved();
        return;
      }
      const panel = panels.current.get(ids[at]);
      if (!panel) return;
      panel.focus(KEEP);
      if (over) carry(at);
      else panel.scrollIntoView(NEAREST);
      soundMoved();
    };
    document.addEventListener("keydown", rotate);
    return () => document.removeEventListener("keydown", rotate);
  }, [row, over, carry, expand]);
  useEffect(() => {
    if (!row || !over) return;
    let quiet: ReturnType<typeof setTimeout> | undefined;
    const carries = (event: WheelEvent) => {
      if (!rolling.current.rolls || Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
      event.preventDefault();
      aimed.current = Math.min(Math.max(offset.get() + event.deltaX, 0), far(rolling.current));
      offset.jump(aimed.current);
      clearTimeout(quiet);
      quiet = setTimeout(() => {
        const { step } = rolling.current;
        glide(Math.round(aimed.current / step) * step);
      }, SETTLE);
    };
    row.addEventListener("wheel", carries, { passive: false });
    return () => {
      row.removeEventListener("wheel", carries);
      clearTimeout(quiet);
    };
  }, [row, over, offset, glide]);
  useEffect(() => {
    if (expanded !== undefined && focused === undefined) setExpanded(undefined);
  }, [expanded, focused]);
  const from = cursor.from;
  const rightward =
    from !== undefined && typing !== undefined && ids.indexOf(typing) > ids.indexOf(from);
  const handoff = useMemo(
    () =>
      from === undefined || typing === undefined || from === typing
        ? undefined
        : { from, to: typing, rightward },
    [from, typing, rightward],
  );
  const track = useMemo(
    () => ({
      hosted: true,
      over,
      hosts,
      place,
      drop,
      move: focused === undefined ? move : undefined,
      arrivals,
      sought,
      typing,
      handoff,
      alone: ids.length === 1,
      watch,
      near,
      shows,
      expandable: over && ids.length > 1,
      expanded: focused,
      expand,
    }),
    [over, hosts, place, drop, move, typing, handoff, watch, near, shows, ids.length, focused, expand],
  );
  if (prior.current !== null && (prior.current.size > 0 || ids.length === 1)) {
    for (const id of ids) if (!prior.current.has(id)) arrivals.current.add(id);
  }
  const marked = active !== undefined && ids.includes(active) ? active : undefined;
  useEffect(() => {
    prior.current = new Set(ids);
    order.current = ids;
    alone.current = focused;
  });
  useEffect(() => {
    onActive?.(marked);
  }, [marked, onActive]);
  useEffect(() => () => onActive?.(undefined), [onActive]);
  const rolls = focused === undefined && over && !narrow && fit !== null && ids.length > fit.lanes;
  const share =
    focused === undefined
      ? Math.max(1, fit === null ? ids.length : Math.min(ids.length, fit.lanes))
      : 1;
  rolling.current = {
    rolls,
    step: fit === null ? 0 : (fit.width - (share - 1) * HAIRLINE) / share + HAIRLINE,
    lanes: fit?.lanes ?? 0,
    ids: focused === undefined ? ids : [focused],
    still: still === true,
  };
  const shown = standing.length > 0;
  const lanes = standing.map((entry) => <SlotHost key={entry.id} id={entry.id} hold={hold} />);
  const full = shown && !rolls && opens !== undefined && opens.length >= TRACK_MAX_SLOTS;
  return (
    <TrackContext.Provider value={track}>
      {over ? (
        <>
          {children}
          <div
            ref={setRow}
            data-slot="slot-track"
            className={shown ? OVER : "contents"}
            style={{ "--lane-share": String(share) } as CSSProperties}
          >
            {lanes}
            {full ? <p className={FULL}>{FULL_NOTE}</p> : null}
          </div>
        </>
      ) : (
        <div
          ref={setRow}
          data-slot="slot-track"
          className={shown ? TRACK : "contents"}
        >
          <div className={shown ? BODY : "contents"}>{children}</div>
          {lanes}
          {full ? <p className={FULL}>{FULL_NOTE}</p> : null}
        </div>
      )}
    </TrackContext.Provider>
  );
}

function SlotHost({
  id,
  hold,
}: {
  id: string;
  hold: (id: string, host: HTMLElement | null) => void;
}) {
  const keep = useCallback((host: HTMLElement | null) => hold(id, host), [id, hold]);
  return <div ref={keep} className="contents" />;
}

/** It waits for its own host rather than standing inline for a frame: an element that moves into a
 *  portal is a different child there, so React would tear the node down and build it again. */
export function useSlot(
  node: ReactNode | null,
  slot: {
    id: string;
    kind?: SlotKind;
    title?: string;
    glyph?: ReactNode;
    tone?: string;
    fixed?: boolean;
    acts?: ReactNode;
    describes?: string;
    crumb?: Crumb;
    onClose?: () => void;
    onBack?: () => void;
  },
): ReactNode {
  const {
    hosted,
    over,
    hosts,
    place,
    drop,
    move,
    arrivals,
    sought,
    typing,
    handoff,
    alone,
    watch,
    near,
    shows,
    expandable,
    expanded,
    expand,
  } = useContext(TrackContext);
  const here = useContext(HereContext);
  const {
    id,
    kind = "reading",
    title,
    glyph,
    tone,
    fixed = false,
    acts,
    describes,
    crumb,
    onClose,
    onBack,
  } = slot;
  const on = node !== null;
  const held = useRef<HTMLElement | null>(null);
  const host = hosts.get(id);

  useEffect(() => {
    if (!on) return;
    return () => drop(id);
  }, [on, id, drop]);

  useEffect(() => {
    if (!on) return;
    place({ id, kind, title }, here);
  }, [on, id, kind, title, here, place]);

  const arrived = useRef(false);
  const takes = on && host !== undefined && (onClose !== undefined || onBack !== undefined);
  useEffect(() => {
    if (!takes) return;
    const before = document.activeElement;
    const panel = held.current;
    if (arrived.current && !panel?.contains(document.activeElement)) panel?.focus(KEEP);
    return () => {
      if (!(before instanceof HTMLElement) || !document.body.contains(before)) return;
      const active = document.activeElement;
      const claimed =
        active instanceof HTMLElement && active !== document.body && !panel?.contains(active);
      if (claimed) return;
      before.focus();
    };
  }, [takes]);

  const land = useCallback(
    (panel: HTMLElement | null) => {
      held.current = panel;
      watch(id, panel);
      if (!panel) return;
      const arriving = arrivals.current.delete(id);
      const asked = sought.current.delete(id);
      if (!arriving && !asked) return;
      if (arriving) arrived.current = true;
      shows(id);
    },
    [arrivals, sought, watch, shows, id],
  );

  if (!on) return null;
  if (!hosted) return node;
  if (!host) return null;
  const Glyph = GLYPHS[kind];
  const lit = !alone && typing === id;
  const sweep =
    handoff === undefined
      ? undefined
      : handoff.to === id
        ? handoff.rightward
          ? "left"
          : "right"
        : handoff.from === id
          ? handoff.rightward
            ? "right"
            : "left"
          : undefined;
  const hidden = expanded !== undefined && expanded !== id;
  const expandedHere = expanded === id;
  const expandAct = expandable ? (
    <Button
      variant="mark"
      size="glyph"
      aria-label={expandedHere ? "Show all lanes" : "Expand " + (title ?? "lane")}
      onClick={() => expand(expandedHere ? undefined : id)}
    >
      {expandedHere ? (
        <IconArrowsDiagonalMinimize2 aria-hidden stroke={GLYPH_STROKE} />
      ) : (
        <IconArrowsDiagonal aria-hidden stroke={GLYPH_STROKE} />
      )}
    </Button>
  ) : null;
  return createPortal(
    <Banded value={false}>
      <HereContext.Provider value={id}>
        <section
          ref={land}
          aria-label={title}
          aria-describedby={describes}
          hidden={hidden}
          tabIndex={-1}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || event.defaultPrevented) return;
            const from = event.target;
            if (!(from instanceof HTMLElement)) return;
            if (!from.closest(TYPING) && !from.isContentEditable) return;
            event.stopPropagation();
            event.preventDefault();
            event.currentTarget.focus(KEEP);
          }}
          onDragOver={move && ((event) => event.preventDefault())}
          onDrop={
            move &&
            ((event) => {
              event.preventDefault();
              move(event.dataTransfer.getData(LIFTED), id);
            })
          }
          data-lit={lit ? "" : undefined}
          className={cn(
            SLOT,
            over ? SPREAD : [WIDTHS[kind], PAGED],
            LIT,
            hidden && "hidden",
            tone,
          )}
          style={{ "--pane-acts-inset": "0px" } as CSSProperties}
        >
          <Focus on={lit} from={sweep} alone={alone} />
          <Header
            pinned
            ruled
            heading={2}
            {...(glyph !== undefined ? { glyph } : Glyph ? { glyph: <Glyph aria-hidden /> } : {})}
            crumb={crumb}
            title={title}
            acts={
              acts === undefined && expandAct === null ? undefined : (
                <span
                  className="contents"
                  onDragStart={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                  }}
                >
                  {acts}
                  {expandAct}
                </span>
              )
            }
            onClose={expandedHere ? undefined : onClose}
            closes={title}
            onBack={onBack}
            onLift={
              move && !fixed ? (event) => event.dataTransfer.setData(LIFTED, id) : undefined
            }
            onDoubleClick={
              expandable
                ? (event) => {
                    const target = event.target;
                    if (target instanceof Element && target.closest("button,a")) return;
                    expand(expandedHere ? undefined : id);
                  }
                : undefined
            }
          />
          {near(id) ? (
            <div className="flex min-h-0 flex-1 flex-col">{node}</div>
          ) : (
            <div className={FAR} />
          )}
        </section>
      </HereContext.Provider>
    </Banded>,
    host,
  );
}
